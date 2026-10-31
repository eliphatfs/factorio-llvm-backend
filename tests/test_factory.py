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
