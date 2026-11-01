"""Fewer, bigger blocks: if-conversion and straight-line block merging.

Every block costs two steps on top of its logic (the latch and the jump), so
small blocks are expensive. Two rewrites on the frontend IR remove them:

* **Merging.** A block that only branches to a block with no other
  predecessor absorbs it.
* **If-conversion.** In a triangle (``A -> T -> J``, ``A -> J``) or a diamond
  (``A -> T -> J``, ``A -> F -> J``) whose side blocks are small and only
  compute, load and store, the side blocks move into ``A``. Their values are
  computed whether or not the branch is taken (reads cannot fault in the
  circuit); a store gets a *guard*, the i1 condition under which it really
  writes; ``J``'s phis become selects.

``split_after_stores`` runs afterwards and splits again where a load must see
a store of the same block.
"""

from __future__ import annotations

from itertools import count

from .frontend import Block, Function, Op, Operand, Phi, Term, Value

MAX_SIDE_OPS = 32  # side blocks bigger than this keep their branch

_fresh = count()


def _preds(fn: Function) -> dict[str, list[str]]:
    preds: dict[str, list[str]] = {b.name: [] for b in fn.blocks}
    for b in fn.blocks:
        assert b.term is not None
        for t in b.term.targets + [d for _, d in b.term.cases]:
            preds[t].append(b.name)
    return preds


def _replace(fn: Function, old: Value, new: Operand) -> None:
    def sub(x):
        return new if x is old else x

    for b in fn.blocks:
        for p in b.phis:
            p.incoming = [(pred, sub(v)) for pred, v in p.incoming]
        for op in b.ops:
            op.args = [sub(a) for a in op.args]
            op.guard = sub(op.guard)
        if b.term is not None:
            b.term.cond, b.term.value = sub(b.term.cond), sub(b.term.value)


def _rename_pred(fn: Function, old: str, new: str) -> None:
    for b in fn.blocks:
        for p in b.phis:
            p.incoming = [(new if pred == old else pred, v) for pred, v in p.incoming]


def _new(a: Block, opcode: str, args: list[Operand], width: int, text: str) -> Value:
    v = Value(f"ifc{next(_fresh)}", width)
    a.ops.append(Op(opcode, args, v, text=f"%{v.name} = {text}"))
    return v


def _and(a: Block, x: Operand | None, y: Operand) -> Operand:
    if x is None:
        return y
    return _new(a, "and", [x, y], 1, "and i1 (if-conversion guard)")


def _merge(fn: Function, preds: dict[str, list[str]]) -> bool:
    for a in fn.blocks:
        t = a.term
        if t is None or t.kind != "br":
            continue
        b = next(x for x in fn.blocks if x.name == t.targets[0])
        if b is a or b is fn.blocks[0] or preds[b.name] != [a.name]:
            continue
        for p in b.phis:
            _replace(fn, p.result, p.incoming[0][1])
        a.ops += b.ops
        a.term = b.term
        fn.blocks.remove(b)
        _rename_pred(fn, b.name, a.name)
        return True
    return False


def _side(fn: Function, preds: dict[str, list[str]], name: str, a: Block) -> Block | None:
    """``name`` as a side block of ``a``: only reached from ``a``, small, and
    ending in a plain branch."""
    b = next(x for x in fn.blocks if x.name == name)
    ok = (
        b is not a
        and preds[name] == [a.name]
        and b.term is not None
        and b.term.kind == "br"
        and b.term.targets[0] not in (a.name, name)
        and len(b.ops) <= MAX_SIDE_OPS
        # Several vector ops are several blocks anyway: a branch skips them all.
        and sum(op.opcode == "clear" and op.predicate is not None for op in b.ops) <= 1
    )
    return b if ok else None


def _absorb(fn: Function, a: Block, side: Block, guard: Operand) -> None:
    for p in side.phis:
        _replace(fn, p.result, p.incoming[0][1])
    for op in side.ops:
        if op.opcode in ("store", "clear"):
            op.guard = _and(a, op.guard, guard) if op.guard is not None else guard
        a.ops.append(op)
    fn.blocks.remove(side)


