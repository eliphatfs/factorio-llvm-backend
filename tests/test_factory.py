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
