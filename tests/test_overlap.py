"""Blocks overlap: values and loads from registers no predecessor writes start early."""

import shutil

import pytest

from factorio_llvm.frontend import compile_c, read_function
from factorio_llvm.lower import lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _wrap(x):
    return (x + 2**31) % 2**32 - 2**31


def test_load_starts_in_the_predecessors():
    src = """int t[16];
    int f(int a, int b) {
      for (int i = 0; i < 16; i++) t[i] = i * a + 3;
      int k = (a * 5 + b) & 15, s = 0;
      for (int i = 0; i < b; i++) s = s * 3 + i;
      if (s > 10) s += t[k] * 7; else s -= t[(k + 1) & 15];
      return s; }"""

    def native(a, b):
        t = [_wrap(i * a + 3) for i in range(16)]
        k = (a * 5 + b) & 15
        s = 0
        for i in range(b):
            s = _wrap(s * 3 + i)
        s = _wrap(s + t[k] * 7) if s > 10 else _wrap(s - t[(k + 1) & 15])
        return s

    c = lower(read_function(compile_c(src), "f"))
    assert any(ld.lead for b in c.schedule.blocks for ld in b.loads), c.schedule
    for a in (0, 1, -3, 9):
        for b in (0, 1, 3, 6):
            assert c.run(a, b) == native(a, b), (a, b)
