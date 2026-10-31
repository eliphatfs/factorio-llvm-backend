"""Lower a single-block function to a combinator pipeline (M1).

Arguments sit on the global red bus. Every SSA value in a block gets its own
signal on the block's green local net. Each combinator reads its operands by
name and writes its result to the local net, so values never collide and an
operation costs one combinator and one tick. With the arguments held steady in
constant combinators, the result is valid once ``latency`` ticks have passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, TypeVar

from .frontend import Function, Op, Operand, Unsupported, Value
from .signals import rich_text
from .netlist import GREEN, RED, Arithmetic, Color, Cond, Connector, Constant, Decider, Entity, Net, Netlist, Out, Sig
from .signals import EACH, allocatable_signals
from .sim import Simulator, arith, compare, wrap

INT_MIN = -(2**31)

E = TypeVar("E", bound=Entity)

_BINOPS = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "sdiv": "/",
    "srem": "%",
    "shl": "<<",
    "ashr": ">>",
    "and": "AND",
    "or": "OR",
    "xor": "XOR",
}
_I1_BINOPS = {"and", "or", "xor"}

_ICMP = {"eq": "=", "ne": "!=", "slt": "<", "sgt": ">", "sle": "<=", "sge": ">="}
_UNSIGNED = {"ult": "<", "ugt": ">", "ule": "<=", "uge": ">="}
_MIRROR = {"=": "=", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


@dataclass(frozen=True)
class Wire:
    """A lowered value: ``signal`` on a net, valid ``depth`` ticks after the inputs.

    ``color`` says which net: RED is the global bus (arguments, registers, S),
    GREEN is the current block's local net (its combinator results).
    """

    signal: str
    depth: int
    color: Color = GREEN


Lowered = Wire | int


@dataclass
class Compiled:
    function: str
    netlist: Netlist
    params: list[Constant]
    param_signals: list[str]
    result: Lowered
    latency: int
    probe: Connector | None  # a connector on the net that carries the result
    signals: dict[str, str] = field(default_factory=dict)  # IR value name -> signal

    def run(self, *args: int) -> int:
        """Simulate the circuit on ``args`` and return the result."""
        if len(args) != len(self.params):
            raise TypeError(f"@{self.function} takes {len(self.params)} arguments")
        for c, sig, a in zip(self.params, self.param_signals, args):
            c.signals = {sig: wrap(a)}
        if isinstance(self.result, int):
            return self.result
        sim = Simulator(self.netlist)
        sim.step(self.latency)
        assert self.probe is not None
        return sim.read(self.probe, self.result.signal, [self.result.color])


PRIVATE_CONSTANT = "signal-dot"  # name for a constant on a private two-entity net
GATE = "signal-dot"  # a block's "sample now" flag on its local net (never a value there)


class _Builder:
    """Places entities and attaches them to the global bus or the block's local net."""

    def __init__(self) -> None:
        self.nl = Netlist()
        self.bus = self.nl.net(RED, "bus")
        self.local: Net | None = None
        self.group = ""
        self._free = list(reversed(allocatable_signals()))
        self._local_free: list[str] = []

    def signal(self) -> str:
        """A fresh signal on the global bus."""
        try:
            return self._free.pop()
        except IndexError:
            raise Unsupported("ran out of signals on the global bus (too many registers)") from None

    def local_signal(self) -> str:
        """A fresh signal on the current block's local net."""
        try:
            return self._local_free.pop()
        except IndexError:
            raise Unsupported(f"ran out of signals in block {self.group}: it has too many values") from None

    def reserve(self, name: str) -> str:
        """Take a specific bus signal, for readability (e.g. S for the step counter)."""
        self._free.remove(name)
        return name

    def begin_block(self, name: str) -> Net:
        self.local = self.nl.net(GREEN, f"block {name}")
        self._local_free = [s for s in reversed(allocatable_signals()) if s != GATE]
        self.group = name
        return self.local

    def net_of(self, color: Color) -> Net:
        if color == RED:
            return self.bus
        assert self.local is not None
        return self.local

    def place(self, e: E, ins: Iterable[Color] = (), out: Color | Net | None = None, group: str | None = None) -> E:
        e.group = self.group if group is None else group
        self.nl.add(e)
        for c in set(ins):
            self.net_of(c).attach(e.inp)
        if out is not None:
            (out if isinstance(out, Net) else self.net_of(out)).attach(e.out)
        return e


def _operand(v: Lowered) -> Sig | int:
    return v if isinstance(v, int) else Sig(v.signal, frozenset({v.color}))


def _colors(*vs: Lowered) -> set[Color]:
    return {v.color for v in vs if isinstance(v, Wire)}


