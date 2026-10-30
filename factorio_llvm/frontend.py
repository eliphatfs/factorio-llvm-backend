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
class Function:
    name: str
    params: list[Value]
    ops: list[Op] = field(default_factory=list)
    ret: Operand | None = None


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


_ATTR_GROUP = re.compile(r"^attributes #\d+ = .*$", re.M)
_ATTR_REF = re.compile(r"\s#\d+\b")


def strip_attributes(ir_text: str) -> str:
    """Drop function attribute groups, which the compiler does not use.

    A clang newer than llvmlite's LLVM writes attributes (such as ``memory(none)``)
    that the older parser rejects.
    """
    return _ATTR_REF.sub("", _ATTR_GROUP.sub("", ir_text))


def read_function(ir_text: str, name: str | None = None) -> Function:
    module = llvm.parse_assembly(strip_attributes(ir_text))
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
    if len(blocks) != 1:
        raise Unsupported(f"@{fn.name} has {len(blocks)} basic blocks; control flow comes in M2")

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
            raise Unsupported(f"operand {ref} is not an argument or earlier result: {where}") from None

    for ins in blocks[0].instructions:
        text = str(ins).strip()
        opc = ins.opcode
        refs = list(ins.operands)
        if opc == "call":
            m = _INTRINSIC.search(text)
            if not m or m.group(1) not in INTRINSICS:
                raise Unsupported(f"calls are not supported yet (inline them): {text}")
            opc, refs = m.group(1), refs[:-1]  # the last operand is the callee
        args = [operand(o, text) for o in refs]
        if opc == "ret":
            if len(args) != 1:
                raise Unsupported(f"void return: {text}")
            out.ret = args[0]
            break
        pred = None
        if opc == "icmp":
            m = _ICMP.search(text)
            assert m, text
            pred = m.group(1)
        out.ops.append(Op(opc, args, fresh(ins, text), pred, text))
    else:
        raise Unsupported(f"@{fn.name} does not end in ret")
    return out


def compile_c(source: str | Path, *, opt: str = "-O2") -> str:
    """Compile a C file (path) or C source text to LLVM IR text with clang."""
    clang = shutil.which("clang")
    if clang is None:
        raise RuntimeError("clang not found on PATH")
    if isinstance(source, Path) or (isinstance(source, str) and source.endswith(".c") and Path(source).exists()):
        cmd = [clang, opt, "-S", "-emit-llvm", "-fno-discard-value-names", "-o", "-", str(source)]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    cmd = [clang, opt, "-S", "-emit-llvm", "-fno-discard-value-names", "-x", "c", "-o", "-", "-"]
    return subprocess.run(cmd, input=source, check=True, capture_output=True, text=True).stdout
