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
from .fuse import (
    MAX_TERMS, Atom, Bool, Lin, Ors, Pieces, TooBig, atom, band, bnot, bor, cases_of, lin_add, lin_of, lin_scale, pieces, simple, unsigned_atom,
)
from .signals import rich_text
from .netlist import GREEN, RED, Arithmetic, Color, Cond, Connector, Constant, Decider, Entity, Message, Net, Netlist, Out, Panel, Sig
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
    early: int | None = None  # ticks along the shortest path from a changing input (None: = depth)
    vary: int | None = None  # ticks along the longest path from a changing input (None: = depth)
    reads: tuple = ()  # (array, lo, hi): memory read between lo and hi ticks before this value

    @property
    def lo(self) -> int:
        return self.depth if self.early is None else self.early

    @property
    def hi(self) -> int:
        return self.depth if self.vary is None else self.vary


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
        self.pools: dict[int, list[str]] = {}

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
        self.pools[id(self.local)] = self._local_free  # shared: a block's signals stay unique
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

    def tag(self, terminal: Connector, text: str, icon: str, group: str | None = None) -> Panel:
        """A display panel naming ``terminal``, on a green wire so it is placed next to it."""
        # A wired panel shows only its messages, so give it one that always holds
        # (signal-dot is never on the wire, so it reads 0).
        always = Message(Cond(Sig("signal-dot", frozenset({GREEN})), "=", 0), icon, text)
        panel = self.place(Panel([always], text=text, icon=icon, always_show=True, label=text), group=group)
        self.nl.net(GREEN, f"tag {text}").attach(terminal, panel.inp)
        return panel


def _operand(v: Lowered) -> Sig | int:
    return v if isinstance(v, int) else Sig(v.signal, frozenset({v.color}))


def _colors(*vs: Lowered) -> set[Color]:
    return {v.color for v in vs if isinstance(v, Wire)}


def _depth(*vs: Lowered) -> int:
    return max((v.depth for v in vs if isinstance(v, Wire)), default=0)


STABLE = 10**9  # ``early`` of a value that does not change while a block runs


def _early(*vs: Lowered) -> int:
    """The soonest a change of the block's inputs can reach any of ``vs``."""
    return min((v.lo for v in vs if isinstance(v, Wire)), default=STABLE)


def _reads(*vs: Lowered, lo: int, hi: int | None = None) -> tuple:
    """The memory reads behind ``vs``, ``lo`` (to ``hi``) ticks further on."""
    hi = lo if hi is None else hi
    out: dict[str, tuple[int, int]] = {}
    for v in vs:
        for a, x, y in v.reads if isinstance(v, Wire) else ():
            px, py = out.get(a, (x, y))
            out[a] = (min(px, x), max(py, y))
    return tuple((a, x + lo, y + hi) for a, (x, y) in sorted(out.items()))


def _vary(*vs: Lowered) -> int:
    """The latest a change of the block's inputs can reach any of ``vs``."""
    return max((v.hi for v in vs if isinstance(v, Wire) and v.lo < STABLE), default=-STABLE)


