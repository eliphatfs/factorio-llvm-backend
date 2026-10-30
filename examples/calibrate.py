"""Print blueprints for checking the simulator against the game.

Run:  python -m examples.calibrate

1. "calibration": each arithmetic or decider combinator gets its own constant
   combinator. Hover each combinator's output in game and compare it with the
   value printed here. The cases cover the edge cases modelled in
   factorio_llvm/sim.py.
2. "bus register": a step counter S and a one-decider register X on a shared
   red bus. X should become 42 when S passes 5 and then stay at 42.
"""

from __future__ import annotations

from factorio_llvm import GREEN, RED, Arithmetic, Cond, Constant, Decider, Netlist, Out, Simulator, sig
from factorio_llvm.blueprint import export_string
from factorio_llvm.components import bus_register, counter

A, B = "signal-A", "signal-B"

ARITH_CASES = [
    ("power, negative exponent", 2, "^", -1),
    ("left shift by 33", 1, "<<", 33),
    ("left shift by -1", 1, "<<", -1),
    ("right shift of negative", -8, ">>", 1),
    ("right shift by 32", -8, ">>", 32),
    ("divide by zero", 7, "/", 0),
    ("modulo by zero", 7, "%", 0),
    ("modulo, negative dividend", -7, "%", 2),
    ("INT_MIN / -1", -(2**31), "/", -1),
    ("overflowing multiply", 65537, "*", 65537),
]


def calibration() -> tuple[Netlist, list[tuple[str, object]]]:
    nl = Netlist()
    probes: list[tuple[str, object]] = []
    for label, a, op, b in ARITH_CASES:
        c = nl.add(Constant({A: a, B: b}, label=label))
        ar = nl.add(Arithmetic(sig(A), op, sig(B), "signal-Z", label=f"{a} {op} {b}"))
        nl.wire(RED, c.out, ar.inp)
        probes.append((f"{a} {op} {b}", ar))

    # Each condition with a specific output that copies input: is it the sum
    # of the passing signals' values?
    c = nl.add(Constant({"iron-plate": 1500, "copper-plate": 200, "coal": 3000}, label="each sum"))
    d = nl.add(Decider([Cond(sig("signal-each"), ">", 1000)], [Out("signal-X")], label="Each > 1000 -> X (input)"))
    nl.wire(RED, c.out, d.inp)
    probes.append(("Each > 1000 -> X copy", d))

    # Each condition with Each else-outputs: per-signal else, as decider-compiler uses.
    c = nl.add(Constant({"iron-plate": 1500, "copper-plate": 200}, label="each else"))
    d = nl.add(
        Decider(
            [Cond(sig("signal-each"), ">", 1000)],
            [Out("signal-each", copy=False, constant=1)],
            [Out("signal-each", copy=False, constant=-1)],
            label="Each > 1000 -> Each=1 else Each=-1",
        )
    )
    nl.wire(RED, c.out, d.inp)
    probes.append(("Each > 1000 ? 1 : -1", d))
    return nl, probes


def register_demo() -> Netlist:
    nl = Netlist()
    cnt = counter(nl, "signal-S", label="step counter S")
    reg = bus_register(nl, "signal-X", "signal-S", [5], label="register X, written at S = 5")
    nl.wire(RED, cnt.bus, reg.inp, reg.out)
    src = nl.add(Constant({"signal-X": 42}, label="data for X"))
    nl.wire(GREEN, src.out, reg.inp)
    return nl


def main() -> None:
    nl, probes = calibration()
    sim = Simulator(nl)
    sim.step(2)
    print("Simulator predictions (combinator output):")
    for label, entity in probes:
        print(f"  {label:28} {sim.output(entity)}")
    print("\ncalibration blueprint:\n" + export_string(nl, "factorio-llvm calibration"))
    print("\nbus register blueprint:\n" + export_string(register_demo(), "factorio-llvm bus register"))


if __name__ == "__main__":
    main()
