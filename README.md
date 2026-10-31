# factorio-llvm-backend

Compile LLVM IR to Factorio 2.1 combinator circuits (base game, no Space Age).

The approach is a simplified high-level synthesis flow: a global step signal `S`
drives control, each IR operation becomes an arithmetic or decider combinator,
and SSA values live in one-decider registers on a shared red bus, where the
signal name is the register number. The milestones are M0 infrastructure, M1 straight-line code, M2 control flow, M3 memory, M4 layout and I/O, M5 optimisation.

## Status

M0 (infrastructure), M1 (straight-line functions), M2 (control flow), M3 (arrays) and M4 (layout and I/O) are in place:

| Module | What it does |
| --- | --- |
| `factorio_llvm/netlist.py` | Combinators (constant, arithmetic, decider with 2.1 else outputs) and red/green wire segments |
| `factorio_llvm/sim.py` | Tick-accurate simulator: wire sums, int32 wrap, one tick per combinator |
| `factorio_llvm/blueprint.py` | Blueprint string export and import |
| `factorio_llvm/components.py` | Step counter and bus register building blocks |
| `factorio_llvm/signals.py` | The 395 base-game signals, from Factorio 2.1.24 |
| `factorio_llvm/frontend.py` | Reads LLVM IR with llvmlite (or C via clang) into a small dataflow IR |
| `factorio_llvm/lower.py` | Lowers a single-block i32 function to a combinator pipeline |
| `factorio_llvm/cfg.py` | Lowers functions with branches and loops to a stepped circuit |
| `factorio_llvm/memory.py` | Arrays: a held cell per word, shared read ports, writes as bus pulses |
| `factorio_llvm/schedule.py` | The step schedule: per block, its steps, register writes and jumps (`--schedule` prints it) |
| `factorio_llvm/layout.py` | Places combinators in substation cells and routes nets as wires |

M1 supports one basic block of i1/i32 code: `add sub mul sdiv srem shl ashr lshr
and or xor`, every `icmp` predicate, `select`, `zext/sext/trunc` between i1 and
i32, and the `smax smin umax umin abs` intrinsics. Values are named per basic block on
the block's own green net; only registers, arguments and the step signal share
the red bus. So the 395-signal limit applies to one block, and to the number of
values kept across blocks, not to the whole function. Tests compare
the simulated circuit with the same C compiled natively.

`udiv`/`urem` are built from signed operations.

M2 handles branches, loops, `switch` and `phi`. A step signal `S` on the bus
counts up one per tick, and each basic block owns a range of steps. Values that
cross blocks, and every phi, live in one-decider bus registers; branches add a
constant to `S`. Each block costs its own depth (at least 1) plus 2 ticks per visit. `ret`
writes the result register and holds `S` at the halt step. Stepped blueprints
are placed with the `RESET` constant combinator switched on, which holds `S` at
1; switch it off to run, and on and off again to rerun with new arguments.

C is compiled with `clang -O2` plus flags that stop vectorisation, unrolling
(except `#pragma unroll` loops, or every loop with `--unroll N`)
and closed-form loop exit values (`factorio_llvm.frontend.CLANG_FLAGS`).

M3 supports fixed-size `i32` arrays: local (`alloca`) or global, with or
without initial values, and constant globals as ROM tables. `getelementptr`,
`load`, `store` and `memset` of a whole array to 0 are handled; i64 is accepted
for indices and computed in 32 bits. Arrays are stored in chunks of 395 words (one per
usable signal) on a private loop: a chunk costs about 5 combinators and each
read port 2 more per chunk, whatever its size, and a load costs 5 ticks on top
of its address. Each store in a block gets its own write port, so
stores whose addresses differ by a constant (`a[i]`, `a[i + 1]`, ... as
`#pragma unroll` produces) run in the same block; a block is split before a
load of an array it already wrote and before a store that may hit a word it
wrote. Loads in a block each get a read port and run in parallel. See
`examples/m5.c`. Memory is cleared (or reset to its initial
values) while `RESET` is on. Not supported: pointer arguments, pointers that
may point into different arrays, other element types, `memcpy`.

M4 I/O. Every stepped blueprint has an **OUTPUT** terminal (an empty constant
combinator to wire the factory to): once halted it carries the result and
`signal-check = 1`. A display panel shows the status: reset, running or done. `extern` arrays are memory-mapped I/O
(see `examples/m4.c`): one the program only reads is an input, word `i` read
live from signal `i` on the **INPUT** terminal's red wire (use `volatile` so
clang re-reads it); one it writes is an output, every word shown on the OUTPUT
terminal. Signals default to `signal-0`, `signal-1`, ...; pick them with
`--io in=iron-plate,copper-plate`. A written `extern` array named `screen...`
is a lamp screen instead: a grid of lamps above the circuit, each showing its
word as a packed `0xRRGGBB` colour (`--screen-width` sets the row length, at
most 16 rows).

`#include <factorio.h>` (found automatically) declares these devices with
docs and helpers: define `SCREEN_W`/`SCREEN_H` first for `screen[]` (its width
also sets the lamp layout), plus `RGB()` and colour names. `joystick[8]` is an
8-direction joystick (`JOY_N` .. `JOY_NW`, or `joy_dx()`/`joy_dy()`): a cage
of a 3x2 cage (five walls and a door gate below), above and left of the screen, with 8 shut sensor gates 7 tiles
around it. Switch on the tagged constant to open the cage's door, stand on the
middle tile, switch it off, then hold WASD: leaning towards a gate trips its
sensor, and its wall reports that arrow signal. See `examples/joystick.c`. Gate orientation and the
wall's circuit settings follow an in-game experiment but the exported
blueprint is untested in game.

## Usage

```
pip install -e '.[dev]'
pytest
python -m factorio_llvm examples/m1.c -f mix --args 6 7
python -m factorio_llvm examples/m2.c -f gcd --args 84 36
python -m examples.calibrate
```

`python -m factorio_llvm` prints the combinator count, latency, which signal
holds each argument and the result, and the simulated result on stderr, and the
blueprint on stdout. The blueprint's constant combinators hold the `--args`
values; change them in game and the result signal follows after `latency`
ticks.

`examples.calibrate` prints two blueprint strings and the simulator's
predictions. Paste the calibration blueprint into the game and compare each
combinator's output with the printed values; the cases cover every game
edge case the simulator models (shift amounts, right-shift sign,
negative powers, division by zero, Each outputs); all matched the game on 2.1. The second blueprint is the
bus register: `X` should become 42 once `S` passes 5 and stay there.

Blueprints are laid out in 12x12 cells, each powered by a substation at its
centre. Combinators of one basic block sit together. Each net becomes a minimum
spanning tree; an edge longer than the 9-tile combinator reach goes through
substations (18-tile reach), and since a substation joins everything on one
colour, each carries at most one red and one green net. Spare substation sites
in each cell are built only when routing needs them.
