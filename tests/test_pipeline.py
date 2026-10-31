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
