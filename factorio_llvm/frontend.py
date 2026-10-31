"""Read LLVM IR (via llvmlite) into the compiler's small dataflow IR.

Scope: one function of i1/i32 values, with branches, loops and fixed-size i32
arrays (local ``alloca`` or global, see :class:`Array`). i64 is accepted and
computed in 32 bits, since clang uses it for array indices; values that need
more than 32 bits wrap. Anything else raises :class:`Unsupported` with the
offending instruction.
"""

from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import llvmlite.binding as llvm

from .sim import wrap


class Unsupported(Exception):
    pass


@dataclass(eq=False)
class Array:
    """A fixed-size array of i32: an ``alloca`` or a global variable."""

    name: str
    size: int
    init: list[int]  # initial contents (zeros if none)
    readonly: bool = False  # a constant global: becomes a ROM
    external: bool = False  # declared, not defined: memory-mapped I/O (see memory.py)

    def __repr__(self) -> str:
        return f"@{self.name}[{self.size}]"


@dataclass(eq=False)
class Value:
    name: str
    width: int  # 1 or 32; pointers are 32 (an element index into ``array``)
    array: Array | None = None  # for pointers: the array they point into

    def __repr__(self) -> str:
        return f"%{self.name}:i{self.width}"


Operand = Value | int | Array  # an Array operand is a pointer to its element 0


@dataclass
class Op:
    opcode: str  # LLVM opcode, e.g. "add", "icmp", "select"; "gep" (pointer, index),
    # "load" (pointer), "store" (value, pointer), "clear" (array) are memory ops
    args: list[Operand]
    result: Value
    predicate: str | None = None  # icmp only
    text: str = ""
    guard: "Operand | None" = None  # store/clear: an i1; writes only when it is 1 (if-conversion)
    wave: int = 0  # store/clear: goes out this many steps after the block's first stores


@dataclass
class Phi:
    result: Value
    incoming: list[tuple[str, Operand]]  # (predecessor block name, value)
    text: str = ""


@dataclass
class Term:
    """A block terminator.

    kind "br": ``targets[0]``. kind "condbr": ``cond ? targets[0] : targets[1]``.
    kind "switch": ``cases`` (value, block) with ``targets[0]`` as the default.
    kind "ret": returns ``value``.
    """

    kind: str
    targets: list[str] = field(default_factory=list)
    cond: Operand | None = None
    cases: list[tuple[int, str]] = field(default_factory=list)
    value: Operand | None = None
    text: str = ""


@dataclass
class Block:
    name: str
    phis: list[Phi] = field(default_factory=list)
    ops: list[Op] = field(default_factory=list)
    term: Term | None = None


@dataclass
class Function:
    name: str
    params: list[Value]
    blocks: list[Block] = field(default_factory=list)
    arrays: list[Array] = field(default_factory=list)

    @property
    def straight_line(self) -> bool:
        return len(self.blocks) == 1

    @property
    def ops(self) -> list[Op]:
        assert self.straight_line
        return self.blocks[0].ops

    @property
    def ret(self) -> Operand | None:
        assert self.straight_line
        term = self.blocks[0].term
        assert term is not None
        return term.value


_WIDTHS = {"i1": 1, "i8": 32, "i32": 32, "i64": 32, "ptr": 32}


def _width(ty: str, where: str) -> int:
    if ty == "i8":
        raise Unsupported(f"type i8 is not supported yet: {where}")
    try:
        return _WIDTHS[ty]
    except KeyError:
        raise Unsupported(f"type {ty} is not supported yet (only i1, i32, i64 and pointers to arrays): {where}") from None


def _key(v) -> int:
    return ctypes.cast(v._ptr, ctypes.c_void_p).value