def _convert(fn: Function, preds: dict[str, list[str]]) -> bool:
    for a in fn.blocks:
        t = a.term
        if t is None or t.kind != "condbr" or t.targets[0] == t.targets[1]:
            continue
        x, y = (_side(fn, preds, n, a) for n in t.targets)
        if x and y and x.term.targets[0] == y.term.targets[0]:  # diamond
            join, sides = x.term.targets[0], [(x, True), (y, False)]
        elif x and x.term.targets[0] == t.targets[1]:  # triangle, taken when true
            join, sides = t.targets[1], [(x, True)]
        elif y and y.term.targets[0] == t.targets[0]:  # triangle, taken when false
            join, sides = t.targets[0], [(y, False)]
        else:
            continue
        j = next(b for b in fn.blocks if b.name == join)
        if any(p.result.array is not None for p in j.phis):
            continue  # a pointer phi would need a select of pointers
        c = t.cond
        assert c is not None
        not_c = None
        if any(not taken for _, taken in sides):
            not_c = _new(a, "xor", [c, 1], 1, f"xor i1 %{getattr(c, 'name', c)}, true (if-conversion)")
        # Each phi of the join: value along each way in, as selects on c.
        via = {s.name: s for s, _ in sides}
        for p in j.phis:
            incoming = dict(p.incoming)
            if len(sides) == 2:
                vt, vf = incoming[x.name], incoming[y.name]
            else:
                side, taken = sides[0]
                vs, va = incoming[side.name], incoming[a.name]
                vt, vf = (vs, va) if taken else (va, vs)
            p.incoming = [(pred, v) for pred, v in p.incoming if pred not in via and pred != a.name]
            p.incoming.append((a.name, None))  # filled in after the side ops move
            p._pending = (vt, vf)  # type: ignore[attr-defined]
        for s, taken in sides:
            _absorb(fn, a, s, c if taken else not_c)
        for p in j.phis:
            vt, vf = p._pending  # type: ignore[attr-defined]
            del p._pending  # type: ignore[attr-defined]
            v = vt if vt is vf else _new(a, "select", [c, vt, vf], p.result.width, f"select (if-conversion of %{p.result.name})")
            p.incoming = [(pred, v if pred == a.name and val is None else val) for pred, val in p.incoming]
        a.term = Term("br", [join], text=f"br label %{join}  ; if-converted {t.text.splitlines()[0]}")
        return True
    return False


def _forces_equal(defs: dict[int, Op], c: Operand, holds: bool, t: Operand, f: Operand, depth: int = 0) -> bool:
    """Whether ``c`` being ``holds`` means ``t == f``."""
    if not isinstance(c, Value) or id(c) not in defs or depth > 8:
        return False
    op = defs[id(c)]
    a = op.args
    if op.opcode == "icmp" and op.predicate in ("eq", "ne") and {id(x) for x in a} == {id(t), id(f)}:
        return holds == (op.predicate == "eq")
    if op.opcode == "xor" and len(a) == 2 and a[1] == 1:
        return _forces_equal(defs, a[0], not holds, t, f, depth + 1)
    # and (select a, b, false): when it holds, both do; or (select a, true, b): when it fails, both do
    parts = None
    if op.opcode == "and" or (op.opcode == "select" and len(a) == 3 and a[2] == 0):
        parts = [a[0], a[1]] if holds else None
    if op.opcode == "or" or (op.opcode == "select" and len(a) == 3 and a[1] == 1):
        parts = [a[0], a[1] if op.opcode == "or" else a[2]] if not holds else None
    return bool(parts) and any(_forces_equal(defs, x, holds, t, f, depth + 1) for x in parts)


def fold_same_selects(fn: Function) -> Function:
    """``select c, t, f`` where c (or not c) forces t == f is just one side,
    as after if-converting ``if (nx != x) x = nx``: x is then nx either way."""
    defs = {id(op.result): op for b in fn.blocks for op in b.ops}
    for b in fn.blocks:
        for op in list(b.ops):
            if op.opcode != "select" or op.result.width == 1:
                continue
            c, t, f = op.args
            if _forces_equal(defs, c, False, t, f):
                _replace(fn, op.result, t)
            elif _forces_equal(defs, c, True, t, f):
                _replace(fn, op.result, f)
    return fn


def simplify_cfg(fn: Function) -> Function:
    """If-convert and merge blocks until nothing changes."""
    while True:
        preds = _preds(fn)
        if not (_merge(fn, preds) or _convert(fn, preds)):
            return fold_same_selects(fn)
