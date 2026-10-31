"""The joystick cage, factorio.h and argument tags."""

import shutil
from pathlib import Path

import pytest

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.frontend import compile_c, read_function, screen_width_of
from factorio_llvm.layout import check_reach
from factorio_llvm.lower import lower
from factorio_llvm.memory import JOYSTICK
from factorio_llvm.netlist import Gate, Lamp, Panel, Wall

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

EXAMPLE = Path(__file__).parent.parent / "examples" / "joystick.c"


@pytest.fixture(scope="module")
def ir():
    return compile_c(EXAMPLE)


def test_cage_has_eight_gates_and_walls(ir):
    c = lower(read_function(ir, "move"), screen_width=screen_width_of(ir))
    walls = [e for e in c.netlist.entities if isinstance(e, Wall)]
    gates = [e for e in c.netlist.entities if isinstance(e, Gate)]
    assert sorted(w.sensor for w in walls) == sorted(JOYSTICK) and len(gates) == 8
    tiles = {(int(e.position[0] // 1), int(e.position[1] // 1)) for e in walls + gates}
    assert len(tiles) == 16
    # Every gate's wall is in line with it.
    for g, w in zip(gates, walls):
        dx, dy = w.position[0] - g.position[0], w.position[1] - g.position[1]
        assert (dx == 0) == g.vertical and abs(dx) + abs(dy) == 1
    assert screen_width_of(ir) == 8
    lamps = [e for e in c.netlist.entities if isinstance(e, Lamp)]
    assert len({e.position[1] for e in lamps}) == 8


def test_steer_the_dot(ir):
    c = lower(read_function(ir, "move"), screen_width=8)
    wall = {w.sensor: w for w in c.netlist.entities if isinstance(w, Wall)}
    wall["down-right-arrow"].pressed = True
    assert c.run(2) == 5 * 8 + 5
    wall["down-right-arrow"].pressed = False
    wall["left-arrow"].pressed = True
    assert c.run(10) == 3 * 8 + 0


def test_round_trip(ir):
    c = lower(read_function(ir, "move"), screen_width=8)
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []
    walls = [e for e in back.entities if isinstance(e, Wall)]
    assert sorted(w.sensor for w in walls) == sorted(JOYSTICK)
    assert sum(isinstance(e, Gate) for e in back.entities) == 8


def test_needs_the_header():
    with pytest.raises(Exception):
        compile_c("int f(void) { return joystick[0]; }")


def test_arguments_are_tagged():
    c = lower(read_function(compile_c("int f(int a, int b) { return a * b + 1; }"), "f"))
    texts = {e.text for e in c.netlist.entities if isinstance(e, Panel)}
    assert {"argument %a", "argument %b"} <= texts