_ICMP = re.compile(r"\bicmp\s+(?:samesign\s+)?(\w+)")  # samesign only adds a promise
_INTRINSIC = re.compile(r"@llvm\.(\w+)\.i\d+\(")
INTRINSICS = {"smax", "smin", "umax", "umin", "abs"}
_CASE = re.compile(r"i\d+ (-?\d+), label")
_GLOBAL = re.compile(r"^@([\w.$-]+) = .*?\b(global|constant) (\[(\d+) x i32\]|i32) (.*?)(?:, align \d+)?$")
_EXTERN = re.compile(r"^@([\w.$-]+) = external .*?\bglobal (\[(\d+) x i32\]|i32)(?:, align \d+)?$")
_ALLOCA = re.compile(r"alloca (\[(\d+) x i32\]|i32)(?=,|$)")
_GEP = re.compile(r"getelementptr (?:inbounds |nuw |nusw )*(\[\d+ x i32\]|i32|i8), ptr")
_CONST_GEP = re.compile(r"ptr getelementptr (?:inbounds |nuw |nusw )*\(\[\d+ x i32\], ptr @([\w.$-]+), i64 0, i64 (\d+)\)")
# The same as a byte offset: newer clang folds a constant index into an i8 gep.
_CONST_GEP_I8 = re.compile(r"ptr getelementptr (?:inbounds |nuw |nusw )*\(i8, ptr @([\w.$-]+), i64 (\d+)\)")
_MEMSET = re.compile(r"@llvm\.memset\.")
_SKIPPED_CALLS = re.compile(r"@llvm\.(lifetime|assume|dbg)\.")


def _global_array(text: str) -> Array | None:
    m = _EXTERN.match(text)
    if m:
        name, _, n = m.groups()
        size = int(n) if n else 1
        return Array(name, size, [0] * size, external=True)
    m = _GLOBAL.match(text)
    if not m:
        return None
    name, kind, ty, n, init_text = m.groups()
    size = int(n) if n else 1
    if init_text.startswith("zeroinitializer"):
        init = [0] * size
    elif n:
        init = [wrap(int(x)) for x in re.findall(r"i32 (-?\d+)", init_text)]
    else:
        init = [wrap(int(init_text.split()[0]))]
    if len(init) != size:
        raise Unsupported(f"global initializer: {text}")
    return Array(name, size, init, readonly=kind == "constant")


def _check_whole_array(arr: Array, nbytes: int, where: str) -> None:
    if nbytes != 4 * arr.size:
        raise Unsupported(f"memset of part of an array: {where}")


