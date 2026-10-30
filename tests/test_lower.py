"""M1: compile straight-line C functions and compare with native code."""

import ctypes
import random
import shutil
import subprocess

import pytest

from factorio_llvm.frontend import Unsupported, compile_c, read_function
from factorio_llvm.lower import lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

SOURCE = r"""
int mix(int a, int b) { return a * b + ((a ^ b) >> 2) - (a & 0xff) + (b | 3); }
int divs(int a, int b) { return a / b + a % b; }
int ushr(int a, int b) { return (int)((unsigned)a >> (b & 31)) + (int)((unsigned)b >> 7); }
int shl(int a, int b) { return a << (b & 31); }
int ucmp(int a, int b) { return ((unsigned)a < (unsigned)b) + 2 * ((unsigned)a >= 77u); }
int scmp(int a, int b) { return (a < b) + 2 * (a == b) + 4 * (a > 5) + 8 * (3 >= b); }
int sel(int a, int b) { return a < b ? a * 3 : b - 7; }
int selc(int a, int b) { return a > b ? 10 : -4; }
int selm(int a, int b) { return a == 0 ? b : 123; }
int logic(int a, int b) { return (a > 0 && b > 0) | ((a < 0) ^ (b < 0)) << 1; }
int neg(int a, int b) { return -(a < b) + 5; }
int max3(int a, int b, int c) { int m = a > b ? a : b; return m > c ? m : c; }
int mins(int a, int b) { return (a < b ? a : b) * 2 + (int)((unsigned)a > (unsigned)b ? (unsigned)a : (unsigned)b); }
int umin(int a, int b) { unsigned x = a, y = b; return (int)(x < y ? x : y); }
int absd(int a, int b) { int d = a - b; return (d < 0 ? -d : d) + 1; }
int konst(int a) { return 42; }
int ident(int a) { return a; }
"""

CASES = [
    ("mix", 2), ("divs", 2), ("ushr", 2), ("shl", 2), ("ucmp", 2), ("scmp", 2), ("sel", 2),
    ("selc", 2), ("selm", 2), ("logic", 2), ("neg", 2), ("max3", 3), ("mins", 2), ("umin", 2), ("absd", 2), ("konst", 1), ("ident", 1),
]

INTERESTING = [0, 1, -1, 2, 3, 5, 7, 31, 32, 77, 255, -255, 2**31 - 1, -(2**31)]


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    d = tmp_path_factory.mktemp("native")
    src, lib = d / "m1.c", d / "m1.so"
    src.write_text(SOURCE)
    subprocess.run(["clang", "-O2", "-fwrapv", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True)
    return ctypes.CDLL(str(lib))


@pytest.fixture(scope="module")
def ir():
    return compile_c(SOURCE)


def safe(name, args):
    a, b = args[0], args[1] if len(args) > 1 else 0
    if name == "divs" and (b == 0 or (a == -(2**31) and b == -1)):
        return False
    return True


@pytest.mark.parametrize("name,arity", CASES)
def test_matches_native(name, arity, native, ir):
    compiled = lower(read_function(ir, name))
    rng = random.Random(name)
    inputs = [tuple(rng.choice(INTERESTING) for _ in range(arity)) for _ in range(60)]
    inputs += [tuple(rng.randint(-(2**31), 2**31 - 1) for _ in range(arity)) for _ in range(60)]
    for args in inputs:
        if not safe(name, args):
            continue
        want = getattr(native, name)(*args)
        assert compiled.run(*args) == want, (name, args)


def test_latency_is_depth():
    c = lower(read_function(compile_c("int f(int a, int b) { return (a + b) * (a - b); }")))
    # two parallel ops, then the multiply
    assert c.latency == 2
    assert len(c.netlist.entities) == 2 + 3


def test_constant_result_needs_no_combinators():
    c = lower(read_function(compile_c("int f(int a) { return 6 * 7; }")))
    assert c.result == 42 and c.latency == 0


def test_control_flow_is_rejected():
    ir = compile_c("int f(int a) { int s = 0; for (int i = 0; i < a; i++) s += i * i; return s; }")
    with pytest.raises(Unsupported, match="basic blocks"):
        read_function(ir)


def test_cli(tmp_path, capsys):
    from factorio_llvm.__main__ import main

    src = tmp_path / "f.c"
    src.write_text("int f(int a, int b) { return a * b + 1; }")
    assert main([str(src), "--args", "6", "7"]) == 0
    out = capsys.readouterr()
    assert "simulated result: 43" in out.err
    assert out.out.startswith("0")


def test_attribute_groups_are_stripped():
    from factorio_llvm.frontend import strip_attributes

    ir = 'define i32 @f(i32 %a) #0 {\n  ret i32 %a\n}\nattributes #0 = { memory(none) "x"="y" }\n'
    out = strip_attributes(ir)
    assert "#0" not in out and "memory" not in out
    assert lower(read_function(ir)).run(5) == 5
