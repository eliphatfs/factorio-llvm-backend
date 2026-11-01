from factorio_llvm import GREEN, RED, Arithmetic, Cond, Constant, Decider, Netlist, Out, Simulator, sig
from factorio_llvm.blueprint import decode, export_string, import_string, to_blueprint
from factorio_llvm.components import bus_register, counter
from factorio_llvm.signals import allocatable_signals, signal_type


def test_signal_catalog():
    names = allocatable_signals()
    assert len(names) == 394
    assert names[0] == "signal-A"
    assert signal_type("iron-plate") == "item"
    assert signal_type("water") == "fluid"
    assert signal_type("signal-each") == "virtual"


def demo():
    nl = Netlist()
    cnt = counter(nl, "signal-S")
    reg = bus_register(nl, "signal-X", "signal-S", [5])
    nl.wire(RED, cnt.bus, reg.inp, reg.out)
    src = nl.add(Constant({"signal-X": 42}))
    nl.wire(GREEN, src.out, reg.inp)
    return nl, cnt


def test_blueprint_shape():
    nl, _ = demo()
    bp = to_blueprint(nl)["blueprint"]
    names = [e["name"] for e in bp["entities"]]
    assert names == ["constant-combinator", "arithmetic-combinator", "decider-combinator", "constant-combinator"]
    dec = bp["entities"][2]["control_behavior"]["decider_conditions"]
    assert dec["outputs"] == [{"signal": {"type": "virtual", "name": "signal-X"}, "networks": {"red": False, "green": True}}]
    assert dec["else_outputs"] == [{"signal": {"type": "virtual", "name": "signal-X"}, "networks": {"red": True, "green": False}}]
    # constant -> adder input (red), adder input -> adder output (the feedback loop)
    assert bp["wires"][0] == [1, 1, 2, 1]
    assert bp["wires"][1] == [2, 1, 2, 3]
    # register data arrives on the green input
    assert bp["wires"][-1] == [4, 2, 3, 2]


def test_round_trip_preserves_behaviour():
    nl, cnt = demo()
    text = export_string(nl)
    assert text.startswith("0")
    assert decode(text) == to_blueprint(nl)
    back = import_string(text)
    a, b = Simulator(nl), Simulator(back)
    reg_a = nl.entities[2]
    reg_b = back.entities[2]
    for _ in range(10):
        assert a.output(reg_a) == b.output(reg_b)
        a.step()
        b.step()
    assert b.output(reg_b) == {"signal-X": 42}


def test_round_trip_operands_and_comparators():
    nl = Netlist()
    nl.add(Arithmetic(sig("signal-each", GREEN), "<<", sig("signal-B", RED), "signal-each"))
    nl.add(Arithmetic(sig("signal-A"), "XOR", -1, "signal-C"))
    nl.add(
        Decider(
            [Cond(sig("signal-A"), ">=", 3), Cond(sig("signal-B", RED), "!=", sig("signal-C", GREEN), join="or")],
            [Out("signal-D", copy=False, constant=-5)],
        )
    )
    nl.add(Constant({"iron-plate": 12, "water": -1}, enabled=False))
    back = import_string(export_string(nl))
    for e, f in zip(nl.entities, back.entities):
        f.position = e.position = None
        f.label = e.label
        assert type(e) is type(f)
    assert to_blueprint(nl) == to_blueprint(back)