def read_function(ir_text: str, name: str | None = None) -> Function:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    defined = [f for f in module.functions if not f.is_declaration]
    if name is None:
        mains = [f for f in defined if f.name == "main"]
        if mains:
            fn = mains[0]
        elif len(defined) == 1:
            fn = defined[0]
        else:
            raise Unsupported(f"module defines {len(defined)} functions and no main; pass the function name")
    else:
        fn = module.get_function(name)

    values: dict[int, Value] = {}
    counter = iter(range(1 << 30))
    arrays: dict[int, Array] = {}  # alloca instruction -> array
    globals_: dict[str, Array] = {}
    for g in module.global_variables:
        arr = _global_array(str(g).strip())
        if arr is not None:
            globals_[g.name] = arr

    def fresh(ref, where: str) -> Value:
        v = Value(ref.name or str(next(counter)), _width(str(ref.type), where))
        values[_key(ref)] = v
        return v

    params = [fresh(a, f"argument of @{fn.name}") for a in fn.arguments]
    if any(str(a.type) == "i64" for a in fn.arguments):
        raise Unsupported(f"i64 arguments are not supported (i64 is only for array indices): @{fn.name}")
    if any(str(a.type) == "ptr" for a in fn.arguments):
        raise Unsupported(f"pointer arguments are not supported (use a global or local array): @{fn.name}")
    out = Function(fn.name, params)

    blocks = list(fn.blocks)
    block_names: dict[int, str] = {}
    for i, b in enumerate(blocks):
        block_names[_key(b)] = b.name or f"bb{i}"

    # Name every result first: phis may refer to values defined later.
    for b in blocks:
        for ins in b.instructions:
            text = str(ins).strip()
            if ins.opcode == "alloca":
                m = _ALLOCA.search(text)
                if not m:
                    raise Unsupported(f"only i32 and [N x i32] locals are supported: {text}")
                n = int(m.group(2)) if m.group(2) else 1
                arrays[_key(ins)] = Array(ins.name or f"local{len(arrays)}", n, [0] * n)
            elif ins.opcode not in ("br", "switch", "ret", "store", "unreachable") and str(ins.type) != "void":
                fresh(ins, text)

    def operand(ref, where: str) -> Operand:
        if _key(ref) in arrays:
            return arrays[_key(ref)]
        if str(ref.type) == "ptr" and ref.is_constant:
            if ref.name in globals_:
                return globals_[ref.name]
            text = str(ref).strip()
            m = _CONST_GEP.fullmatch(text)
            index = int(m.group(2)) if m else None
            if m is None and (m := _CONST_GEP_I8.fullmatch(text)):
                if int(m.group(2)) % 4:
                    raise Unsupported(f"pointer into the middle of a word: {where}")
                index = int(m.group(2)) // 4
            if m and m.group(1) in globals_:
                # A constant element pointer: a gep at the point of use.
                arr = globals_[m.group(1)]
                p = Value(f"{arr.name}.{index}", 32, arr)
                out.blocks[-1].ops.append(Op("gep", [arr, index], p, text=text))
                return p
            raise Unsupported(f"pointer {ref}: {where}")
        if ref.is_constant:
            if str(ref).split()[-1] in ("undef", "poison"):
                return 0  # any value will do
            if str(ref.type) not in _WIDTHS:
                raise Unsupported(f"constant of type {ref.type}: {where}")
            v = ref.get_constant_value(signed_int=True)
            if not isinstance(v, int):
                raise Unsupported(f"non-integer constant: {where}")
            # llvmlite does not always sign-extend, so normalise here.
            return v & 1 if str(ref.type) == "i1" else wrap(v)
        try:
            return values[_key(ref)]
        except KeyError:
            raise Unsupported(f"operand {ref} is not an argument or instruction result: {where}") from None

    def target(ref) -> str:
        return block_names[_key(ref)]

    for b in blocks:
        block = Block(block_names[_key(b)])
        out.blocks.append(block)
        for ins in b.instructions:
            text = str(ins).strip()
            opc = ins.opcode
            refs = list(ins.operands)
            if opc == "phi":
                incoming = [(target(bb), operand(v, text)) for v, bb in zip(refs, ins.incoming_blocks)]
                block.phis.append(Phi(values[_key(ins)], incoming, text))
                continue
            if opc == "br":
                if len(refs) == 1:
                    block.term = Term("br", [target(refs[0])], text=text)
                else:  # operands are cond, false target, true target
                    block.term = Term("condbr", [target(refs[2]), target(refs[1])], operand(refs[0], text), text=text)
                break
            if opc == "switch":
                vals = [int(v) for v in _CASE.findall(text)]
                dests = [target(r) for r in refs[2:]]
                block.term = Term("switch", [target(refs[1])], operand(refs[0], text), list(zip(vals, dests)), text=text)
                break
            if opc == "ret":
                if not refs:  # void: the result reads 0
                    block.term = Term("ret", value=0, text=text)
                    break
                if str(refs[0].type) != "i32" and str(refs[0].type) != "i1":
                    raise Unsupported(f"only i32 and i1 results are supported: {text}")
                block.term = Term("ret", value=operand(refs[0], text), text=text)
                break
            if opc == "alloca":
                continue
            if opc == "call" and _SKIPPED_CALLS.search(text):
                continue
            if opc == "call" and _MEMSET.search(text):
                dst, val, size = (operand(r, text) for r in refs[:3])
                if not isinstance(dst, Array) or val != 0 or not isinstance(size, int):
                    raise Unsupported(f"only memset of a whole array to 0 is supported: {text}")
                _check_whole_array(dst, size, text)
                block.ops.append(Op("clear", [dst], Value(f"clear{next(counter)}", 0), text=text))
                continue
            if opc == "store":
                block.ops.append(
                    Op("store", [operand(refs[0], text), operand(refs[1], text)], Value(f"store{next(counter)}", 0), text=text)
                )
                continue
            if opc == "getelementptr":
                block.ops.append(Op("gep", _gep_args(text, [operand(r, text) for r in refs]), values[_key(ins)], text=text))
                continue
            if opc == "call":
                m = _INTRINSIC.search(text)
                if not m or m.group(1) not in INTRINSICS:
                    raise Unsupported(f"calls are not supported yet (inline them): {text}")
                opc, refs = m.group(1), refs[:-1]  # the last operand is the callee
            pred = None
            if opc == "icmp":
                m = _ICMP.search(text)
                assert m, text
                pred = m.group(1)
            if _key(ins) not in values:
                raise Unsupported(f"instruction not supported yet: {text}")
            out.blocks[-1].ops.append(Op(opc, [operand(o, text) for o in refs], values[_key(ins)], pred, text))
        if block.term is None:
            raise Unsupported(f"block {block.name} has an unsupported terminator")
    used = {id(a): a for a in [*arrays.values(), *globals_.values()]}
    out.arrays = [a for a in used.values() if _mentions(out, a)]
    _point_to_arrays(out)
    from .ifconv import simplify_cfg

    return split_after_stores(simplify_cfg(out))


