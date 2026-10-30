"""Lower a function with control flow to a stepped circuit (M2).

Control is one step signal ``S`` on the red bus, counting up by one each tick.
Each basic block owns a range of steps:

    base .. e        the block's combinators settle (e - base = its depth)
    e                write gates sample the values to keep
    e + 2            registers latch them; the branch decider fires
    e + 3            S lands on the next block's base

Values used only inside their block are read straight from their combinator.
A value used in another block, and every phi, lives in a bus register, so it is
stable for the whole of every later block. A register is one decider; each
write to it adds a gate decider and an ``x + 0`` that renames the gated value to
the register's signal. The renames all drive one shared green write net, which
every register reads, so a register needs no wiring of its own beyond the bus
and that net.

Branches add a constant to ``S`` (signals on a wire sum), so a jump costs one
decider per target. ``ret`` writes the result register and jumps to the halt
step, where a decider holds ``S`` still.
"""

from __future__ import annotations

from dataclasses import dataclass

from .frontend import Array, Block, Function, Op, Operand, Unsupported, Value
from .lower import PRIVATE_CONSTANT, Lowered, Wire, _Builder, _colors, _depth, _Lowerer, _operand
from .netlist import GREEN, RED, Arithmetic, Cond, Connector, Constant, Decider, Net, Netlist, Out, Sig
from .memory import ADDRESS, ENABLE, VALUE, Memory, Ptr
from .schedule import GATE_TO_LATCH, BlockSchedule, Jump, Load, Register, Schedule, Store, Test, Write
from .signals import EACH
from .sim import Simulator, wrap

STEP = "signal-S"
RESET = "signal-R"
HALT = "<halt>"  # jump target name for the halt step
# Reset holds S at 1, so the program starts at 2: nothing fires while in reset.
FIRST_STEP = 2


@dataclass
class SteppedCompiled:
    function: str
    netlist: Netlist
    params: list[Constant]
    param_signals: list[str]
    result: str
    halt: int
    bus: Connector
    blocks: dict[str, tuple[int, int]]  # block -> (base step, jump step)
    signals: dict[str, str]
    reset: Constant
    schedule: Schedule
    last_ticks: int = 0

    @property
    def latency(self) -> int:
        """Ticks for the shortest path; loops take longer (see ``last_ticks``)."""
        return self.halt

    def run(self, *args: int, max_ticks: int = 1_000_000) -> int:
        if len(args) != len(self.params):
            raise TypeError(f"@{self.function} takes {len(self.params)} arguments")
        for c, sig, a in zip(self.params, self.param_signals, args):
            c.signals = {sig: wrap(a)}
        self.reset.enabled = False
        sim = Simulator(self.netlist)
        self.last_ticks = sim.run_until(lambda s: s.read(self.bus, STEP, [RED]) == self.halt, max_ticks)
        sim.step()  # the result register shows its value on the bus one tick after halting
        return sim.read(self.bus, self.result, [RED])


