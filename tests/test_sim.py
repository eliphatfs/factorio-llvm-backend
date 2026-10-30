import pytest

from factorio_llvm import GREEN, RED, Arithmetic, Cond, Constant, Decider, Netlist, Out, Simulator, sig
from factorio_llvm.components import bus_register, counter
from factorio_llvm.sim import arith, wrap

EACH = "signal-each"


def test_wrap():
    assert wrap(2**31) == -(2**31)
    assert wrap(-(2**31) - 1) == 2**31 - 1
    assert wrap(2**32 + 5) == 5


@pytest.mark.parametrize(
    "op,a,b,want",
    [
        ("+", 2**31 - 1, 1, -(2**31)),
        ("*", 65536, 65536, 0),
        ("/", -7, 2, -3),
        ("/", 7, 0, 0),
        ("/", -(2**31), -1, -(2**31)),
        ("%", -7, 2, -1),
        ("%", 7, -2, 1),
        ("%", 7, 0, 0),
        ("^", 3, 4, 81),
        ("^", 2, 31, -(2**31)),
        ("^", 2, -1, 0),
        ("<<", 1, 31, -(2**31)),
        ("<<", 1, 33, 2),
        (">>", -8, 1, -4),
        ("AND", -1, 0xFF, 255),
        ("XOR", -1, 0, -1),
    ],
)
def test_arith(op, a, b, want):
    assert arith(op, a, b) == want


def feed(nl, signals, target, color=RED):
    c = nl.add(Constant(signals))
    nl.wire(color, c.out, target.inp)
    return c


def test_combinator_has_one_tick_latency():
    nl = Netlist()
    a = nl.add(Arithmetic(sig("signal-A"), "*", 3, "signal-B"))
    b = nl.add(Arithmetic(sig("signal-B"), "+", 1, "signal-C"))
    feed(nl, {"signal-A": 5}, a)
    nl.wire(RED, a.out, b.inp)
    sim = Simulator(nl)
    assert sim.output(a) == {} and sim.output(b) == {}
    sim.step()
    # b already ran on an empty input: B absent reads as 0, so 0 + 1.
    assert sim.output(a) == {"signal-B": 15} and sim.output(b) == {"signal-C": 1}
    sim.step()
    assert sim.output(b) == {"signal-C": 16}


def test_same_signal_sums_on_wire():
    nl = Netlist()
    a = nl.add(Arithmetic(sig("signal-A"), "+", 0, "signal-A"))
    feed(nl, {"signal-A": 2}, a)
    feed(nl, {"signal-A": 40}, a)
    sim = Simulator(nl)
    sim.step()
    assert sim.output(a) == {"signal-A": 42}


def test_red_and_green_are_separate():
    nl = Netlist()
    a = nl.add(Arithmetic(sig("signal-A", RED), "-", sig("signal-A", GREEN), "signal-B"))
    feed(nl, {"signal-A": 10}, a, RED)
    feed(nl, {"signal-A": 3}, a, GREEN)
    sim = Simulator(nl)
    sim.step()
    assert sim.output(a) == {"signal-B": 7}


def test_each_arithmetic():
    nl = Netlist()
    a = nl.add(Arithmetic(sig(EACH), "*", 2, EACH))
    s = nl.add(Arithmetic(sig(EACH), "+", 0, "signal-S"))
    feed(nl, {"signal-A": 1, "signal-B": 5}, a)
    feed(nl, {"signal-A": 1, "signal-B": 5}, s)
    sim = Simulator(nl)
    sim.step()
    assert sim.output(a) == {"signal-A": 2, "signal-B": 10}
    assert sim.output(s) == {"signal-S": 6}