class _Lowerer:
    def __init__(self, fn: Function) -> None:
        self.fn = fn
        self.b = _Builder()
        self.env: dict[int, Lowered] = {}
        # Balance: delay the operands of every combinator so that all paths
        # from a changing input reach it at once (early == vary), for loops
        # whose iterations will overlap (cfg.py).
        self.balance = False
        self._delays: dict[tuple[int, str, object, int], Wire] = {}
        self.names: dict[str, str] = {}

    def get_raw(self, a: Operand):
        """``a`` as lowered so far: possibly a lazy form (see fuse.py)."""
        return a if isinstance(a, int) else self.env[id(a)]

    def get(self, a: Operand) -> Lowered:
        """``a`` as a signal (or constant), placing its lazy form if need be."""
        return self.real(self.get_raw(a))

    # ---- lazy forms (fuse.py)

    def real(self, x):
        if isinstance(x, bool):
            return int(x)
        if not isinstance(x, (Lin, Bool, Pieces, Ors)):
            return x
        if x.memo is None:
            x.memo = self._place_ors(x) if isinstance(x, Ors) else self._place_lin(x) if isinstance(x, Lin) else self._place_bool(x) if isinstance(x, Bool) else self._place_pieces(x)
        return x.memo

    def _lin(self, x) -> Lin:
        return x if isinstance(x, Lin) else lin_of(self.real(x))

    def _boolish(self, x):
        """An i1 value as a Bool (or a Python bool)."""
        if isinstance(x, (bool, Bool)):
            return x
        if isinstance(x, int):
            return bool(x)
        w = self.real(x)
        b = atom(w, "!=", 0)
        b.memo = w  # already 0/1
        return b

    def _caseable(self, x):
        return x if isinstance(x, (int, Wire, Pieces)) else self.real(x)

    def _delayed(self, w: Wire, d: int) -> Wire:
        """``w``, ``d`` ticks later, through a chain of copies."""
        if d <= 0:
            return w
        key = (id(self.b.local), w.signal, w.color, d)  # local signals repeat across blocks
        if key not in self._delays:
            prev = self._delayed(w, d - 1)
            out = self.b.local_signal()
            self.b.place(Arithmetic(_operand(prev), "+", 0, out, label=f"{w.signal} delayed {d}"), _colors(prev), GREEN)
            self._delays[key] = Wire(out, prev.depth + 1, early=prev.lo + 1, vary=prev.hi + 1, reads=_reads(prev, lo=1))
        return self._delays[key]

    def _align(self, *vs) -> dict:
        """With ``balance``: each changing wire among ``vs``, delayed to arrive
        with the latest of them."""
        if not self.balance:
            return {}
        ws = [v for v in vs if isinstance(v, Wire) and v.lo < STABLE]
        if not ws:
            return {}
        target = max(w.hi for w in ws)
        return {w: self._delayed(w, target - w.lo) for w in ws if w.lo < target}

    def _aligned_bool(self, b: Bool, m: dict) -> Bool:
        if not m:
            return b
        get = lambda x: m.get(x, x) if isinstance(x, Wire) else x
        return Bool(tuple(tuple(Atom(get(at.a), at.cmp, get(at.b)) for at in cl) for cl in b.clauses))

    def _conds(self, b: Bool) -> list[Cond]:
        return [
            Cond(_operand(at.a), at.cmp, _operand(at.b), join="or" if j == 0 and i > 0 else "and")
            for i, clause in enumerate(b.clauses)
            for j, at in enumerate(clause)
        ]

    def _leaves(self, b: Bool) -> list[Lowered]:
        return [x for clause in b.clauses for at in clause for x in (at.a, at.b)]

    def _place_lin(self, x: Lin) -> Wire:
        m = self._align(*x.terms)
        if m:
            x = Lin({m.get(w, w): c for w, c in x.terms.items()}, x.const)
        out = self.b.local_signal()
        terms = list(x.terms.items())
        k = x.const
        label = f"{out} = linear sum"
        if len(terms) == 2 and k == 0 and {c for _, c in terms} <= {1, -1} and any(c == 1 for _, c in terms):
            (a, ca), (b, cb) = terms if terms[0][1] == 1 else terms[::-1]
            self.b.place(Arithmetic(_operand(a), "+" if cb == 1 else "-", _operand(b), out, label=label), _colors(a, b), GREEN)
        elif len(terms) == 1 and (k == 0 or terms[0][1] == 1):
            (a, c), = terms
            self.b.place(Arithmetic(_operand(a), "*" if c != 1 else "+", c if c != 1 else k, out, label=label), _colors(a), GREEN)
        else:
            for w, c in terms:
                self.b.place(Arithmetic(_operand(w), "*" if c != 1 else "+", c if c != 1 else 0, out, label=label), _colors(w), GREEN)
            if k:
                self.b.place(Constant({out: k}, label=f"{label} (+ {k})"), (), GREEN)
        return Wire(out, _depth(*x.terms) + 1, early=_early(*x.terms) + 1, vary=_vary(*x.terms) + 1, reads=_reads(*x.terms, lo=1))

    def _place_ors(self, x: Ors) -> Wire:
        level: list[Lowered] = list(x.parts)
        while len(level) > 1:  # a balanced tree of ORs
            level = [self.arith("OR", level[i], level[i + 1], "or") if i + 1 < len(level) else level[i] for i in range(0, len(level), 2)]
        return level[0]

    def _place_bool(self, x: Bool) -> Wire:
        x = self._aligned_bool(x, self._align(*self._leaves(x)))
        out = self.b.local_signal()
        leaves = self._leaves(x)
        self.b.place(Decider(self._conds(x), [Out(out, copy=False, constant=1)], label=f"{out} = condition"), _colors(*leaves), GREEN)
        return Wire(out, _depth(*leaves) + 1, early=_early(*leaves) + 1, vary=_vary(*leaves) + 1, reads=_reads(*leaves, lo=1))

    def _place_pieces(self, x: Pieces) -> Lowered:
        out = self.b.local_signal()
        cases = [(g, v) for g, v in x.cases if v != 0]
        m = self._align(*(leaf for g, _ in cases for leaf in self._leaves(g)), *(v for _, v in cases))
        if m:
            cases = [(self._aligned_bool(g, m), m.get(v, v) if isinstance(v, Wire) else v) for g, v in cases]
        guards = [leaf for g, _ in cases for leaf in self._leaves(g)]
        label = f"{out} = case"
        def guard_depth(g: Bool) -> int:
            return g.memo.depth if g.memo is not None else _depth(*self._leaves(g)) + 1

        # Either multiply each value by its placed guard (0 or 1) onto the result
        # signal, or gate the values onto a private net and rename: whichever is
        # ready first.
        by_product = max(_depth(*self._leaves(g)) + 1 if isinstance(v, int) else max(_depth(v), guard_depth(g)) + 1 for g, v in cases)
        by_rename = _depth(*guards, *(v for _, v in cases)) + 2
        if by_product <= by_rename and not self.balance:  # products: paths of 1 and 2
            for g, v in cases:
                if isinstance(v, int):
                    self.b.place(Decider(self._conds(g), [Out(out, copy=False, constant=v)], label=label), _colors(*self._leaves(g)), GREEN)
                else:
                    flag = self.real(g)
                    self.b.place(Arithmetic(_operand(v), "*", _operand(flag), out, label=f"{label} (value * guard)"), _colors(v, flag), GREEN)
            return Wire(out, by_product, early=_early(*guards, *(v for _, v in cases)) + 1, vary=_vary(*guards, *(v for _, v in cases)) + 2, reads=_reads(*guards, *(v for _, v in cases), lo=1, hi=2))
        # Deciders cannot rename: each case passes its value under its own name
        # onto a private net, and Each + 0 sums that net into the result.
        private = self.b.nl.net(GREEN, f"cases {out}")
        for g, v in cases:
            o = Out(v.signal, networks=v.color) if isinstance(v, Wire) else Out(PRIVATE_CONSTANT, copy=False, constant=v)
            self.b.place(Decider(self._conds(g), [o], label=f"{label} (pass)"), _colors(*self._leaves(g), v), private)
        rename = Arithmetic(Sig(EACH, frozenset({GREEN})), "+", 0, out, label=f"{label} (rename)")
        self.b.place(rename, (), GREEN)
        private.attach(rename.inp)
        return Wire(out, _depth(*guards, *(v for _, v in cases)) + 2, early=_early(*guards, *(v for _, v in cases)) + 2, vary=_vary(*guards, *(v for _, v in cases)) + 2, reads=_reads(*guards, *(v for _, v in cases), lo=1, hi=2))

    def lazy_op(self, op: Op):
        """``op`` as a lazy form, or NotImplemented to place it the plain way."""
        raw = [self.get_raw(a) for a in op.args]
        if not all(isinstance(x, (int, Wire, Lin, Bool, Pieces, Ors)) for x in raw):
            return NotImplemented
        try:
            r = self._lazy(op, raw)
        except TooBig:
            # Too many conditions for one decider: place the operands first.
            try:
                r = self._lazy(op, [self.real(x) for x in raw])
            except TooBig:
                return NotImplemented
        if isinstance(r, bool):
            return int(r)
        return simple(r) if isinstance(r, Lin) else r

    def _cmp(self, pred: str, a, b):
        """``a pred b`` (icmp predicate) as a Bool, over the cases of each side."""
        out = False
        ca, cb = cases_of(self._caseable(a)), cases_of(self._caseable(b))
        if len(ca) * len(cb) > 8:
            raise TooBig
        for g, v in ca:
            for h, u in cb:
                c = atom(v, _ICMP[pred], u) if pred in _ICMP else unsigned_atom(v, pred, u)
                if c is None:
                    raise TooBig
                out = bor(out, band(band(g, h), c))
        return out

    def _lazy(self, op: Op, raw: list):
        opc, w = op.opcode, op.result.width
        widths = [a.width if isinstance(a, Value) else None for a in op.args]
        if opc in ("zext", "freeze") or (opc in ("trunc", "sext") and widths[0] == w):
            return raw[0]
        if opc == "sext" and widths[0] == 1:  # i1 true is -1
            return lin_scale(self._lin(raw[0]), -1)
        if w == 1 and opc in ("and", "or", "xor"):
            a, b = (self._boolish(x) for x in raw)
            if opc == "and":
                return band(a, b)
            if opc == "or":
                return bor(a, b)
            return bor(band(a, bnot(b)), band(bnot(a), b))
        if w > 1 and opc == "or" and " or disjoint " not in f" {op.text} " and not any(isinstance(x, int) for x in raw):
            parts = [p for x in raw for p in (x.parts if isinstance(x, Ors) else [self.real(x)])]
            if len(parts) > MAX_TERMS:
                raise TooBig
            return Ors(parts)
        if opc == "icmp":
            pred = op.predicate or ""
            a, b = raw
            if pred in ("eq", "ne") and isinstance(a, Ors) and b == 0:
                # x | y | z != 0 is x != 0 || y != 0 || z != 0
                tests = [atom(p, "!=", 0) for p in a.parts]
                any_set = tests[0]
                for t in tests[1:]:
                    any_set = bor(any_set, t)
                return any_set if pred == "ne" else bnot(any_set)
            if widths[0] == 1:
                if pred not in ("eq", "ne"):
                    return NotImplemented
                a, b = (self._boolish(x) for x in raw)
                same = bor(band(a, b), band(bnot(a), bnot(b)))
                return same if pred == "eq" else bnot(same)
            if pred in ("eq", "ne") and (isinstance(a, Lin) or isinstance(b, Lin)):
                # Equality survives wrapping, so a + k == b + j is a == b + (j - k).
                d = lin_add(self._lin(a), self._lin(b), -1)
                cmp = _ICMP[pred]
                if isinstance(d, int):
                    return compare(cmp, d, 0)
                terms = list(d.terms.items())
                if len(terms) == 1 and terms[0][1] in (1, -1):
                    (t, c), = terms
                    return atom(t, cmp, wrap(-d.const * c))
                if len(terms) == 2 and not d.const and terms[0][1] == -terms[1][1] and terms[0][1] in (1, -1):
                    return atom(terms[0][0], cmp, terms[1][0])
            return self._cmp(pred, a, b)
        if opc == "select":
            c = self._boolish(raw[0])
            if w == 1:
                t, f = (self._boolish(x) for x in raw[1:])
                if f is False or t is True:  # c && t, c || f
                    return bor(band(c, t), f)
                return bor(band(c, t), band(bnot(c), f))
            t, f = (cases_of(self._caseable(x)) for x in raw[1:])
            return pieces([(band(c, g), v) for g, v in t] + [(band(bnot(c), g), v) for g, v in f])
        if opc in ("smax", "smin", "umax", "umin"):
            pred = {"smax": "sgt", "smin": "slt", "umax": "ugt", "umin": "ult"}[opc]
            ca, cb = (cases_of(self._caseable(x)) for x in raw)
            out = []
            for g, v in ca:
                for h, u in cb:
                    first = self._cmp(pred, v, u)
                    gh = band(g, h)
                    out += [(band(gh, first), v), (band(gh, bnot(first)), u)]
            return pieces(out)
        if w > 1 and opc == "or" and " or disjoint " in f" {op.text} ":
            opc = "add"  # no common bits: the same as adding
        if w > 1 and opc in ("add", "sub"):
            return lin_add(self._lin(raw[0]), self._lin(raw[1]), 1 if opc == "add" else -1)
        if w > 1 and opc == "mul" and (isinstance(raw[0], int) or isinstance(raw[1], int)):
            k, x = (raw[0], raw[1]) if isinstance(raw[0], int) else (raw[1], raw[0])
            return lin_scale(self._lin(x), k)
        if w > 1 and opc == "shl" and isinstance(raw[1], int):
            return lin_scale(self._lin(raw[0]), wrap(1 << (raw[1] & 31)))
        return NotImplemented

    def set(self, v: Value, x: Lowered) -> None:
        self.env[id(v)] = x
        if isinstance(x, Wire):
            self.names.setdefault(v.name, x.signal)

    # ---- emitters

    def arith(self, op: str, a: Lowered, b: Lowered, label: str) -> Lowered:
        if isinstance(a, int) and isinstance(b, int):
            return arith(op, a, b)
        m = self._align(a, b)
        a, b = (m.get(x, x) if isinstance(x, Wire) else x for x in (a, b))
        out = self.b.local_signal()
        self.b.place(Arithmetic(_operand(a), op, _operand(b), out, label=label), _colors(a, b), GREEN)
        return Wire(out, _depth(a, b) + 1, early=_early(a, b) + 1, vary=_vary(a, b) + 1, reads=_reads(a, b, lo=1))

    def compare(self, cmp: str, a: Lowered, b: Lowered, label: str) -> Lowered:
        if isinstance(a, int) and isinstance(b, int):
            return int(compare(cmp, a, b))
        if isinstance(a, int):
            a, b, cmp = b, a, _MIRROR[cmp]
        assert isinstance(a, Wire)
        m = self._align(a, b)
        a, b = m.get(a, a), m.get(b, b) if isinstance(b, Wire) else b
        out = self.b.local_signal()
        dec = Decider([Cond(_operand(a), cmp, _operand(b))], [Out(out, copy=False, constant=1)], label=label)
        self.b.place(dec, _colors(a, b), GREEN)
        return Wire(out, _depth(a, b) + 1, early=_early(a, b) + 1, vary=_vary(a, b) + 1, reads=_reads(a, b, lo=1))

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
        m = self._align(a, b, t, f)
        a, b, t, f = (m.get(x, x) if isinstance(x, Wire) else x for x in (a, b, t, f))
        out = self.b.local_signal()
        cond = [Cond(_operand(a), cmp, _operand(b))]
        if isinstance(t, int) and isinstance(f, int):
            dec = Decider(cond, [Out(out, copy=False, constant=t)], [Out(out, copy=False, constant=f)], label=label)
            self.b.place(dec, _colors(a, b), GREEN)
            return Wire(out, _depth(a, b) + 1, early=_early(a, b) + 1, vary=_vary(a, b) + 1, reads=_reads(a, b, lo=1))

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
        return Wire(out, _depth(a, b, t, f) + 2, early=_early(a, b, t, f) + 2, vary=_vary(a, b, t, f) + 2, reads=_reads(a, b, t, f, lo=2))

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

    def op(self, op: Op):
        lazy = self.lazy_op(op)
        if lazy is not NotImplemented:
            return lazy
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
            self.b.tag(params[-1].out, f"argument %{p.name}", s, group="arguments")
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
