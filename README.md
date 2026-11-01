# factorio-llvm-backend

Compile C (through clang) or LLVM IR to Factorio 2.1 combinator circuits,
base game, no Space Age. The output is a blueprint string you paste into the
game.

How it works inside, the module map and the test setup are in
[DEVELOPMENT.md](DEVELOPMENT.md).

## Installing

You need Python 3.11+, clang, and llvmlite.

```
pip install -e .
```

This installs `llvmlite` and `pyperclip` (for copying the blueprint to the
clipboard). clang is only needed to compile `.c` files; `.ll` files are read
directly.

### clang and llvmlite versions

llvmlite bundles its own LLVM and parses the IR clang writes. An LLVM parser
reads IR from the same or an older LLVM, but often not from a newer one, so
**llvmlite's LLVM must be at least as new as your clang**.

| llvmlite | bundled LLVM | clang it can read |
| --- | --- | --- |
| 0.42 – 0.43 | 14 | too old (the IR needs opaque pointers, clang 15+) |
| 0.44 | 15 | clang 15 |
| 0.45 – 0.47 | 20 | clang 15 – 20 |
| 0.48 – 0.50 | 22 | clang 15 – 22 |

The package requires llvmlite 0.45 or newer. Pick the llvmlite whose LLVM is
at least your clang's major version; with too old an llvmlite, parsing fails
with an `LLVM IR parsing error` such as `unterminated attribute group`.
Clang 15 is the lower bound because it is the first to emit opaque `ptr`
types by default, which the frontend expects.

