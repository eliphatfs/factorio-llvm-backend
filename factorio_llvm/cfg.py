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

from dataclasses import dataclass, field

from .frontend import Block, Function, Operand, Unsupported, Value
from .lower import PRIVATE_CONSTANT, Lowered, Wire, _Builder, _colors, _depth, _Lowerer, _operand
from .netlist import GREEN, RED, Arithmetic, Cond, Connector, Constant, Decider, Net, Netlist, Out, Sig
from .signals import EACH
from .sim import Simulator, wrap

STEP = "signal-S"
RESET = "signal-R"
GATE_TO_LATCH = 2  # ticks from the gate sampling a value to the register latching it


@dataclass
class _Register:
    signal: str
    steps: set[int] = field(default_factory=set)  # gate steps


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
        self.regs: dict[int, _Register] = {}  # value id -> register
        self.def_block: dict[int, str] = {}
        self.writes = self.b.nl.net(GREEN, "register writes")

    # values: block-local combinators first, then registers and arguments
    def get(self, a: Operand) -> Lowered:
        if isinstance(a, int):
            return a
        if id(a) in self.local:
            return self.local[id(a)]
        if id(a) in self.regs:
            return Wire(self.regs[id(a)].signal, 0, RED)
        return self.env[id(a)]

    def set(self, v: Value, x: Lowered) -> None:
        self.local[id(v)] = x
        if isinstance(x, Wire):
            self.names.setdefault(v.name, x.signal)

    def _new_register(self) -> _Register:
        sig = self.b.signal()
        return _Register(sig)

    def _register(self, v: Value) -> _Register:
        if id(v) not in self.regs:
            r = self._new_register()
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

    def _jump(self, conds: list[Cond], delta: int, label: str, reads: set) -> None:
        if delta != 0:
            dec = Decider(conds, [Out(self.step, copy=False, constant=delta)], label=label)
            self.b.place(dec, reads | {RED}, RED)

    def _write(self, r: _Register, step: int, v: Lowered, label: str) -> None:
        """A gate in the current block: at ``step`` it passes ``v`` towards register ``r``."""
        if step in r.steps:
            return
        r.steps.add(step)
        if isinstance(v, int):
            out, picked = Out(PRIVATE_CONSTANT, copy=False, constant=v), PRIVATE_CONSTANT
        else:
            out, picked = Out(v.signal, networks=v.color), v.signal
        gate = Decider([self._step_is(step)], [out], label=f"{label} (write at {step})")
        private = self.b.nl.net(GREEN, f"write {r.signal} at {step}")
        self.b.place(gate, _colors(v) | {RED}, private)
        rename = Arithmetic(Sig(picked, frozenset({GREEN})), "+", 0, r.signal, label=f"{label} (rename)")
        private.attach(rename.inp)
        self.b.place(rename, (), self.writes)

    def _emit_register(self, r: _Register, label: str) -> None:
        if not r.steps:
            return
        latch_steps = sorted(k + GATE_TO_LATCH for k in r.steps)
        reg = Decider(
            [Cond(Sig(self.step, frozenset({RED})), "=", k, join="or") for k in latch_steps],
            [Out(r.signal, networks=GREEN)],
            [Out(r.signal, networks=RED)],
            label=label,
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

        result = self._new_register()

        # Pass 1: combinators of every block, and each block's settle depth.
        lowered: dict[str, dict[int, Lowered]] = {}
        nets: dict[str, Net] = {}
        depth: dict[str, int] = {}
        for b in self.fn.blocks:
            nets[b.name] = self.b.begin_block(b.name)
            self.local = {}
            for op in b.ops:
                self.set(op.result, self.op(op))
            lowered[b.name] = self.local
            depth[b.name] = self._block_depth(b)

        # Steps.
        steps: dict[str, tuple[int, int]] = {}
        k = 1
        for b in self.fn.blocks:
            e = k + depth[b.name]
            steps[b.name] = (k, e + GATE_TO_LATCH)
            k = e + GATE_TO_LATCH + 1
        halt = k

        # Pass 2: register writes and jumps.
        for b in self.fn.blocks:
            self.local = lowered[b.name]
            self.b.local, self.b.group = nets[b.name], b.name
            base, jump = steps[b.name]
            gate = jump - GATE_TO_LATCH
            for op in b.ops:
                if id(op.result) in self.regs:
                    self._write(self.regs[id(op.result)], gate, self.local[id(op.result)], f"keep %{op.result.name}")
            t = b.term
            assert t is not None
            for succ in t.targets + [d for _, d in t.cases]:
                for p in self._block(succ).phis:
                    for pred, v in p.incoming:
                        if pred == b.name:
                            self._write(self.regs[id(p.result)], gate, self.get(v), p.text)
            self._emit_terminator(b, steps, jump, result, gate, halt)

        for b in self.fn.blocks:
            for p in b.phis:
                self._emit_register(self.regs[id(p.result)], p.text)
            for op in b.ops:
                if id(op.result) in self.regs:
                    self._emit_register(self.regs[id(op.result)], f"keep %{op.result.name}")
        self._emit_register(result, "return value")

        # Halt: hold S at the halt step by cancelling the counter's +1.
        halt_dec = Decider([self._ge(halt)], [Out(self.step, copy=False, constant=-1)], label="halt")
        self.b.place(halt_dec, (RED,), RED, group="control")

        return SteppedCompiled(
            self.fn.name, self.b.nl, params, param_signals, result.signal, halt, self.b.bus.probe, steps, self.names, reset
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
        return _depth(*needed)

    def _emit_terminator(self, b: Block, steps, jump: int, result: _Register, gate: int, halt: int) -> None:
        t = b.term
        assert t is not None
        nxt = jump + 1

        def delta(target: str) -> int:
            return steps[target][0] - nxt

        at = self._step_is(jump)
        label = f"{b.name}: {t.text.splitlines()[0]}"
        if t.kind == "ret":
            assert t.value is not None
            self._write(result, gate, self.get(t.value), "return value")
            self._jump([at], halt - nxt, label, set())
        elif t.kind == "br":
            self._jump([at], delta(t.targets[0]), label, set())
        elif t.kind == "condbr":
            c = self.get(t.cond)
            if isinstance(c, int):
                self._jump([at], delta(t.targets[0] if c else t.targets[1]), label, set())
            else:
                self._jump([at, Cond(_operand(c), "!=", 0)], delta(t.targets[0]), f"{label} (true)", _colors(c))
                self._jump([at, Cond(_operand(c), "=", 0)], delta(t.targets[1]), f"{label} (false)", _colors(c))
        elif t.kind == "switch":
            c = self.get(t.cond)
            if isinstance(c, int):
                dest = dict(t.cases).get(c, t.targets[0])
                self._jump([at], delta(dest), label, set())
                return
            for val, dest in t.cases:
                self._jump([at, Cond(_operand(c), "=", val)], delta(dest), f"{label} (case {val})", _colors(c))
            self._jump(
                [at] + [Cond(_operand(c), "!=", val) for val, _ in t.cases],
                delta(t.targets[0]),
                f"{label} (default)",
                _colors(c),
            )
        else:
            raise Unsupported(f"terminator {t.kind}")


def lower_cfg(fn: Function) -> SteppedCompiled:
    return _CfgLowerer(fn).run()