def _gep_args(text: str, ops: list[Operand]) -> list[Operand]:
    """(base pointer, element index) of a getelementptr over i32 elements."""
    m = _GEP.search(text)
    if not m:
        raise Unsupported(f"only getelementptr over i32 arrays is supported: {text}")
    ty, base, idx = m.group(1), ops[0], ops[1:]
    if ty.startswith("[") and len(idx) == 2 and idx[0] == 0:
        return [base, idx[1]]
    if ty == "i32" and len(idx) == 1:
        return [base, idx[0]]
    if ty == "i8" and len(idx) == 1 and isinstance(idx[0], int) and idx[0] % 4 == 0:
        return [base, idx[0] // 4]
    raise Unsupported(f"getelementptr form not supported: {text}")


def _mentions(fn: Function, arr: Array) -> bool:
    return any(a is arr for b in fn.blocks for op in b.ops for a in op.args) or any(
        v is arr for b in fn.blocks for p in b.phis for _, v in p.incoming
    )


def _point_to_arrays(fn: Function) -> None:
    """Work out which array every pointer value points into."""

    def array_of(v: Operand) -> Array | None:
        if isinstance(v, Array):
            return v
        return v.array if isinstance(v, Value) else None

    changed = True
    while changed:
        changed = False
        for b in fn.blocks:
            found: list[tuple[Value, list[Operand]]] = [(p.result, [v for _, v in p.incoming]) for p in b.phis]
            found += [(op.result, op.args[:1]) for op in b.ops if op.opcode == "gep"]
            for result, sources in found:
                arrs = {id(a): a for a in map(array_of, sources) if a is not None}
                if len(arrs) > 1:
                    raise Unsupported(f"%{result.name} may point into different arrays")
                if arrs and result.array is None:
                    result.array = next(iter(arrs.values()))
                    changed = True
    for b in fn.blocks:
        for op in b.ops:
            ptr = op.args[0] if op.opcode in ("gep", "load") else op.args[1] if op.opcode == "store" else None
            if ptr is not None and array_of(ptr) is None:
                raise Unsupported(f"pointer does not point into a known array: {op.text}")


def _array_of(op: Op) -> Array | None:
    ptr = op.args[0] if op.opcode in ("load", "clear") else op.args[1]
    return ptr if isinstance(ptr, Array) else getattr(ptr, "array", None)


_Affine = tuple[frozenset[tuple[int, int]], int]  # ({(value id, coefficient)}, constant)


def _affine(v: Operand, defs: dict[int, Op], depth: int = 0) -> _Affine:
    """``v`` as a sum of opaque values plus a constant, looking through adds,
    disjoint ors, casts and geps."""
    if isinstance(v, int):
        return frozenset(), v
    if isinstance(v, Array):
        return frozenset(), 0
    op = defs.get(id(v))
    if op is not None and depth < 32:
        a = op.args
        add = op.opcode == "add" or (op.opcode == "or" and " disjoint " in op.text) or op.opcode == "gep"
        if add or op.opcode == "sub":
            (xs, xc), (ys, yc) = (_affine(x, defs, depth + 1) for x in a[:2])
            if op.opcode == "sub":
                ys, yc = frozenset((k, -c) for k, c in ys), -yc
            terms: dict[int, int] = {}
            for k, c in (*xs, *ys):
                terms[k] = terms.get(k, 0) + c
            return frozenset((k, c) for k, c in terms.items() if c), xc + yc
        if op.opcode in ("sext", "zext", "trunc", "freeze"):
            return _affine(a[0], defs, depth + 1)
    return frozenset({(id(v), 1)}), 0


def split_after_stores(fn: Function) -> Function:
    """Split blocks where a load must see an earlier store or clear; order the rest.

    Arrays change only after a block, so a new block starts before a load of
    an array the block already wrote or cleared. Stores and clears that may
    hit a word an earlier one of the block wrote go out a step later (a later
    *wave*); two stores to one array share a wave only if their addresses
    differ by a constant (``i`` and ``i + 1``, as unrolled loops produce).

    The block keeps its name for the part that runs first (branches go there);
    successors' phis are told about the part that now ends with the terminator.
    """
    defs = {id(op.result): op for b in fn.blocks for op in b.ops}
    taken = {b.name for b in fn.blocks}
    renamed: dict[str, str] = {}
    blocks: list[Block] = []
    for b in fn.blocks:
        piece = Block(b.name, b.phis)
        n = 0
        written: list[tuple[Array | None, _Affine | None, int]] = []  # this piece's stores (None: clear), wave

        def mine(op: Op) -> list[tuple[Array | None, _Affine | None, int]]:
            arr = _array_of(op)
            return [w for w in written if arr is None or w[0] is None or w[0] is arr]

        def must_split(op: Op) -> bool:
            return op.opcode == "load" and bool(mine(op))

        def wave(op: Op) -> int:
            """One step after every earlier store of this piece it may overwrite."""
            if op.opcode == "clear":
                clash = mine(op)
            else:
                addr = _affine(op.args[1], defs)
                clash = [w for w in mine(op) if w[1] is None or w[1][0] != addr[0] or w[1][1] == addr[1]]
            return max((w[2] + 1 for w in clash), default=0)

        for op in b.ops:
            if must_split(op):
                n += 1
                name = f"{b.name}.part{n}"
                while name in taken:  # unrolled loops have names like if.then.1 already
                    name += "_"
                taken.add(name)
                nxt = Block(name)
                piece.term = Term("br", [nxt.name], text=f"br label %{nxt.name}  ; before {op.text}")
                blocks.append(piece)
                piece, written = nxt, []
            piece.ops.append(op)
            if op.opcode in ("store", "clear"):
                op.wave = wave(op)
                written.append((_array_of(op), _affine(op.args[1], defs) if op.opcode == "store" else None, op.wave))
        piece.term = b.term
        blocks.append(piece)
        if piece.name != b.name:
            renamed[b.name] = piece.name
    for b in blocks:
        for p in b.phis:
            p.incoming = [(renamed.get(pred, pred), v) for pred, v in p.incoming]
    fn.blocks = blocks
    return fn


# Keep clang from producing what the circuit cannot run cheaply: vector code,
# unrolled loops, and loop exit values rewritten as wide closed forms (i33/i64).
CLANG_FLAGS = [
    "-fno-vectorize",
    "-fno-slp-vectorize",
    "-fno-unroll-loops",
    "-mllvm",
    "-replexitval=never",
]


def compile_c(source: str | Path, *, opt: str = "-O2", unroll: int = 1) -> str:
    """Compile a C file (path) or C source text to LLVM IR text with clang.

    ``unroll`` > 1 unrolls every loop that many times (``-unroll-count``);
    otherwise only loops with ``#pragma unroll`` are unrolled."""
    clang = shutil.which("clang")
    if clang is None:
        raise RuntimeError("clang not found on PATH")
    flags = CLANG_FLAGS
    if unroll > 1:
        flags = [f for f in CLANG_FLAGS if f != "-fno-unroll-loops"] + ["-funroll-loops", "-mllvm", f"-unroll-count={unroll}"]
    include = Path(__file__).parent / "include"  # factorio.h
    base = [clang, opt, *flags, "-I", str(include), "-S", "-emit-llvm", "-fno-discard-value-names", "-o", "-"]
    if isinstance(source, Path) or (isinstance(source, str) and source.endswith(".c") and Path(source).exists()):
        return subprocess.run([*base, str(source)], check=True, capture_output=True, text=True).stdout
    return subprocess.run([*base, "-x", "c", "-"], input=source, check=True, capture_output=True, text=True).stdout


def screen_width_of(ir_text: str) -> int | None:
    """The ``screen_width`` constant factorio.h defines from SCREEN_W, if any.

    Read through llvmlite like every other global: LLVM parses and verifies
    the module, and we read the global's initializer as an array of one word."""
    module = llvm.parse_assembly(ir_text)
    for g in module.global_variables:
        if g.name == "screen_width":
            arr = _global_array(str(g).strip())
            if arr is None or not arr.readonly or arr.size != 1:
                raise Unsupported(f"screen_width must be a constant int: {str(g).strip()}")
            return arr.init[0]
    return None