class _CfgLowerer(_Lowerer):
    def __init__(self, fn: Function) -> None:
        super().__init__(fn)
        self.step = self.b.reserve(STEP)
        self.local: dict[int, Lowered] = {}
        self.regs: dict[int, Register] = {}  # value id -> register
        self.def_block: dict[int, str] = {}
        self.writes = self.b.nl.net(GREEN, "register writes")
        self.registers: list[Register] = []
        self.mem = Memory(self.b, self.step, fn.arrays) if fn.arrays else None
        self.loads: list[Load] = []  # the current block's, while lowering it
        self.stores: list[Store] = []

    # values: block-local combinators first, then registers and arguments
    def get(self, a: Operand):
        if isinstance(a, int):
            return a
        if isinstance(a, Array):
            return Ptr(a, 0)
        if id(a) in self.local:
            return self.local[id(a)]
        if id(a) in self.regs:
            w = Wire(self.regs[id(a)].signal, 0, RED)
            return Ptr(a.array, w) if a.array is not None else w
        return self.env[id(a)]

    def op(self, op: Op):
        if op.opcode == "gep":
            base, idx = (self.get(x) for x in op.args)
            assert isinstance(base, Ptr)
            index = idx if base.index == 0 else self.arith("+", base.index, idx, op.text)
            return Ptr(base.array, index)
        if op.opcode == "load":
            assert self.mem is not None
            ptr = self.get(op.args[0])
            port = sum(1 for x in self.loads if x.array == ptr.array.name)
            self.loads.append(Load(ptr.array.name, port, ptr.index, _name(op.args[0])))
            return self.mem.load_result(ptr.array, port, ptr.index, op.text)
        if op.opcode == "store":
            assert self.mem is not None
            value, ptr = (self.get(x) for x in op.args)
            if isinstance(value, Ptr):
                raise Unsupported(f"storing a pointer: {op.text}")
            if ptr.array.readonly:
                raise Unsupported(f"store to a constant: {op.text}")
            bank = self.mem.bank(ptr.array)
            names = (_name(op.args[1]), _name(op.args[0]))
            self.stores.append(Store(0, ptr.array.name, bank.id, ptr.index, value, sources=names))
            return 0
        if op.opcode == "clear":
            assert self.mem is not None
            arr = op.args[0]
            assert isinstance(arr, Array)
            self.stores.append(Store(0, arr.name, self.mem.bank(arr).id, clear=True))
            return 0
        return super().op(op)

    def set(self, v: Value, x) -> None:
        self.local[id(v)] = x
        if isinstance(x, Wire):
            self.names.setdefault(v.name, x.signal)

    def _new_register(self, label: str) -> Register:
        r = Register(self.b.signal(), label)
        self.registers.append(r)
        return r

    def _register(self, v: Value) -> Register:
        if id(v) not in self.regs:
            r = self._new_register(f"%{v.name}")
            self.regs[id(v)] = r
            self.names[v.name] = r.signal
        return self.regs[id(v)]

    def _plan_registers(self) -> None:
        for b in self.fn.blocks:
            for p in b.phis:
                self.def_block[id(p.result)] = b.name
                self._register(p.result)
            for op in b.ops:
                self.def_block[id(op.result)] = b.name

        def need(v: Operand, used_in: str) -> None:
            if isinstance(v, Value) and self.def_block.get(id(v), used_in) != used_in:
                self._register(v)

        for b in self.fn.blocks:
            for op in b.ops:
                for a in op.args:
                    need(a, b.name)
            t = b.term
            assert t is not None
            for v in (t.cond, t.value):
                if v is not None:
                    need(v, b.name)
            for p in b.phis:
                for pred, v in p.incoming:
                    need(v, pred)

    # ---- emission

    def _step_is(self, k: int) -> Cond:
        return Cond(Sig(self.step, frozenset({RED})), "=", k)

    def _emit_jump(self, j: Jump) -> None:
        if j.delta == 0:
            return
        conds = [self._step_is(j.step)] + [Cond(_operand(t.value), t.cmp, t.constant) for t in j.tests]
        dec = Decider(conds, [Out(self.step, copy=False, constant=j.delta)], label=j.label)
        self.b.place(dec, _colors(*(t.value for t in j.tests)) | {RED}, RED)

    def _pass(self, conds: list[Cond], v: Lowered, signal: str, net: Net, label: str) -> None:
        """While ``conds`` hold, a gate passes ``v``; an ``x + 0`` renames it to ``signal`` on ``net``."""
        if isinstance(v, int):
            out, picked = Out(PRIVATE_CONSTANT, copy=False, constant=v), PRIVATE_CONSTANT
        else:
            out, picked = Out(v.signal, networks=v.color), v.signal
        gate = Decider(conds, [out], label=label)
        private = self.b.nl.net(GREEN, f"{label} (private)")
        self.b.place(gate, _colors(v) | {RED}, private)
        rename = Arithmetic(Sig(picked, frozenset({GREEN})), "+", 0, signal, label=f"{label} (rename)")
        private.attach(rename.inp)
        self.b.place(rename, (), net)

    def _emit_write(self, w: Write) -> None:
        self._pass([self._step_is(w.step)], w.value, w.register.signal, self.writes, f"{w.label} (write at {w.step})")

    def _emit_store(self, st: Store) -> None:
        at = [self._step_is(st.step)]
        if st.clear:
            self._pass(at, -st.array_id, ENABLE, self.b.bus, f"clear {st.array}")
            return
        label = f"store {st.array} at {st.step}"
        for v, sig in ((st.address, ADDRESS), (st.value, VALUE), (st.array_id, ENABLE)):
            if v != 0:
                self._pass(at, v, sig, self.b.bus, f"{label} ({sig})")

    def _emit_load(self, ld: Load, bs: BlockSchedule) -> None:
        assert self.mem is not None
        if ld.address == 0:
            return  # an absent address reads word 0
        s = Sig(self.step, frozenset({RED}))
        window = [Cond(s, ">=", bs.base), Cond(s, "<=", bs.jump)]
        arr = next(a for a in self.fn.arrays if a.name == ld.array)
        self._pass(window, ld.address, ADDRESS, self.mem.port(arr, ld.port).address, f"{bs.name}: {ld}")

    def _emit_register(self, r: Register, latch_steps: list[int]) -> None:
        if not latch_steps:
            return
        reg = Decider(
            [Cond(Sig(self.step, frozenset({RED})), "=", k, join="or") for k in latch_steps],
            [Out(r.signal, networks=GREEN)],
            [Out(r.signal, networks=RED)],
            label=str(r),
        )
        self.b.place(reg, (RED,), RED, group=f"register {r.signal}")
        self.writes.attach(reg.inp)

    def run(self) -> SteppedCompiled:
        self._plan_registers()

        self.b.group = "control"
        # Counter: S starts at 1 and rises by 1 a tick; jumps add to it.
        self.b.place(Constant({self.step: 1}, label="step counter increment"), (), RED)
        self.b.place(Arithmetic(Sig(self.step, frozenset({RED})), "+", 0, self.step, label="step counter S"), (RED,), RED)
        # Reset: while this constant is on, S * R = -S cancels the count and S
        # stays at 1. Blueprints are placed with it on; turn it off to run.
        reset_signal = self.b.reserve(RESET)
        reset = self.b.place(Constant({reset_signal: -1}, label="RESET: turn off to run"), (), RED)
        self.b.place(
            Arithmetic(
                Sig(self.step, frozenset({RED})), "*", Sig(reset_signal, frozenset({RED})), self.step, label="reset"
            ),
            (RED,),
            RED,
        )

        params, param_signals = [], []
        for p in self.fn.params:
            s = self.b.signal()
            params.append(self.b.place(Constant({}, label=f"argument %{p.name}"), (), RED, group="arguments"))
            param_signals.append(s)
            self.env[id(p)] = Wire(s, 0, RED)
            self.names[p.name] = s

        result = self._new_register("return value")

        # Pass 1: combinators of every block, and each block's settle depth.
        lowered: dict[str, dict[int, Lowered]] = {}
        nets: dict[str, Net] = {}
        sched: list[BlockSchedule] = []
        k = FIRST_STEP
        for b in self.fn.blocks:
            nets[b.name] = self.b.begin_block(b.name)
            self.local, self.loads, self.stores = {}, [], []
            for op in b.ops:
                self.set(op.result, self.op(op))
            lowered[b.name] = self.local
            bs = BlockSchedule(b.name, k, self._block_depth(b), loads=self.loads, stores=self.stores)
            for st in bs.stores:
                st.step = bs.gate
            sched.append(bs)
            k = bs.jump + 1
        schedule = Schedule(sched, self.registers, result, halt=k)

        # Pass 2: the schedule's writes and jumps.
        for b, bs in zip(self.fn.blocks, sched):
            self.local = lowered[b.name]
            for op in b.ops:
                if id(op.result) in self.regs:
                    self._keep(bs, self.regs[id(op.result)], op.result, f"keep %{op.result.name}")
            t = b.term
            assert t is not None
            for succ in t.targets + [d for _, d in t.cases]:
                for p in self._block(succ).phis:
                    for pred, v in p.incoming:
                        if pred == b.name:
                            self._keep(bs, self.regs[id(p.result)], v, p.text)
            self._plan_terminator(b, bs, schedule)

        # Emit: each block's gates and jumps next to its combinators, then registers.
        for bs in sched:
            self.b.local, self.b.group = nets[bs.name], bs.name
            for w in bs.writes:
                self._emit_write(w)
            for st in bs.stores:
                self._emit_store(st)
            for ld in bs.loads:
                self._emit_load(ld, bs)
            for j in bs.jumps:
                self._emit_jump(j)
        for r in self.registers:
            self._emit_register(r, schedule.latch_steps(r))
        if self.mem is not None:
            self.mem.build()
        halt = schedule.halt

        # Halt: hold S at the halt step by cancelling the counter's +1.
        halt_dec = Decider([self._ge(halt)], [Out(self.step, copy=False, constant=-1)], label="halt")
        self.b.place(halt_dec, (RED,), RED, group="control")

        steps = {bs.name: (bs.base, bs.jump) for bs in sched}
        return SteppedCompiled(
            self.fn.name, self.b.nl, params, param_signals, result.signal, halt, self.b.bus.probe, steps, self.names,
            reset, schedule,
        )

    def _ge(self, k: int) -> Cond:
        return Cond(Sig(self.step, frozenset({RED})), ">=", k)

    def _block(self, name: str) -> Block:
        for b in self.fn.blocks:
            if b.name == name:
                return b
        raise KeyError(name)

    def _block_depth(self, b: Block) -> int:
        """Ticks until everything the block hands on is valid."""
        needed: list[Lowered] = []
        t = b.term
        assert t is not None
        for v in (t.cond, t.value):
            if v is not None:
                needed.append(self.get(v))
        for op in b.ops:
            if id(op.result) in self.regs:
                needed.append(self.local[id(op.result)])
        for succ in t.targets + [d for _, d in t.cases]:
            for p in self._block(succ).phis:
                needed += [self.get(v) for pred, v in p.incoming if pred == b.name]
        for st in self.stores:
            needed += [st.address, st.value]
        return _depth(*(x.index if isinstance(x, Ptr) else x for x in needed))

    def _keep(self, bs: BlockSchedule, r: Register, v: Operand, label: str) -> None:
        if not any(w.register is r for w in bs.writes):
            x = self.get(v)
            bs.writes.append(Write(bs.gate, x.index if isinstance(x, Ptr) else x, r, label, _name(v)))

    def _plan_terminator(self, b: Block, bs: BlockSchedule, schedule: Schedule) -> None:
        t = b.term
        assert t is not None
        nxt = bs.jump + 1
        label = f"{b.name}: {t.text.splitlines()[0]}"

        def go(target: str, tests: list[Test], note: str = "") -> None:
            delta = (schedule.halt if target == HALT else schedule.block(target).base) - nxt
            bs.jumps.append(Jump(bs.jump, tests, delta, target, f"{label}{note}"))

        if t.kind == "ret":
            assert t.value is not None
            self._keep(bs, schedule.result, t.value, "return value")
            go(HALT, [])
        elif t.kind == "br":
            go(t.targets[0], [])
        elif t.kind == "condbr":
            c = self.get(t.cond)
            if isinstance(c, int):
                go(t.targets[0] if c else t.targets[1], [])
            else:
                go(t.targets[0], [Test(c, "!=", 0, _name(t.cond))], " (true)")
                go(t.targets[1], [Test(c, "=", 0, _name(t.cond))], " (false)")
        elif t.kind == "switch":
            c = self.get(t.cond)
            if isinstance(c, int):
                go(dict(t.cases).get(c, t.targets[0]), [])
                return
            for val, dest in t.cases:
                go(dest, [Test(c, "=", val, _name(t.cond))], f" (case {val})")
            go(t.targets[0], [Test(c, "!=", val, _name(t.cond)) for val, _ in t.cases], " (default)")
        else:
            raise Unsupported(f"terminator {t.kind}")


def _name(v: Operand) -> str:
    return f"%{v.name}" if isinstance(v, Value) else ""


def lower_cfg(fn: Function) -> SteppedCompiled:
    return _CfgLowerer(fn).run()
