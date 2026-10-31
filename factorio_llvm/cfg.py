"""Lower a function with control flow to a stepped circuit (M2).

Control is one step signal ``S`` on the red bus, counting up by one each tick.
Each basic block owns a range of steps:

    base .. e        the block's combinators settle (e - base = its depth, >= 1)
    e - 1            a decider raises the block's GATE flag for one tick
    e                ``value * GATE`` samples and renames each value to keep
    e + 1            registers latch them; the branch decider fires
    e + 2            S lands on the next block's base

Values used only inside their block are read straight from their combinator.
A value used in another block, and every phi, lives in a bus register, so it is
stable for the whole of every later block. A register is one decider; each
write to it adds one arithmetic ``value * GATE`` that outputs the register's
signal, so it is 0 except at the gate step. These all drive one shared green write net, which
every register reads, so a register needs no wiring of its own beyond the bus
and that net.

Branches add a constant to ``S`` (signals on a wire sum), so a jump costs one
decider per target. ``ret`` writes the result register and jumps to the halt
step, where a decider holds ``S`` still.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .frontend import Array, Block, Function, Op, Operand, Unsupported, Value
from .lower import GATE, PRIVATE_CONSTANT, Lowered, Wire, _Builder, _colors, _depth, _Lowerer, _operand
from .netlist import GREEN, RED, Arithmetic, Cond, Connector, Constant, Decider, Message, Net, Netlist, Out, Panel, Sig
from .memory import ADDRESS, Memory, Ptr
from .schedule import GATE_TO_LATCH, BlockSchedule, Jump, Load, Register, Schedule, Store, Test, Write
from .signals import EACH
from .sim import Simulator, wrap

STEP = "signal-S"
RESET = "signal-R"
DONE = "signal-check"  # on the OUTPUT net once halted
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
    output: Connector  # the OUTPUT terminal: result and signal-check = 1 once halted, plus output arrays
    status: Panel  # display panel: "reset", "running" or "done"
    inputs: Constant | None = None  # the INPUT terminal, if the program reads extern arrays
    io: dict[str, list[str]] = field(default_factory=dict)  # extern array -> signal per word
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
    def __init__(self, fn: Function, io: dict[str, list[str]] | None = None, screen_width: int | None = None) -> None:
        super().__init__(fn)
        self.io_choice = io or {}
        self.screen_width = screen_width
        self.step = self.b.reserve(STEP)
        self.local: dict[int, Lowered] = {}
        self.regs: dict[int, Register] = {}  # value id -> register
        self.def_block: dict[int, str] = {}
        self.writes = self.b.nl.net(GREEN, "register writes")
        self.registers: list[Register] = []
        self.mem = Memory(self.b, self.step, fn.arrays) if fn.arrays else None
        self.loads: list[Load] = []  # the current block's, while lowering it
        self.stores: list[Store] = []
        self.gates: set[tuple[int, int]] = set()  # (block net, step) with a GATE flag

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
            port = len(self.stores)
            self.mem.write_port(ptr.array, port)
            names = (_name(op.args[1]), _name(op.args[0]))
            st = Store(0, ptr.array.name, self.mem.bank(ptr.array).id, ptr.index, value, sources=names, port=port)
            self.stores.append(st)
            return 0
        if op.opcode == "clear":
            assert self.mem is not None
            arr = op.args[0]
            assert isinstance(arr, Array)
            port = len(self.stores)
            self.mem.write_port(arr, port)
            self.stores.append(Store(0, arr.name, self.mem.bank(arr).id, clear=True, port=port))
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

    def _pass(self, conds: list[Cond], v: Lowered, signal: str, net: Net, label: str, add: int = 0) -> None:
        """While ``conds`` hold, a gate passes ``v + add``; a rename puts it on ``net`` as ``signal``."""
        if isinstance(v, int):
            outs = [Out(PRIVATE_CONSTANT, copy=False, constant=v + add)]
        else:
            outs = [Out(v.signal, networks=v.color)]
            if add:
                outs.append(Out(PRIVATE_CONSTANT, copy=False, constant=add))
        gate = Decider(conds, outs, label=label)
        private = self.b.nl.net(GREEN, f"{label} (private)")
        self.b.place(gate, _colors(v) | {RED}, private)
        # Each + 0 into one signal sums the private net: the value and the added constant.
        rename = Arithmetic(Sig(EACH, frozenset({GREEN})), "+", 0, signal, label=f"{label} (rename)")
        private.attach(rename.inp)
        self.b.place(rename, (), net)

    def _gated(self, step: int, v: Lowered, signal: str, net: Net, label: str) -> None:
        """Put ``v`` on ``net`` as ``signal`` for the one tick after S = ``step``.

        A decider in the block raises GATE = 1 on the local net while S = step - 1,
        so at S = step one arithmetic ``v * GATE`` both samples and renames.
        """
        key = (id(self.b.local), step)
        if key not in self.gates:
            flag = Decider([self._step_is(step - 1)], [Out(GATE, copy=False, constant=1)], label=f"gate at {step}")
            self.b.place(flag, (RED,), GREEN)
            self.gates.add(key)
        g = Sig(GATE, frozenset({GREEN}))
        pick = Arithmetic(g, "*", _operand(v), signal, label=label)
        self.b.place(pick, _colors(v) | {GREEN}, net)

    def _emit_write(self, w: Write) -> None:
        self._gated(w.step, w.value, w.register.signal, self.writes, f"{w.label} (write at {w.step})")

    def _emit_store(self, st: Store) -> None:
        assert self.mem is not None
        sig_a, sig_v, sig_e = self.mem.write_signals[st.port]
        if st.clear:
            self._gated(st.step, -st.array_id, sig_e, self.b.bus, f"clear {st.array}")
            return
        label = f"store {st.array} at {st.step}"
        # The address goes out as address + 1 (see memory.py): a second pulse adds the 1.
        for v, sig in ((st.address, sig_a), (1, sig_a), (st.value, sig_v), (st.array_id, sig_e)):
            if v != 0:
                self._gated(st.step, v, sig, self.b.bus, f"{label} ({sig})")

    def _emit_load(self, ld: Load, bs: BlockSchedule) -> None:
        assert self.mem is not None
        s = Sig(self.step, frozenset({RED}))
        window = [Cond(s, ">=", bs.base), Cond(s, "<=", bs.jump)]
        arr = next(a for a in self.fn.arrays if a.name == ld.array)
        # Addresses travel as address + 1 (see memory.py).
        self._pass(window, ld.address, ADDRESS, self.mem.port(arr, ld.port).address, f"{bs.name}: {ld}", add=1)

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
        self._tag(reset.out, "RESET (turn off to run)", "signal-R")
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
            # Depth >= 1: the GATE flag is raised at gate - 1, which S passes only
            # when that is inside the block (a jump lands S on the base).
            bs = BlockSchedule(b.name, k, max(1, self._block_depth(b)), loads=self.loads, stores=self.stores)
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
        halt = schedule.halt

        # I/O: an OUTPUT terminal with the result once halted, a status panel, and
        # memory-mapped extern arrays (INPUT terminal only if something reads one).
        self.b.group = "io"
        output = self.b.nl.net(RED, "OUTPUT")
        out_terminal = self.b.place(Constant({}, label="OUTPUT: wire the factory here"), (), output)
        self._tag(out_terminal.out, "OUTPUT", "signal-O")
        port = Decider(
            [self._ge(halt)],
            [Out(result.signal, networks=RED), Out(DONE, copy=False)],
            label="result to OUTPUT once halted",
        )
        self.b.place(port, (RED,), output)
        s = Sig(self.step, frozenset({RED}))
        status = Panel(
            [
                Message(self._ge(halt), "signal-check", "done"),
                Message(Cond(s, "<=", 1), "signal-deny", "reset"),
                Message(Cond(s, ">", 1), "signal-hourglass", "running"),
            ],
            label="status",
            always_show=True,
        )
        self.b.place(status, (RED,))
        inputs, in_terminal, io = None, None, {}
        if self.mem is not None:
            io = self.mem.assign_io(self.io_choice, {result.signal, DONE})
            if any(bank.is_input for bank in self.mem.banks.values()):
                inputs = self.b.nl.net(RED, "INPUT")
                in_terminal = self.b.place(Constant({}, label="INPUT: wire the factory here"), (), inputs)
                self._tag(in_terminal.out, "INPUT", "signal-I")
            self.mem.build(inputs, output, self.screen_width)
        unknown = set(self.io_choice) - set(io)
        if unknown:
            raise Unsupported(f"--io names no extern array: {sorted(unknown)}")

        # Halt: hold S at the halt step by cancelling the counter's +1.
        halt_dec = Decider([self._ge(halt)], [Out(self.step, copy=False, constant=-1)], label="halt")
        self.b.place(halt_dec, (RED,), RED, group="control")

        steps = {bs.name: (bs.base, bs.jump) for bs in sched}
        return SteppedCompiled(
            self.fn.name, self.b.nl, params, param_signals, result.signal, halt, self.b.bus.probe, steps, self.names,
            reset, schedule, out_terminal.out, status, in_terminal, io,
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

    def _tag(self, terminal: Connector, text: str, icon: str) -> None:
        """A display panel naming ``terminal``, on a green wire so it is placed next to it."""
        # A wired panel shows only its messages, so give it one that always holds
        # (signal-dot is never on the wire, so it reads 0).
        always = Message(Cond(Sig("signal-dot", frozenset({GREEN})), "=", 0), icon, text)
        tag = self.b.place(Panel([always], text=text, icon=icon, always_show=True, label=text))
        self.b.nl.net(GREEN, f"tag {text}").attach(terminal, tag.inp)

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


def lower_cfg(fn: Function, io: dict[str, list[str]] | None = None, screen_width: int | None = None) -> SteppedCompiled:
    return _CfgLowerer(fn, io, screen_width).run()