def _depth(*vs: Lowered) -> int:
    return max((v.depth for v in vs if isinstance(v, Wire)), default=0)


class _Lowerer:
    def __init__(self, fn: Function) -> None:
        self.fn = fn
        self.b = _Builder()
        self.env: dict[int, Lowered] = {}
        self.names: dict[str, str] = {}

    def get(self, a: Operand) -> Lowered:
        return a if isinstance(a, int) else self.env[id(a)]

    def set(self, v: Value, x: Lowered) -> None:
        self.env[id(v)] = x
        if isinstance(x, Wire):
            self.names.setdefault(v.name, x.signal)

    # ---- emitters

    def arith(self, op: str, a: Lowered, b: Lowered, label: str) -> Lowered:
        if isinstance(a, int) and isinstance(b, int):
            return arith(op, a, b)
        out = self.b.local_signal()
        self.b.place(Arithmetic(_operand(a), op, _operand(b), out, label=label), _colors(a, b), GREEN)
        return Wire(out, _depth(a, b) + 1)

    def compare(self, cmp: str, a: Lowered, b: Lowered, label: str) -> Lowered:
        if isinstance(a, int) and isinstance(b, int):
            return int(compare(cmp, a, b))
        if isinstance(a, int):
            a, b, cmp = b, a, _MIRROR[cmp]
        assert isinstance(a, Wire)
        out = self.b.local_signal()
        dec = Decider([Cond(_operand(a), cmp, _operand(b))], [Out(out, copy=False, constant=1)], label=label)
        self.b.place(dec, _colors(a, b), GREEN)
        return Wire(out, _depth(a, b) + 1)

    def select(self, c: Lowered, t: Lowered, f: Lowered, label: str) -> Lowered:
        if isinstance(c, int):
            return t if c else f
        return self.pick(c, "!=", 0, t, f, label)

    def pick(self, a: Lowered, cmp: str, b: Lowered, t: Lowered, f: Lowered, label: str) -> Lowered:
        """``a cmp b ? t : f``, with the comparison folded into the picking decider."""
        if isinstance(a, int) and isinstance(b, int):
            return t if compare(cmp, a, b) else f
        if isinstance(a, int):
            a, b, cmp = b, a, _MIRROR[cmp]
        if t == f:
            return t
        out = self.b.local_signal()
        cond = [Cond(_operand(a), cmp, _operand(b))]
        if isinstance(t, int) and isinstance(f, int):
            dec = Decider(cond, [Out(out, copy=False, constant=t)], [Out(out, copy=False, constant=f)], label=label)
            self.b.place(dec, _colors(a, b), GREEN)
            return Wire(out, _depth(a, b) + 1)

        # Pass the chosen value under its own name onto a private net, then
        # rename it with Each + 0 onto the block's net.
        def branch(v: Lowered) -> Out:
            if isinstance(v, Wire):
                return Out(v.signal, networks=v.color)
            return Out(PRIVATE_CONSTANT, copy=False, constant=v)

        private = self.b.nl.net(GREEN, f"pick {out}")
        gate = Decider(cond, [branch(t)], [branch(f)], label=f"{label} (pick)")
        self.b.place(gate, _colors(a, b, t, f), private)
        rename = Arithmetic(Sig(EACH, frozenset({GREEN})), "+", 0, out, label=f"{label} (rename)")
        self.b.place(rename, (), GREEN)
        private.attach(rename.inp)
        return Wire(out, _depth(a, b, t, f) + 2)

    def uge(self, a: Lowered, b: Lowered, label: str) -> Lowered:
        fa = self.arith("XOR", a, INT_MIN, f"{label} (flip a)")
        fb = self.arith("XOR", b, INT_MIN, f"{label} (flip b)")
        return self.compare(">=", fa, fb, f"{label} (uge)")

    def udiv(self, a: Lowered, b: Lowered, label: str) -> Lowered:
        """Unsigned division from signed operations (b != 0)."""
        if isinstance(a, int) and isinstance(b, int):
            return wrap((a & 0xFFFFFFFF) // (b & 0xFFFFFFFF))
        # b >= 2^31: the quotient is 0 or 1.
        big = self.uge(a, b, f"{label} (b huge)")
        # a < 2^31, b < 2^31: plain signed division.
        small = self.arith("/", a, b, f"{label} (a small)")
        # a >= 2^31: halve a, divide, double, then correct by at most one.
        half = self.arith("AND", self.arith(">>", a, 1, f"{label} (a/2)"), 0x7FFFFFFF, f"{label} (a/2 mask)")
        q = self.arith("<<", self.arith("/", half, b, f"{label} (a/2/b)"), 1, f"{label} (2q)")
        r = self.arith("-", a, self.arith("*", q, b, f"{label} (2q*b)"), f"{label} (r)")
        q = self.arith("+", q, self.uge(r, b, f"{label} (r >= b)"), f"{label} (q fix)")
        pos = self.pick(a, "<", 0, q, small, f"{label} (a sign)")
        return self.pick(b, "<", 0, big, pos, label)

    # ---- instructions

    def op(self, op: Op) -> Lowered:
        args = [self.get(a) for a in op.args]
        widths = [a.width if isinstance(a, Value) else None for a in op.args]
        label = op.text
        opc = op.opcode
        if opc in _BINOPS:
            if op.result.width == 1 and opc not in _I1_BINOPS:
                raise Unsupported(f"i1 arithmetic: {label}")
            return self.arith(_BINOPS[opc], args[0], args[1], label)
        if opc in ("udiv", "urem"):
            a, b = args
            q = self.udiv(a, b, label)
            if opc == "udiv":
                return q
            return self.arith("-", a, self.arith("*", q, b, f"{label} (q*b)"), label)
        if opc == "lshr":
            x, n = args
            if isinstance(n, int):
                n &= 31
                if n == 0:
                    return x
                t = self.arith(">>", x, n, f"{label} (shift)")
                return self.arith("AND", t, (1 << (32 - n)) - 1, f"{label} (mask)")
            # mask = ~((-1 << (31 - n)) << 1) keeps the low 32 - n bits, n in 0..31
            k = self.arith("-", 31, n, f"{label} (31 - n)")
            m = self.arith("<<", -1, k, f"{label} (mask 1)")
            m = self.arith("<<", m, 1, f"{label} (mask 2)")
            m = self.arith("XOR", m, -1, f"{label} (mask 3)")
            t = self.arith(">>", x, n, f"{label} (shift)")
            return self.arith("AND", t, m, label)
        if opc == "icmp":
            a, b = args
            pred = op.predicate or ""
            if pred in _ICMP:
                return self.compare(_ICMP[pred], a, b, label)
            if pred in _UNSIGNED:
                if widths[0] == 1:
                    raise Unsupported(f"unsigned i1 compare: {label}")
                a = self.arith("XOR", a, INT_MIN, f"{label} (flip a)")
                b = self.arith("XOR", b, INT_MIN, f"{label} (flip b)")
                return self.compare(_UNSIGNED[pred], a, b, label)
            raise Unsupported(f"icmp predicate {pred}: {label}")
        if opc in ("smax", "smin"):
            a, b = args
            return self.pick(a, ">" if opc == "smax" else "<", b, a, b, label)
        if opc in ("umax", "umin"):
            a, b = args
            fa = self.arith("XOR", a, INT_MIN, f"{label} (flip a)")
            fb = self.arith("XOR", b, INT_MIN, f"{label} (flip b)")
            return self.pick(fa, ">" if opc == "umax" else "<", fb, a, b, label)
        if opc == "abs":
            x = args[0]
            return self.pick(x, "<", 0, self.arith("-", 0, x, f"{label} (negate)"), x, label)
        if opc == "select":
            return self.select(args[0], args[1], args[2], label)
        if opc in ("zext", "freeze") or (opc in ("trunc", "sext") and widths[0] == op.result.width):
            return args[0]
        if opc == "sext" and widths[0] == 1:
            return self.arith("*", args[0], -1, label)
        if opc == "trunc" and op.result.width == 1:
            return self.arith("AND", args[0], 1, label)
        raise Unsupported(f"instruction not supported yet: {label}")

    def run(self) -> Compiled:
        params: list[Constant] = []
        param_signals: list[str] = []
        for p in self.fn.params:
            s = self.b.signal()
            params.append(self.b.place(Constant({s: 0}, label=f"argument %{p.name} = {rich_text(s)}"), (), RED, group="arguments"))
            param_signals.append(s)
            self.set(p, Wire(s, 0, RED))
        self.b.begin_block(self.fn.blocks[0].name)
        for op in self.fn.ops:
            self.set(op.result, self.op(op))
        assert self.fn.ret is not None
        result = self.get(self.fn.ret)
        return Compiled(
            self.fn.name,
            self.b.nl,
            params,
            param_signals,
            result,
            _depth(result),
            self.b.net_of(result.color).probe if isinstance(result, Wire) else None,
            self.names,
        )


def lower(fn: Function, io: dict[str, list[str]] | None = None, screen_width: int | None = None):
    """Compile ``fn``: a pipeline if it is one block, else a stepped circuit (M2)."""
    if fn.straight_line and not fn.arrays and not io:
        return _Lowerer(fn).run()
    from .cfg import lower_cfg

    return lower_cfg(fn, io, screen_width)
