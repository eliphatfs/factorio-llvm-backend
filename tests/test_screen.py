"""Screens of any size: lamp blocks per chunk, substations only where power needs them."""

import shutil

import pytest

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.cfg import DONE
from factorio_llvm.frontend import compile_c, read_function
from factorio_llvm.layout import _axis, check_reach, screen_poles
from factorio_llvm.lower import lower
from factorio_llvm.netlist import RED, Lamp, Pole
from factorio_llvm.sim import Simulator

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _program(w, h):
    return compile_c(
        f"extern volatile int screen[{w * h}];\n"
        f"void draw(void) {{ for (int i = 0; i < {w * h}; i++) screen[i] = i * 7 + 1; }}\n"
    )


def test_power_grid_covers_every_lamp():
    for n in range(1, 120):
        xs = _axis(0, n)
        centres = [x + 1 for x in xs]
        assert all(any(abs(t + 0.5 - c) <= 9 for c in centres) for t in range(n)), n


def test_poles_do_not_overlap():
    for n in range(1, 120):
        xs = _axis(0, n)
        assert all(b - a >= 2 for a, b in zip(xs, xs[1:])), n


def test_small_screens_have_no_dead_pixels():
    for w, h in ((8, 8), (16, 16), (5, 16)):
        poles = screen_poles(0, 0, w, h)
        assert not any(0 <= x + i < w and 0 <= y + j < h for x, y in poles for i in (0, 1) for j in (0, 1))


def test_big_screen_runs():
    w = h = 30  # 900 lamps: 3 chunks and one substation inside
    c = lower(read_function(_program(w, h), "draw"), screen_width=w)
    lamps = [e for e in c.netlist.entities if isinstance(e, Lamp) and e.rgb]
    assert len(lamps) == w * h - 4
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []
    pos = {id(e): e.position for e in c.netlist.entities}
    by_pos = {e.position: e for e in back.entities}
    by_pos[pos[id(c.reset)]].enabled = False
    sim = Simulator(back)
    out = by_pos[pos[id(c.output.entity)]].out
    sim.run_until(lambda s: s.read(out, DONE, [RED]) == 1, 200_000)
    sim.step(5)
    for lamp in lamps:
        i = int(lamp.label.split("[")[1][:-1])
        assert sim.color(by_pos[lamp.position]) == i * 7 + 1, i


def test_huge_screen_routes():
    w, h = 96, 64
    c = lower(read_function(_program(w, h), "draw"), screen_width=w)
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []
    dead = w * h - sum(isinstance(e, Lamp) and bool(e.rgb) for e in c.netlist.entities)
    assert dead == 4 * 5 * 3  # inside poles: 5 columns x 3 rows