def test_decider_each_filter_and_count():
    nl = Netlist()
    filt = nl.add(Decider([Cond(sig(EACH), ">", 1000)], [Out(EACH)]))
    count = nl.add(Decider([Cond(sig(EACH), ">", 1000)], [Out("signal-X", copy=False)]))
    total = nl.add(Decider([Cond(sig(EACH), ">", 1000)], [Out("signal-X")]))
    data = {"iron-plate": 1500, "copper-plate": 200, "coal": 3000}
    for d in (filt, count, total):
        feed(nl, data, d)
    sim = Simulator(nl)
    sim.step()
    assert sim.output(filt) == {"iron-plate": 1500, "coal": 3000}
    assert sim.output(count) == {"signal-X": 2}
    assert sim.output(total) == {"signal-X": 4500}


def test_decider_and_binds_tighter_than_or():
    # (A = 1 AND B = 1) OR C = 1
    conds = [
        Cond(sig("signal-A"), "=", 1),
        Cond(sig("signal-B"), "=", 1, join="and"),
        Cond(sig("signal-C"), "=", 1, join="or"),
    ]
    cases = [({"signal-A": 1}, False), ({"signal-A": 1, "signal-B": 1}, True), ({"signal-C": 1}, True), ({}, False)]
    for inputs, want in cases:
        nl = Netlist()
        d = nl.add(Decider(conds, [Out("signal-Z", copy=False)]))
        if inputs:
            feed(nl, inputs, d)
        sim = Simulator(nl)
        sim.step()
        assert (sim.output(d) == {"signal-Z": 1}) is want, inputs


def test_decider_else_outputs():
    nl = Netlist()
    d = nl.add(
        Decider(
            [Cond(sig("signal-A"), ">", 0)],
            [Out("signal-T", copy=False, constant=7)],
            [Out("signal-F", copy=False, constant=-3)],
        )
    )
    c = feed(nl, {"signal-A": 1}, d)
    sim = Simulator(nl)
    sim.step()
    assert sim.output(d) == {"signal-T": 7}
    c.signals = {"signal-A": -1}
    sim.step()
    assert sim.output(d) == {"signal-F": -3}


def test_counter():
    nl = Netlist()
    cnt = counter(nl, "signal-S")
    sim = Simulator(nl)
    seen = []
    for _ in range(5):
        seen.append(sim.read(cnt.bus, "signal-S"))
        sim.step()
    assert seen == [1, 2, 3, 4, 5]


def build_register_demo(write_steps, data=42):
    nl = Netlist()
    cnt = counter(nl, "signal-S")
    reg = bus_register(nl, "signal-X", "signal-S", write_steps)
    nl.wire(RED, cnt.bus, reg.inp, reg.out)
    src = nl.add(Constant({"signal-X": data}))
    nl.wire(GREEN, src.out, reg.inp)
    return nl, cnt, reg, src


def test_bus_register_latches_and_holds():
    nl, cnt, reg, src = build_register_demo([5])
    sim = Simulator(nl)
    history = []
    for _ in range(12):
        history.append((sim.read(cnt.bus, "signal-S"), sim.read(cnt.bus, "signal-X")))
        sim.step()
    # S = 5 is on the bus at tick 4; the register outputs 42 from tick 5 on.
    assert history[4] == (5, 0)
    assert history[5] == (6, 42)
    assert all(x == 42 for _, x in history[5:])
    assert all(x == 0 for _, x in history[:5])
    # Changing the data source after the write step does not disturb the value.
    src.signals = {"signal-X": 7}
    sim.step(3)
    assert sim.read(cnt.bus, "signal-X") == 42


def test_bus_register_rewrites():
    nl, cnt, reg, src = build_register_demo([3, 8])
    sim = Simulator(nl)
    sim.step(5)
    assert sim.read(cnt.bus, "signal-X") == 42
    src.signals = {"signal-X": -9}
    sim.step(5)  # S = 8 at tick 7
    assert sim.read(cnt.bus, "signal-X") == -9


def test_run_until():
    nl = Netlist()
    cnt = counter(nl, "signal-S")
    sim = Simulator(nl)
    assert sim.run_until(lambda s: s.read(cnt.bus, "signal-S") == 10) == 9
    with pytest.raises(TimeoutError):
        sim.run_until(lambda s: False, max_ticks=5)
