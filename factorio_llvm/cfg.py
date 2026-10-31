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

from .frontend import _array_of, Array, Block, Function, Op, Operand, Unsupported, Value
from .fuse import Bool, pieces
from .lower import GATE, PRIVATE_CONSTANT, STABLE, Lowered, Wire, _Builder, _colors, _depth, _early, _Lowerer, _operand, _vary
from .signals import EACH, rich_text
from .netlist import GREEN, RED, Arithmetic, Cond, Connector, Constant, Decider, Message, Net, Netlist, Out, Panel, Sig
from .memory import ADDRESS, STORE_LANDS, Memory, Ptr
from .schedule import GATE_TO_LATCH, BlockSchedule, Jump, Load, Register, Schedule, Store, Test, Write
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
        self.reset_signal = self.b.reserve(RESET)
        self.local: dict[int, Lowered] = {}
        self.regs: dict[int, Register] = {}  # value id -> register
        self.def_block: dict[int, str] = {}
        self.writes = self.b.nl.net(GREEN, "register writes")
        self.registers: list[Register] = []
        self.mem = Memory(self.b, self.step, fn.arrays) if fn.arrays else None
        self.written = {a.name for b in fn.blocks for o in b.ops if o.opcode in ("store", "clear") for a in [_array_of(o)] if a}
        self.direct_reads: list[tuple[Arithmetic, Array, int]] = []
        self.loads: list[Load] = []  # the current block's, while lowering it
        self.stores: list[Store] = []
        self.copies: dict[int, tuple[Register, str]] = {}  # phi -> (copy register, phi's block)
        self.cur = ""  # the block being lowered
        self.gates: dict[tuple[int, int], str] = {}  # (block net, step) -> its flag signal
        self.phi_block = {id(p.result): b.name for b in fn.blocks for p in b.phis}
        self.chains: dict[int, list[str]] = {}  # pipelined block net -> its iteration flags

    # values: block-local combinators first, then registers and arguments
    def get_raw(self, a: Operand):
        if not isinstance(a, int) and id(a) in self.copies and self.copies[id(a)][1] != self.cur:
            w = Wire(self.copies[id(a)][0].signal, 0, RED, early=STABLE, vary=-STABLE)
            return Ptr(a.array, w) if a.array is not None else w
        if isinstance(a, int):
            return a
        if isinstance(a, Array):
            return Ptr(a, 0)
        if id(a) in self.local:
            return self.local[id(a)]
        if id(a) in self.regs:
            # Only the block's own phis change while it runs (see _pipeline).
            w = Wire(self.regs[id(a)].signal, 0, RED, **_changing(self.phi_block.get(id(a)) == self.cur))
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
            if isinstance(ptr.index, int) and ptr.array.external and ptr.array.name not in self.written:
                # An input word at a known index: read it straight off the
                # INPUT (or joystick) wire, no read port. Its signal is picked
                # later, so the copy is filled in by _wire_direct_reads.
                out = self.b.local_signal()
                copy = Arithmetic(Sig(EACH, frozenset({RED})), "+", 0, out, label=f"{op.text} (direct)")
                self.b.place(copy, (), GREEN)
                self.direct_reads.append((copy, ptr.array, ptr.index))
                return Wire(out, 1, early=STABLE, vary=-STABLE)
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
            port = sum(1 for s in self.stores if s.wave == op.wave)
            self.mem.write_port(ptr.array, port)
            names = (_name(op.args[1]), _name(op.args[0]))
            aid = self.mem.bank(ptr.array).id
            st = Store(0, ptr.array.name, aid, ptr.index, value, sources=names, port=port, wave=op.wave, enable=self._enable(op, aid))
            self.stores.append(st)
            return 0
        if op.opcode == "clear":
            assert self.mem is not None
            arr = op.args[0]
            assert isinstance(arr, Array)
            port = sum(1 for s in self.stores if s.wave == op.wave)
            self.mem.write_port(arr, port)
            aid = self.mem.bank(arr).id
            self.stores.append(Store(0, arr.name, aid, clear=True, port=port, wave=op.wave, enable=self._enable(op, -aid)))
            return 0
        return super().op(op)

    def _enable(self, op: Op, e: int):
        """A guarded store's E: ``e`` while its guard holds, else 0 (no write)."""
        if op.guard is None:
            return None
        g = self._boolish(self.get_raw(op.guard))
        return self.real(pieces([(g, e)]))

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

        phi_block = {id(p.result): b.name for b in self.fn.blocks for p in b.phis}

        def need(v: Operand, used_in: str) -> None:
            if isinstance(v, Value) and self.def_block.get(id(v), used_in) != used_in:
                self._register(v)
            # A phi's register takes its next value at the end of every
            # predecessor, whichever way that one branches; uses outside the
            # phi's block read a copy the block keeps instead.
            if isinstance(v, Value) and phi_block.get(id(v), used_in) != used_in and id(v) not in self.copies:
                r = self._new_register(f"%{v.name} (as last seen)")
                self.copies[id(v)] = (r, phi_block[id(v)])

        for b in self.fn.blocks:
            for op in b.ops:
                for a in [*op.args, op.guard]:
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
        leaves = [t.value for t in j.tests]
        if j.when is not None:
            # (S = step and tests) and (c1 or c2 ...) = (S = step and tests and c1) or ...
            base, conds = conds, []
            for clause in j.when.clauses:
                conds += [Cond(c.left, c.cmp, c.right, join="or" if conds and k == 0 else "and") for k, c in enumerate(base)]
                conds += [Cond(_operand(a.a), a.cmp, _operand(a.b)) for a in clause]
            leaves += self._leaves(j.when)
        dec = Decider(conds, [Out(self.step, copy=False, constant=j.delta)], label=j.label)
        self.b.place(dec, _colors(*leaves) | {RED}, RED)

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

        A decider in the block raises a flag = 1 on the local net while S = step - 1,
        so at S = step one arithmetic ``v * flag`` both samples and renames. The
        block's first flag is GATE; stores of earlier waves get flags of their own.
        """
        if getattr(self, "_gated_by", None):
            g = Sig(self._gated_by, frozenset({GREEN}))
            self.b.place(Arithmetic(g, "*", _operand(v), signal, label=label), _colors(v) | {GREEN}, net)
            return
        key = (id(self.b.local), step)
        if key not in self.gates:
            mine = [s for (net, _), s in self.gates.items() if net == key[0]]
            sig = GATE if not mine else self.b.local_signal()
            flag = Decider([self._step_is(step - 1)], [Out(sig, copy=False, constant=1)], label=f"gate at {step}")
            self.b.place(flag, (RED,), GREEN)
            self.gates[key] = sig
        g = Sig(self.gates[key], frozenset({GREEN}))
        pick = Arithmetic(g, "*", _operand(v), signal, label=label)
        self.b.place(pick, _colors(v) | {GREEN}, net)

    def _iteration_flag(self, base: int, delay: int) -> str:
        """A pipelined block's flag that is 1 for the one tick ``delay`` ticks
        after each iteration starts (S = base), as a block-local signal."""
        chain = self.chains.setdefault(id(self.b.local), [])
        while len(chain) < delay:
            sig = self.b.local_signal()
            if not chain:
                self.b.place(Decider([self._step_is(base)], [Out(sig, copy=False, constant=1)], label="iteration starts"), (RED,), GREEN)
            else:
                self.b.place(Arithmetic(Sig(chain[-1], frozenset({GREEN})), "+", 0, sig, label=f"iteration + {len(chain) + 1}"), (GREEN,), GREEN)
            chain.append(sig)
        return chain[delay - 1]

    def _emit_write(self, w: Write) -> None:
        self._gated(w.step, w.value, w.register.signal, self.writes, f"{w.label} (write at {w.step})")

    def _emit_store(self, st: Store, bs: BlockSchedule | None = None) -> None:
        assert self.mem is not None
        if st.delay is not None:
            assert bs is not None
            flag = self._iteration_flag(bs.base + bs.prologue, st.delay)
            self._gated_by = flag
        else:
            self._gated_by = None
        sig_a, sig_v, sig_e = self.mem.write_signals[st.port]
        if st.clear:
            e = st.enable if st.enable is not None else -st.array_id
            if e != 0:
                self._gated(st.step, e, sig_e, self.b.bus, f"clear {st.array}")
            return
        label = f"store {st.array} at {st.step}"
        e = st.enable if st.enable is not None else st.array_id
        # The address goes out as address + 1 (see memory.py): a second pulse adds the 1.
        for v, sig in ((st.address, sig_a), (1, sig_a), (st.value, sig_v), (e, sig_e)):
            if v != 0:
                self._gated(st.step, v, sig, self.b.bus, f"{label} ({sig})")

    def _emit_load(self, ld: Load, bs: BlockSchedule) -> None:
        assert self.mem is not None
        s = Sig(self.step, frozenset({RED}))
        window = [Cond(s, ">=", bs.base), Cond(s, "<=", bs.jump + bs.drain)]
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
        reset_signal = self.reset_signal
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
            params.append(self.b.place(Constant({s: 0}, label=f"argument %{p.name} = {rich_text(s)}"), (), RED, group="arguments"))
            self.b.tag(params[-1].out, f"argument %{p.name}", s, group="arguments")
            param_signals.append(s)
            self.env[id(p)] = Wire(s, 0, RED, early=STABLE, vary=-STABLE)
            self.names[p.name] = s

        result = self._new_register("return value")

        # Pass 1: combinators of every block, and each block's settle depth.
        lowered: dict[str, dict[int, Lowered]] = {}
        nets: dict[str, Net] = {}
        sched: list[BlockSchedule] = []
        k = FIRST_STEP
        for b in self.fn.blocks:
            nets[b.name] = self.b.begin_block(b.name)
            self.cur = b.name
            self.balance = self._loop_with_stores(b)
            self.local, self.loads, self.stores = {}, [], []
            for op in b.ops:
                self.set(op.result, self.op(op))
            lowered[b.name] = self.local
            # Depth >= 1: the GATE flag is raised at gate - 1, which S passes only
            # when that is inside the block (a jump lands S on the base).
            bs = BlockSchedule(b.name, k, max(1, self._block_depth(b)), loads=self.loads, stores=self.stores)
            last = max((st.wave for st in bs.stores), default=0)
            for st in bs.stores:
                st.step = bs.gate - (last - st.wave)
            self._pipeline(b, bs)
            sched.append(bs)
            k = bs.jump + 1 + bs.drain
        schedule = Schedule(sched, self.registers, result, halt=k)

        # Pass 2: the schedule's writes and jumps.
        for b, bs in zip(self.fn.blocks, sched):
            self.local = lowered[b.name]
            self.cur = b.name
            self.balance = self._loop_with_stores(b)
            for p in b.phis:
                if id(p.result) in self.copies:
                    self._keep(bs, self.copies[id(p.result)][0], p.result, f"copy %{p.result.name}")
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

        self.balance = False
        # Emit: each block's gates and jumps next to its combinators, then registers.
        for bs in sched:
            self.b.local, self.b.group = nets[bs.name], bs.name
            self.b._local_free = self.b.pools[id(self.b.local)]
            for w in bs.writes:
                self._emit_write(w)
            for st in bs.stores:
                self._emit_store(st, bs)
            self._gated_by = None
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
        out_terminal = self.b.place(Constant({}, label=f"OUTPUT: wire the factory here. Result = {rich_text(result.signal)}, done = {rich_text(DONE)}"), (), output)
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
            banks = list(self.mem.banks.values())
            ins = [b for b in banks if b.is_input]  # the joystick has its own net
            if ins:
                # Every input word starts at 0 here: set it in the terminal, or
                # wire the factory to it.
                inputs = self.b.nl.net(RED, "INPUT")
                zeros = {sig: 0 for b in ins for sig in b.io}
                label = "INPUT: wire the factory here, or set values here.\n" + _io_lines(ins)
                in_terminal = self.b.place(Constant(zeros, label=label), (), inputs)
                self._tag(in_terminal.out, "INPUT", "signal-I")
            outs = [b for b in banks if b.array.external and b.side == "OUTPUT"]
            if outs:
                out_terminal.label += "\n" + _io_lines(outs)
            self.mem.build(inputs, output, self.screen_width)
            for copy, arr, i in self.direct_reads:
                bank = self.mem.bank(arr)
                copy.left = Sig(bank.io[i], frozenset({RED}))
                (self.mem.joystick if bank.is_joystick else self.mem.clock if bank.is_clock else inputs).attach(copy.inp)
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

    @staticmethod
    def _loop_with_stores(b: Block) -> bool:
        """A one-block loop that stores: a candidate for _pipeline, so its
        combinators are balanced as they are placed."""
        return b.term is not None and b.name in b.term.targets and any(op.opcode in ("store", "clear") for op in b.ops)

    def _pipeline(self, b: Block, bs: BlockSchedule) -> None:
        """Overlap the iterations of a one-block loop, if that is faster.

        Normally an iteration takes the block's whole depth plus 2 (latch and
        jump). But only the loop-carried values (phis), the branch and the
        values kept for later must be ready by the latch; stores need not be.
        Every combinator is a pipeline register, so iteration i + 1 can start
        (the phis latch new values) while iteration i's stores are still on
        their way down the circuit, as long as each store's inputs are still
        from iteration i when it samples them.

        The phis hold for II ticks, so a value is from one iteration from its
        longest path (``depth``) until II ticks after its shortest (``early``)
        from the phis. A store at ``k`` ticks into its iteration needs every
        input inside that window: ``depth <= k < early + II``. Iteration
        i + 1's stores must come after all of iteration i's (they may alias):
        the stores' spread is less than II. A flag born at the iteration's
        start runs down a chain of copies and fires each store at its tick.

        Phis change every iteration, loop invariants and inputs do not, so II
        counts only the paths from the phis (``vary``); the first iteration
        waits ``prologue`` steps more for the rest to settle once.

        On exit the loop's phis keep their last values, so the last
        iterations' stores still see the right inputs; the exit waits
        ``drain`` steps for them before the next block starts.
        """
        t = b.term
        assert t is not None
        if b.name not in t.targets or not self.stores:
            return
        needed = self._needed(b)
        ins_of = {id(st): [x for x in (st.address, st.value, st.enable) if x is not None] for st in self.stores}
        ks: dict[int, int] = {}
        before = 0
        for wave in sorted({st.wave for st in self.stores}):
            nxt = before
            for st in (x for x in self.stores if x.wave == wave):
                ks[id(st)] = max(1, _vary(*ins_of[id(st)]), before + 1)
                nxt = max(nxt, ks[id(st)])
            before = nxt
        for _ in range(16):
            ii = max(3, _vary(*needed) + 2, max(ks.values()) - min(ks.values()) + 1)
            # (Each changing store input gets delayed to arrive at its store's
            # tick, so it is in its window whatever II.)
            # Memory: a read of an array the loop writes must see every store
            # of the iterations before and none of its own (nor later ones').
            # A read r ticks into the iteration sees a store at k once
            # r >= k + STORE_LANDS; the previous iteration's at k - II.
            written: dict[str, list[int]] = {}
            for st in self.stores:
                written.setdefault(st.array, []).append(ks[id(st)])
            late = 0  # how far the stores must move later
            for v in needed:
                for arr, lo, hi in v.reads if isinstance(v, Wire) else ():
                    if arr in written:
                        # sampled at II - 2 (kept) or II - 1 (branch)
                        ii = max(ii, -(-(max(written[arr]) + STORE_LANDS + 2 + hi) // 2))
                        late = max(late, (ii - 1 - lo) - (min(written[arr]) + STORE_LANDS - 1))
            for st in self.stores:
                for v in ins_of[id(st)]:
                    d = ks[id(st)] - v.lo if isinstance(v, Wire) and v.lo < STABLE else 0
                    for arr, lo, hi in v.reads if isinstance(v, Wire) else ():
                        lo, hi = lo + d, hi + d
                        if arr in written:
                            ii = max(ii, max(written[arr]) + STORE_LANDS - ks[id(st)] + hi)
                            late = max(late, (ks[id(st)] - lo) - (min(written[arr]) + STORE_LANDS - 1))
            if late <= 0:
                break
            ks = {i: k + late for i, k in ks.items()}
        else:
            return
        # The first iteration also waits for what settles once (loop
        # invariants, inputs): the prologue.
        pro = max(0, _depth(*needed) - (ii - 2))
        for st in self.stores:
            for v in ins_of[id(st)]:
                if isinstance(v, Wire):  # changing inputs get delayed by k - early
                    pro = max(pro, v.depth - (v.lo if v.lo < STABLE else ks[id(st)]))
        if ii >= bs.depth + 2:
            return
        for st in self.stores:
            k = ks[id(st)]
            delay = lambda v: self._delayed(v, k - v.lo) if isinstance(v, Wire) and v.lo < STABLE else v
            st.address, st.value = delay(st.address), delay(st.value)
            st.enable = delay(st.enable) if st.enable is not None else None
        bs.depth, bs.pipelined, bs.prologue = pro + ii - 2, True, pro
        for st in self.stores:
            st.delay = ks[id(st)]
            st.step = bs.base + pro + st.delay
        # The next block starts as if after a block whose stores are at the last delay.
        bs.drain = max(0, max(ks.values()) + 2 - ii)

    def _needed(self, b: Block) -> list[Lowered]:
        """What the block hands on: kept values and its branch's inputs."""
        needed: list[Lowered] = []
        t = b.term
        assert t is not None
        for v in (t.cond, t.value):
            if v is not None:
                folded = self._folded_branch(b) if v is t.cond else None
                needed += self._leaves(folded) if folded else [self.get(v)]
        for op in b.ops:
            if id(op.result) in self.regs:
                needed.append(self.get(op.result))
        for succ in t.targets + [d for _, d in t.cases]:
            for p in self._block(succ).phis:
                needed += [self.get(v) for pred, v in p.incoming if pred == b.name]
        return [x.index if isinstance(x, Ptr) else x for x in needed]

    def _block_depth(self, b: Block) -> int:
        """Ticks until everything the block hands on is valid."""
        needed = self._needed(b)
        last = max((st.wave for st in self.stores), default=0)
        depth = _depth(*needed)
        for st in self.stores:
            # Earlier waves go out (last - wave) steps before the gate, after
            # the GATE flag step (so at least one step into the block).
            ready = _depth(*(x for x in (st.address, st.value, st.enable) if x is not None))
            depth = max(depth, ready + last - st.wave, last - st.wave + 1)
        return depth

    def _folded_branch(self, b: Block):
        """A conditional branch's condition, to test right in the jump decider
        instead of placing it as a signal first, or None."""
        t = b.term
        if t is None or t.kind != "condbr":
            return None
        raw = self.get_raw(t.cond)
        return raw if isinstance(raw, Bool) and raw.memo is None else None

    def _tag(self, terminal: Connector, text: str, icon: str) -> None:
        self.b.tag(terminal, text, icon)

    def _keep(self, bs: BlockSchedule, r: Register, v: Operand, label: str) -> None:
        if not any(w.register is r for w in bs.writes):
            x = self.get(v)
            bs.writes.append(Write(bs.gate, x.index if isinstance(x, Ptr) else x, r, label, _name(v)))

    def _plan_terminator(self, b: Block, bs: BlockSchedule, schedule: Schedule) -> None:
        t = b.term
        assert t is not None
        nxt = bs.jump + 1
        label = f"{b.name}: {t.text.splitlines()[0]}"

        def offset(target: str) -> int:
            if bs.drain and target != b.name:
                return 0  # into the drain steps; their last one jumps on
            if target == b.name:
                return bs.base + bs.prologue - nxt  # past the prologue
            return (schedule.halt if target == HALT else schedule.block(target).base) - nxt

        if bs.drain:
            exits = [x for x in t.targets + [d for _, d in t.cases] if x != b.name]
            if exits:
                assert len(set(exits)) == 1
                end = bs.jump + bs.drain
                bs.jumps.append(Jump(end, [], schedule.block(exits[0]).base - (end + 1), exits[0], f"{b.name}: after the drain"))

        def go(target: str, tests: list[Test], note: str = "", when=None, minus: int = 0) -> None:
            bs.jumps.append(Jump(bs.jump, tests, offset(target) - minus, target, f"{label}{note}", when))

        if t.kind == "ret":
            assert t.value is not None
            self._keep(bs, schedule.result, t.value, "return value")
            go(HALT, [])
        elif t.kind == "br":
            go(t.targets[0], [])
        elif t.kind == "condbr" and (folded := self._folded_branch(b)):
            # Always take the false jump; when the condition holds, a second
            # decider adds the difference, landing on the true target.
            go(t.targets[1], [], " (false, unless true)")
            go(t.targets[0], [], " (true: on top of false)", folded, offset(t.targets[1]))
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


def _changing(yes: bool) -> dict[str, int]:
    """``early``/``vary`` of a register read in a block: 0 if it changes there."""
    return {"early": 0, "vary": 0} if yes else {"early": STABLE, "vary": -STABLE}


def _io_lines(banks) -> str:
    """One description line per word: ``@in[0] = <icon>``."""
    return "\n".join(f"@{b.array.name}[{i}] = {rich_text(sig)}" for b in banks for i, sig in enumerate(b.io))


def _name(v: Operand) -> str:
    return f"%{v.name}" if isinstance(v, Value) else ""


def lower_cfg(fn: Function, io: dict[str, list[str]] | None = None, screen_width: int | None = None) -> SteppedCompiled:
    return _CfgLowerer(fn, io, screen_width).run()
