"""Lazy values that several IR instructions can share one combinator for.

The lowerer gives most instructions a combinator and a tick each. Many chains
need less, because one combinator does more than one IR instruction:

* a wire adds every value put on the same signal, so ``a + b - c + 5`` is one
  tick: one arithmetic per term, all writing the result signal (:class:`Lin`);
* a decider tests an OR of AND groups of comparisons, so any boolean mix of
  comparisons (``&&``, ``||``, ``!``, ``select`` on ``i1``) is one tick
  (:class:`Bool`);
* deciders writing to one wire under exclusive conditions add up to a
  piecewise value, so a chain of ``select``/``min``/``max`` (a clamp, say) is
  one gate and one rename: two ticks (:class:`Pieces`).

Instructions return these forms instead of placing combinators; a form is
placed (and its result cached) only when something needs it as a signal.
Comparisons and copies can only read values already on a wire, so a form's
leaves are always :class:`~factorio_llvm.lower.Wire` values or constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Union

from .sim import compare, wrap

if TYPE_CHECKING:
    from .lower import Wire

MAX_TERMS = 12  # a Lin's terms (one arithmetic each)
MAX_CLAUSES = 16  # a Bool's AND groups
MAX_ATOMS = 32  # comparisons in one decider
MAX_PIECES = 8  # a Pieces' cases (one decider each)

_NEGATE = {"=": "!=", "!=": "=", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
_UCMP = {"ult": "<", "ule": "<=", "ugt": ">", "uge": ">="}
MIRROR = {"=": "=", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


@dataclass(frozen=True)
class Atom:
    """``a cmp b``, one condition of a decider."""

    a: "Wire"
    cmp: str
    b: "Wire | int"

    def negate(self) -> "Atom":
        return Atom(self.a, _NEGATE[self.cmp], self.b)


Clause = tuple[Atom, ...]  # AND of its atoms


@dataclass(eq=False)
class Lin:
    """``sum(c * w) + const`` over wires ``w``."""

    terms: dict["Wire", int]
    const: int = 0
    memo: "Wire | None" = None


@dataclass(eq=False)
class Bool:
    """An OR of AND groups of comparisons: 1 if it holds, else 0."""

    clauses: tuple[Clause, ...]
    memo: "Wire | None" = None


@dataclass(eq=False)
class Pieces:
    """``value`` of the one case whose guard holds (0 if none); the guards exclude each other."""

    cases: list[tuple[Bool, "Wire | int"]] = field(default_factory=list)
    memo: "Wire | None" = None


Lazy = Union[Lin, Bool, Pieces]


class TooBig(Exception):
    """A form would need more than one combinator's worth of conditions."""


# ---- linear forms


def lin_of(x: "Wire | int | Lin") -> Lin:
    if isinstance(x, Lin):
        return x
    if isinstance(x, int):
        return Lin({}, x)
    return Lin({x: 1})


def lin_add(a: Lin, b: Lin, scale: int = 1) -> "Lin | int":
    terms = dict(a.terms)
    for w, c in b.terms.items():
        terms[w] = wrap(terms.get(w, 0) + scale * c)
    terms = {w: c for w, c in terms.items() if c}
    if len(terms) > MAX_TERMS:
        raise TooBig
    const = wrap(a.const + scale * b.const)
    return Lin(terms, const) if terms else const


def lin_scale(a: Lin, k: int) -> "Lin | int":
    return lin_add(Lin({}), a, k) if k else 0


def simple(x: "Lin | int") -> "Wire | Lin | int":
    """A Lin that is just one wire, as that wire."""
    if isinstance(x, Lin) and not x.const and len(x.terms) == 1:
        (w, c), = x.terms.items()
        if c == 1:
            return w
    return x


# ---- boolean forms (constants are plain bools)


def atom(a: "Wire | int", cmp: str, b: "Wire | int") -> "Bool | bool":
    if isinstance(a, int) and isinstance(b, int):
        return compare(cmp, a, b)
    if isinstance(a, int):
        a, b, cmp = b, a, MIRROR[cmp]
    return Bool(((Atom(a, cmp, b),),))


