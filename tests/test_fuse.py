"""Several IR instructions sharing one combinator (fuse.py)."""

import shutil

import pytest

from factorio_llvm.frontend import compile_c, read_function
from factorio_llvm.fuse import Atom, Bool, band, bnot, bor, unsigned_atom
from factorio_llvm.lower import Wire, lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

X, Y = Wire("signal-X", 1), Wire("signal-Y", 1)


def test_bounds_merge_and_contradictions_drop():
    b = band(Bool(((Atom(X, ">", 0),),)), Bool(((Atom(X, ">=", 0), Atom(X, "<", 7)),)))
    assert b.clauses == ((Atom(X, ">=", 1), Atom(X, "<=", 6)),)
    assert band(Bool(((Atom(X, ">", 3),),)), Bool(((Atom(X, "<", 2),),))) is False
    assert bor(Bool(((Atom(X, "=", 1),),)), bnot(Bool(((Atom(X, "=", 1),),)))) is not False


def test_unsigned_against_a_constant():
    assert unsigned_atom(X, "ult", 0) is False
    assert unsigned_atom(X, "ult", 8).clauses == ((Atom(X, ">=", 0), Atom(X, "<=", 7)),)
    assert unsigned_atom(X, "uge", 0) is True


def _depth(src: str) -> int:
    return lower(read_function(compile_c(src), "f")).latency


def test_linear_sum_is_one_tick():
    assert _depth("int f(int a, int b, int c) { return a + b - c + 5; }") == 1
    assert _depth("int f(int a, int b) { return 3 * a - 2 * b; }") == 1


def test_clamp_and_boolean_mix():
    assert _depth("int f(int x) { return x < 0 ? 0 : x > 7 ? 7 : x; }") <= 2
    assert _depth("int f(int a, int b) { return (a > 1 && b < 3) || a == b; }") == 1


@pytest.mark.parametrize("args", [(5, 7, 1), (-3, 2, 9), (2**31 - 1, -(2**31), 0), (0, 0, 0)])
def test_fused_values_match_c(args):
    src = (
        "int f(int a, int b, int c) {\n"
        "  int m = a > b ? a : b;\n"
        "  int k = (unsigned)c < 5u ? c * 2 : -c;\n"
        "  int t = (a < 0 || b == 3) && !(c > 4);\n"
        "  return m - k + t * 7 + ((unsigned)a >= 3u);\n"
        "}\n"
    )
    a, b, c = args
    m = max(a, b)
    k = c * 2 if 0 <= c < 5 else -c
    t = int((a < 0 or b == 3) and not c > 4)
    want = (m - k + t * 7 + int((a & 0xFFFFFFFF) >= 3) + 2**31) % 2**32 - 2**31
    assert lower(read_function(compile_c(src), "f")).run(*args) == want


def test_branch_condition_folds_into_the_jump():
    c = lower(read_function(compile_c("int f(int n) { int s = 0; while (n > 1 && n != 7) { n--; s++; } return s; }"), "f"))
    assert c.run(10) == 3 and c.run(5) == 4
    assert any(j.when is not None for b in c.schedule.blocks for j in b.jumps)
