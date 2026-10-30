# factorio-llvm-backend

Compile LLVM IR to Factorio 2.1 combinator circuits (base game, no Space Age).

The approach is a simplified high-level synthesis flow: a global step signal `S`
drives control, each IR operation becomes an arithmetic or decider combinator,
and SSA values live in one-decider registers on a shared red bus, where the
signal name is the register number. The milestones are M0 infrastructure, M1 straight-line code, M2 control flow, M3 memory, M4 in-game layout, M5 optimisation.

## Status

M0 (infrastructure) is in place:

| Module | What it does |
| --- | --- |
| `factorio_llvm/netlist.py` | Combinators (constant, arithmetic, decider with 2.1 else outputs) and red/green wire segments |
| `factorio_llvm/sim.py` | Tick-accurate simulator: wire sums, int32 wrap, one tick per combinator |
| `factorio_llvm/blueprint.py` | Blueprint string export and import |
| `factorio_llvm/components.py` | Step counter and bus register building blocks |
| `factorio_llvm/signals.py` | The 395 base-game signals, from Factorio 2.1.24 |

Next: M1 compiles straight-line i32 functions from LLVM IR (via llvmlite).

## Usage

```
pip install -e '.[dev]'
pytest
python -m examples.calibrate
```

`examples.calibrate` prints two blueprint strings and the simulator's
predictions. Paste the calibration blueprint into the game and compare each
combinator's output with the printed values; the cases cover every game
edge case the simulator models (shift amounts, right-shift sign,
negative powers, division by zero, Each outputs); all matched the game on 2.1. The second blueprint is the
bus register: `X` should become 42 once `S` passes 5 and stay there.

Blueprints use an automatic grid layout with no electric poles yet; place
them where they get power.
