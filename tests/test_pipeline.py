"""Pipelined one-block loops: iterations overlap; stores fire from a flag chain."""

import shutil

import pytest

from factorio_llvm.frontend import compile_c, read_function, screen_width_of
from factorio_llvm.lower import lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _loop(c, name):
    return next(b for b in c.schedule.blocks if b.name == name)


def test_store_heavy_loop_overlaps_iterations():
    src = (
        "int f(int n) { int v[16] = {0}; int x = 1;\n"
        "  for (int i = 0; i < n; i++) { v[(x * 5 + i * 3) & 15] = (x * 7 - i) * 3 + (x > i ? x : i); x = x * 3 + 1; }\n"
        "  int s = 0; for (int k = 0; k < 16; k++) s = s * 7 + v[k]; return s; }\n"
    )

    def native(n):
        v, x = [0] * 16, 1
        for i in range(n):
            v[(x * 5 + i * 3) & 15] = ((x * 7 - i) * 3 + max(x, i)) & 0xFFFFFFFF
            x = (x * 3 + 1) & 0xFFFFFFFF
        s = 0
        for k in range(16):
            s = (s * 7 + v[k]) & 0xFFFFFFFF
        return s - (1 << 32) if s >= 1 << 31 else s

    c = lower(read_function(compile_c(src), "f"))
    body = next(b for b in c.schedule.blocks if b.pipelined)
    for n in (0, 1, 2, 5, 9):
        assert c.run(n) == native(n), n


def test_joystick_loop_takes_five_ticks():
    ir = compile_c("examples/joystick.c")
    c = lower(read_function(ir, "move"), screen_width=screen_width_of(ir))
    body = _loop(c, "for.body")
    assert body.pipelined and body.jump - body.base - body.prologue + 1 == 5
    assert c.run(5) == 27


def _wrap(x):
    return (x + 2**31) % 2**32 - 2**31


def test_loops_that_read_memory_overlap_too():
    # h[...] += ...: each iteration reads what the one before may have written.
    src = """int f(int n, int a) { int v[16] = {0}; for (int i = 0; i < 16; i++) v[i] = i * a + 1;
      int h[4] = {0}; for (int i = 0; i < n; i++) h[(v[i] ^ i) & 3] += i + 1;
      int s = 0; for (int k = 0; k < 4; k++) s = s * 5 + h[k]; return s; }"""

    def native(n, a):
        v = [_wrap(i * a + 1) for i in range(16)]
        h = [0] * 4
        for i in range(n):
            h[(v[i] ^ i) & 3] += i + 1
        s = 0
        for k in range(4):
            s = _wrap(s * 5 + h[k])
        return s

    c = lower(read_function(compile_c(src), "f"))
    body = next(b for b in c.schedule.blocks if b.pipelined and b.loads)
    assert body.jump - body.base - body.prologue + 1 < 8
    for n in (1, 2, 7, 16):
        for a in (1, 3, -2):
            assert c.run(n, a) == native(n, a), (n, a)


def test_two_unrolled_pipelined_loops():
    # Block-local signals repeat across blocks: delays must not be shared.
    src = """int perm[16];
    int f(int k) {
    #pragma unroll 4
      for (int i = 0; i < 16; i++) perm[i] = (i * 5 + k) & 15;
      int inv[16];
    #pragma unroll 4
      for (int i = 0; i < 16; i++) inv[perm[i]] = i;
      int s = 0; for (int i = 0; i < 16; i++) s = s * 3 + inv[i]; return s; }"""

    def native(k):
        perm = [(i * 5 + k) & 15 for i in range(16)]
        inv = [0] * 16
        for i in range(16):
            inv[perm[i]] = i
        s = 0
        for i in range(16):
            s = _wrap(s * 3 + inv[i])
        return s

    c = lower(read_function(compile_c(src), "f"))
    assert sum(b.pipelined for b in c.schedule.blocks) >= 2
    for k in (0, 5, 11):
        assert c.run(k) == native(k)