def unsigned_atom(a: "Wire | int", pred: str, b: "Wire | int") -> "Bool | bool | None":
    """``a pred b`` for ult/ule/ugt/uge when one side is a constant, else None."""
    if isinstance(a, int) and isinstance(b, int):
        return compare(_UCMP[pred], a & 0xFFFFFFFF, b & 0xFFFFFFFF)
    if isinstance(a, int):
        a, b, pred = b, a, {"ult": "ugt", "ule": "uge", "ugt": "ult", "uge": "ule"}[pred]
    if not isinstance(b, int):
        return None
    if pred in ("ugt", "uge"):
        lt = unsigned_atom(a, "ult" if pred == "uge" else "ule", b)
        return bnot(lt) if lt is not None else None
    if pred == "ule":
        if b == -1:
            return True
        b, pred = wrap(b + 1), "ult"
    # a <u b
    if b == 0:
        return False
    if b > 0:
        return band(atom(a, ">=", 0), atom(a, "<", b))
    return bor(atom(a, ">=", 0), atom(a, "<", b))


def _check(clauses: tuple[Clause, ...]) -> tuple[Clause, ...]:
    if len(clauses) > MAX_CLAUSES or sum(len(c) for c in clauses) > MAX_ATOMS:
        raise TooBig
    return clauses


INT_MIN, INT_MAX = -(2**31), 2**31 - 1


def _simplify(clause: Clause) -> Clause | None:
    """``clause`` with each wire's bounds against constants merged into at most
    two comparisons; None if it can never hold."""
    bounds: dict[object, list[int]] = {}
    other: list[Atom] = []
    nots: dict[object, set[int]] = {}
    for x in clause:
        if not isinstance(x.b, int) or x.cmp == "!=":
            if isinstance(x.b, int):
                nots.setdefault(x.a, set()).add(x.b)
            else:
                other.append(x)
            continue
        lo, hi = bounds.setdefault(x.a, [INT_MIN, INT_MAX])
        k = x.b
        if x.cmp in ("=", ">=", ">"):
            lo = max(lo, k + (x.cmp == ">"))
        if x.cmp in ("=", "<=", "<"):
            hi = min(hi, k - (x.cmp == "<"))
        bounds[x.a] = [lo, hi]
    out: list[Atom] = []
    for w, (lo, hi) in bounds.items():
        excluded = nots.pop(w, set())
        while lo <= hi and lo in excluded:
            lo += 1
        while lo <= hi and hi in excluded:
            hi -= 1
        if lo > hi:
            return None
        if lo == hi:
            out.append(Atom(w, "=", lo))
            continue
        if lo > INT_MIN:
            out.append(Atom(w, ">=", lo))
        if hi < INT_MAX:
            out.append(Atom(w, "<=", hi))
        out += [Atom(w, "!=", k) for k in sorted(excluded) if lo < k < hi]
    for w, ks in nots.items():
        out += [Atom(w, "!=", k) for k in sorted(ks)]
    out += list(dict.fromkeys(other))
    return tuple(out)


def _clauses(clauses) -> "Bool | bool":
    kept = []
    for c in clauses:
        s = _simplify(c)
        if s is None:
            continue
        if not s:
            return True
        kept.append(s)
    if not kept:
        return False
    return Bool(_check(tuple(dict.fromkeys(kept))))


def bor(a: "Bool | bool", b: "Bool | bool") -> "Bool | bool":
    if a is True or b is True:
        return True
    if a is False:
        return b
    if b is False:
        return a
    return _clauses(a.clauses + b.clauses)


def band(a: "Bool | bool", b: "Bool | bool") -> "Bool | bool":
    if a is False or b is False:
        return False
    if a is True:
        return b
    if b is True:
        return a
    if len(a.clauses) * len(b.clauses) > 4 * MAX_CLAUSES:
        raise TooBig
    return _clauses([x + y for x in a.clauses for y in b.clauses])


def bnot(a: "Bool | bool") -> "Bool | bool":
    if isinstance(a, bool):
        return not a
    # De Morgan: not (OR of ANDs) = AND of (OR of negated atoms), multiplied out.
    out: "Bool | bool" = True
    for clause in a.clauses:
        out = band(out, _clauses([(x.negate(),) for x in clause]))
    return out


# ---- piecewise forms


def cases_of(x: "Wire | int | Pieces") -> list[tuple["Bool | bool", "Wire | int"]]:
    if isinstance(x, Pieces):
        return list(x.cases)
    return [(True, x)]


def pieces(cases: list[tuple["Bool | bool", "Wire | int"]]) -> "Pieces | Wire | int":
    merged: dict[object, "Bool | bool"] = {}
    for g, v in cases:
        if g is False:
            continue
        merged[v] = bor(merged[v], g) if v in merged else g
    if not merged:
        return 0
    if len(merged) == 1:
        (v, g), = merged.items()
        if g is True or v == 0:
            return v
    if len(merged) > MAX_PIECES:
        raise TooBig
    out = Pieces()
    for v, g in merged.items():
        assert g is not True
        out.cases.append((g, v))  # type: ignore[arg-type]
    return out
