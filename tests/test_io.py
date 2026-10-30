"""M4: the OUTPUT terminal, the done lamp and memory-mapped extern arrays."""

import shutil
from pathlib import Path

import pytest

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.cfg import DONE, STEP
from factorio_llvm.frontend import Unsupported, compile_c, read_function
from factorio_llvm.layout import check_reach
from factorio_llvm.lower import lower
from factorio_llvm.netlist import RED, Lamp, Panel
from factorio_llvm.sim import Simulator

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

EXAMPLE = Path(__file__).parent.parent / "examples" / "m4.c"


@pytest.fixture(scope="module")
def ir():
    return compile_c(EXAMPLE)


def _start(c, n):
    c.params[0].signals = {c.param_signals[0]: n}
    c.reset.enabled = False
    return Simulator(c.netlist)


def test_waits_for_input_then_runs(ir):
    c = lower(read_function(ir, "wait_and_square"))
    assert c.io == {"in": ["signal-0", "signal-1"], "out": ["signal-0", "signal-1"]}
    sim = _start(c, 4)
    c.inputs.signals = {"signal-1": 3}
    sim.step(200)  # in[0] is 0: still waiting
    assert sim.message(c.status) == "running" and sim.network(c.output, RED) == {}
    c.inputs.signals = {"signal-0": 1, "signal-1": 3}
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step(2)
    out = sim.network(c.output, RED)
    assert out == {c.result: 3 * (1 + 2 + 3 + 4), DONE: 1, "signal-0": 4, "signal-1": 16}
    assert sim.message(c.status) == "done"


def test_outputs_show_progress_while_running(ir):
    c = lower(read_function(ir, "wait_and_square"))
    sim = _start(c, 50)
    c.inputs.signals = {"signal-0": 1, "signal-1": 1}
    seen = set()
    while sim.read(c.bus, STEP) != c.halt:
        sim.step()
        seen.add(sim.network(c.output, RED).get("signal-0", 0))
    assert set(range(1, 51)) <= seen


def test_chosen_io_signals_and_round_trip(ir):
    io = {"in": ["iron-plate", "copper-plate"], "out": ["signal-X", "signal-Y"]}
    c = lower(read_function(ir, "wait_and_square"), io)
    assert c.io == io
    bp = export_string(c.netlist)
    back = import_string(bp)
    assert check_reach(back) == []
    assert sum(isinstance(e, Panel) for e in back.entities) == 1
    pos = {id(e): e.position for e in c.netlist.entities}
    by_pos = {e.position: e for e in back.entities}
    terminal_in = by_pos[pos[id(c.inputs)]]
    terminal_in.signals = {"iron-plate": 1, "copper-plate": 2}
    by_pos[pos[id(c.params[0])]].signals = {c.param_signals[0]: 3}
    by_pos[pos[id(c.reset)]].enabled = False
    sim = Simulator(back)
    out = by_pos[pos[id(c.output.entity)]].out
    sim.run_until(lambda s: s.read(out, DONE, [RED]) == 1, 10_000)
    assert sim.network(out, RED) == {c.result: 12, DONE: 1, "signal-X": 3, "signal-Y": 9}


def test_io_signal_clash_is_rejected(ir):
    fn = read_function(ir, "wait_and_square")
    with pytest.raises(Unsupported):
        lower(fn, {"out": ["signal-check", "signal-1"]})


def test_screen_shows_pixels(ir):
    c = lower(read_function(ir, "draw"))
    lamps = [e for e in c.netlist.entities if isinstance(e, Lamp)]
    assert len(lamps) == 64 and all(lamp.rgb for lamp in lamps)
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []
    pos = {id(e): e.position for e in c.netlist.entities}
    by_pos = {e.position: e for e in back.entities}
    by_pos[pos[id(c.params[0])]].signals = {c.param_signals[0]: 1}
    by_pos[pos[id(c.reset)]].enabled = False
    sim = Simulator(back)
    out = by_pos[pos[id(c.output.entity)]].out
    sim.run_until(lambda s: s.read(out, DONE, [RED]) == 1, 100_000)
    sim.step(5)
    for i, lamp in enumerate(lamps):
        y, x = divmod(i, 8)
        want = ((x * 32) << 16 | (y * 32) << 8 | 0x40) if (x + y + 1) & 1 else 0
        assert sim.color(by_pos[lamp.position]) == want, (x, y)
    assert sim.message(by_pos[pos[id(c.status)]]) == "done"