**Recommended:** clang 18 (Ubuntu 24.04's `clang`) or clang 20, with the
newest llvmlite (0.50). The test suite passes with clang 18 on llvmlite 0.45
through 0.50, and with clang 20 on 0.47.

Check yours with:

```
clang --version
python -c "import llvmlite.binding as l; print(l.llvm_version_info)"
```

On Linux, `apt install clang`; on macOS, the Xcode clang works (Apple clang
15+); on Windows, install LLVM from llvm.org and put `clang` on `PATH`.

## Usage

```
python -m factorio_llvm examples/m2.c -f gcd --args 84 36
```

The summary goes to stderr: combinator count, latency, which signal holds each
argument and the result, the I/O terminals, and the simulated result. The
blueprint string goes to stdout and is copied to the clipboard (with
pyperclip, else `pbcopy`, `clip.exe`, `wl-copy`, `xclip` or `xsel`), so you
can paste it straight into the game.

| Option | Meaning |
| --- | --- |
| `source` | a `.c` file (compiled with `clang -O2`) or a `.ll` file |
| `-f`, `--function NAME` | function to compile (default: `main`, else the only one) |
| `--args N ...` | argument values to simulate and bake into the blueprint |
| `--io ARRAY=SIG,SIG,...` | signals for the words of an `extern` I/O array (default `signal-0`, `signal-1`, ...); repeatable |
| `--screen-width N` | lamps per row for an `extern` `screen` array (default: square, or `SCREEN_W` from `factorio.h`) |
| `--unroll N` | unroll every loop N times (default: only `#pragma unroll` loops) |
| `--schedule` | print the step schedule of a function with control flow |
| `--emit-llvm` | print the LLVM IR clang produced and stop |
| `--no-sim` | bake `--args` into the blueprint without simulating (for programs that run a long time) |
| `--layout dense\|sparse` | `dense` (default) packs tightly and spreads out if routing fails; `sparse` places one combinator per cell and routes in one try, at about 50 times the area |
| `--no-blueprint` | do not print the blueprint string |
| `--no-copy` | do not copy the blueprint to the clipboard |

### Running a program in game

Programs with branches or loops are placed with the `RESET` constant
combinator switched on, which holds the circuit at its start. Switch it off to
run; switch it on and off again to rerun. Argument values sit in constant
combinators; change them in game before rerunning. When the program halts, the
**OUTPUT** terminal (an empty constant combinator to wire your factory to)
carries the result and `signal-check = 1`, and a display panel shows reset,
running or done. With a lamp screen, these controls sit in one row right under
the screen's bottom-left corner: the status panel, then RESET, OUTPUT, the
INPUT terminals and the arguments, each with its display panel directly above
it.

Straight-line functions have no `RESET`: change the argument constants and the
result follows after the printed latency.

### What C is supported

`int` (i32) and `bool` arithmetic, comparisons, `if`/loops/`switch`, and
fixed-size `int` arrays (local, global, or constant tables). Not supported:
pointer arguments, pointers that may point into different arrays, other
element types, floating point, `memcpy`, calls to other non-inlined functions.

### I/O, screen and joystick

Start with `#include <factorio.h>` (found automatically). It declares:

- **Input/output arrays.** An `extern` array the program only reads is an
  input: word `i` is read live from signal `i` on the **INPUT** terminal's red
  wire (declare it `volatile` so clang re-reads it). One it writes is an
  output, every word shown on the OUTPUT terminal. Pick the signals with
  `--io`, e.g. `--io in=iron-plate,copper-plate`. See `examples/m4.c`.
  With several input arrays, each gets its own terminal (a constant
  combinator described `INPUT @name` with its signals in order), all next to
  one INPUT panel, so each can be set separately, e.g. by pasting a blueprint
  of a constant combinator onto it, which also works from remote view.
- **Screen.** Define `SCREEN_W` and `SCREEN_H` before the include to get
  `screen[]`: a grid of lamps above the circuit, each word a packed `0xRRGGBB`
  colour (`RGB()` and colour names are provided). Any size works; large
  screens get substations inside to power them.
- **Joystick.** `joystick[8]` (`JOY_N` .. `JOY_NW`, or `joy_dx()` /
  `joy_dy()`) reads an 8-direction joystick built below the screen, left of
  the circuit: a small cage with sensor gates around it. Switch on the tagged
  constant combinator to open the cage door, stand on the middle tile, switch
  it off, then hold WASD; leaning towards a gate trips its sensor. See
  `examples/joystick.c`. The joystick layout has not yet been tested in game.
  Input reads (joystick and input terminals) are latched: a block that
  reads an input sees one consistent value even if it changes mid-block (it
  may be the value from up to a block ago, but costs no extra ticks).
- **`ticks()`**, a free-running game-tick counter.
- **Vector ops.** `vadd(d, a, b)`, `vsub`, `vsel`, comparisons (`vlt` ..
  `vne`), ops with a constant (`vaddk`, `vmulk`, `vandk`, `vshlk`, `vshrk`,
  `vltk` .. `vnek`), `vshift(d, a, k)` (`d[i] = a[i + k]`), `vsum(a)` and
  `vfirst(a)` (lowest non-zero index, or -1) work on whole arrays at once,
  in a fixed number of ticks (about 5 a block of them) whatever the length:
  every word of an array is on its memory loop at the same time. They cost
  combinators instead, a few per op per 395 words (`vshift` one per word
  per source array and `k`). See `include/factorio.h`.

### Examples

```
python -m factorio_llvm examples/m1.c -f mix --args 6 7
python -m factorio_llvm examples/m2.c -f gcd --args 84 36
python -m factorio_llvm examples/m4.c -f draw --args 3
python -m factorio_llvm examples/joystick.c
python -m factorio_llvm examples/allsignals.c   # one lamp per signal, to check signals in game
python -m factorio_llvm examples/factory.c -f game --args 1000000000 --no-sim --io walk=signal-W --io hold=signal-H
```

`examples/factory.c` is a small factory game on a 256x192 screen (32x24
tiles): a chest of plates, an inserter, belts, and an engineer who walks
and places belts and inserters. Two input terminals steer it, `walk` (0 stop,
1 up, 2 right, 3 down, 4 left) and `hold` (0 nothing, 1 belt, 2 inserter,
3 eraser); `examples/factory_buttons.txt` is a blueprint book with a page
per setting to paste onto them. Redrawing a tile costs 24 ticks (all rows of
all changed tiles run as one pipelined loop), so the first full screen takes
about 19,000 ticks (5 minutes) and a frame then takes about 300 to 450 ticks
on a 64x48 build. It exports in about 1.5 minutes. Belts run as vector ops
(`BELT_STEP`), about 31 ticks per direction that has belts plus 19 per plate
that moves, however many belts there are.

## Credits

- **[decider-compiler](https://github.com/Globexix/decider-compiler)** by
  Globexix, which compiles a small game language into a single decider
  combinator (Flappy Bird and Pong). Its use of `Each` conditions, value-coded
  signals and 2.1 else outputs was the starting point for this project's
  design study.
- **[llvmlite](https://github.com/numba/llvmlite)** (Numba project) for
  parsing LLVM IR, and **[clang/LLVM](https://llvm.org)** as the C frontend.
- **[pyperclip](https://github.com/asweigart/pyperclip)** for clipboard copy.
- The overall approach follows high-level synthesis (HLS) for FPGAs, in a
  much simplified form.
- Factorio is by [Wube Software](https://factorio.com); this project is not
  affiliated with them.

## License

LGPL-2.1-or-later; see [LICENSE](LICENSE).
