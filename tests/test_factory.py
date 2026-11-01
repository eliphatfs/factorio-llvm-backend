"""examples/factory.c: the factory mini game on a 256x192 lamp screen."""

import shutil
from pathlib import Path

import pytest

from factorio_llvm.frontend import compile_c, read_function, screen_width_of
from factorio_llvm.lower import lower
from factorio_llvm.netlist import Lamp

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

EXAMPLE = Path(__file__).parent.parent / "examples" / "factory.c"


def test_factory_lowers_to_a_full_screen():
    ir = compile_c(EXAMPLE)
    assert screen_width_of(ir) == 256
    c = lower(read_function(ir, "game"), screen_width=256)
    lamps = sum(isinstance(e, Lamp) and bool(e.rgb) for e in c.netlist.entities)
    assert 256 * 192 - lamps < 600  # only the substations inside the screen


def test_each_input_array_gets_a_terminal():
    src = "extern volatile int a[1], b[1]; int f(void) { return a[0] * 10 + b[0]; }"
    c = lower(read_function(compile_c(src), "f"), {"a": ["signal-A"], "b": ["signal-B"]})
    assert set(c.input_terminals) == {"a", "b"} and c.input_terminals["a"] is not c.input_terminals["b"]
    c.input_terminals["a"].signals = {"signal-A": 4}
    c.input_terminals["b"].signals = {"signal-B": 2}
    assert c.run() == 42


def test_table_with_a_zero_tail():
    src = "static const int t[100] = {5, 6, 7}; int f(int i) { return t[i]; }"
    c = lower(read_function(compile_c(src), "f"))
    assert [c.run(i) for i in (0, 2, 3, 99)] == [5, 7, 0, 0]


SMALL = "#define TW 16\n#define TH 6\n#define SY 2\n#define BELT_END 10\n"


def test_small_factory_matches_native(tmp_path):
    """A 16x8-tile build delivers as many plates as the same C run natively
    (the belts are vector ops: see examples/factory.c)."""
    import ctypes
    import subprocess

    src = tmp_path / "small.c"
    src.write_text(SMALL + EXAMPLE.read_text())
    host = tmp_path / "host.c"
    host.write_text(f'#include "{src}"\nvolatile int walk[1], hold[1], screen[SCREEN_H * SCREEN_W];\n')
    lib = tmp_path / "host.so"
    inc = Path(__file__).parent.parent / "factorio_llvm" / "include"
    subprocess.run(["clang", "-O2", "-fwrapv", "-shared", "-fPIC", "-I", str(inc), "-o", str(lib), str(host)], check=True)
    frames = 13
    want = ctypes.CDLL(str(lib)).game(frames)
    assert want > 0
    c = lower(read_function(compile_c(src), "game"), screen_width=128)
    assert c.run(frames, max_ticks=200_000) == want
