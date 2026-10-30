"""Read LLVM IR (via llvmlite) into the compiler's small dataflow IR.

M1 scope: one function, one basic block, i1/i32 values, no memory.
Anything else raises :class:`Unsupported` with the offending instruction.
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
class Value:
    name: str
    width: int  # 1 or 32

    def __repr__(self) -> str:
        return f"%{self.name}:i{self.width}"


Operand = Value | int


@dataclass
class Op:
    opcode: str  # LLVM opcode, e.g. "add", "icmp", "select"
    args: list[Operand]
    result: Value
    predicate: str | None = None  # icmp only
    text: str = ""


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


_WIDTHS = {"i1": 1, "i32": 32}


def _width(ty: str, where: str) -> int:
    try:
        return _WIDTHS[ty]
    except KeyError:
        raise Unsupported(f"type {ty} is not supported yet (only i1 and i32): {where}") from None


def _key(v) -> int:
    return ctypes.cast(v._ptr, ctypes.c_void_p).value


_ICMP = re.compile(r"\bicmp\s+(\w+)")
_INTRINSIC = re.compile(r"@llvm\.(\w+)\.i\d+\(")
INTRINSICS = {"smax", "smin", "umax", "umin", "abs"}
_CASE = re.compile(r"i\d+ (-?\d+), label")


def read_function(ir_text: str, name: str | None = None) -> Function:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    defined = [f for f in module.functions if not f.is_declaration]
    if name is None:
        if len(defined) != 1:
            raise Unsupported(f"module defines {len(defined)} functions; pass the function name")
        fn = defined[0]
    else:
        fn = module.get_function(name)

    values: dict[int, Value] = {}
    counter = iter(range(1 << 30))

    def fresh(ref, where: str) -> Value:
        v = Value(ref.name or str(next(counter)), _width(str(ref.type), where))
        values[_key(ref)] = v
        return v

    params = [fresh(a, f"argument of @{fn.name}") for a in fn.arguments]
    out = Function(fn.name, params)

    blocks = list(fn.blocks)
    block_names: dict[int, str] = {}
    for i, b in enumerate(blocks):
        block_names[_key(b)] = b.name or f"bb{i}"

    # Name every result first: phis may refer to values defined later.
    for b in blocks:
        for ins in b.instructions:
            if ins.opcode not in ("br", "switch", "ret", "store", "unreachable") and str(ins.type) != "void":
                fresh(ins, str(ins).strip())

    def operand(ref, where: str) -> Operand:
        if ref.is_constant:
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
                if len(refs) != 1:
                    raise Unsupported(f"void return: {text}")
                block.term = Term("ret", value=operand(refs[0], text), text=text)
                break
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
    return out


# Keep clang from producing what the circuit cannot run cheaply: vector code,
# unrolled loops, and loop exit values rewritten as wide closed forms (i33/i64).
CLANG_FLAGS = [
    "-fno-vectorize",
    "-fno-slp-vectorize",
    "-fno-unroll-loops",
    "-mllvm",
    "-replexitval=never",
]


def compile_c(source: str | Path, *, opt: str = "-O2") -> str:
    """Compile a C file (path) or C source text to LLVM IR text with clang."""
    clang = shutil.which("clang")
    if clang is None:
        raise RuntimeError("clang not found on PATH")
    base = [clang, opt, *CLANG_FLAGS, "-S", "-emit-llvm", "-fno-discard-value-names", "-o", "-"]
    if isinstance(source, Path) or (isinstance(source, str) and source.endswith(".c") and Path(source).exists()):
        return subprocess.run([*base, str(source)], check=True, capture_output=True, text=True).stdout
    return subprocess.run([*base, "-x", "c", "-"], input=source, check=True, capture_output=True, text=True).stdout
