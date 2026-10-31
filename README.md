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

| llvmlite | bundled LLVM | works here |
| --- | --- | --- |
| 0.42, 0.43 | 14 | no (no wheels for Python 3.13; the IR needs opaque pointers) |
| 0.44 | 15 | no |
| 0.45 – 0.47 | 20 | no (the frontend relies on newer llvmlite APIs) |
| 0.48 – 0.50 | 22 | yes |

So use **llvmlite 0.48 or newer** (the package requires it) with **clang 15
through 22**. Clang 15 is the first to emit opaque `ptr` types by default,
which the frontend expects. The recommended setup, and the one the tests are
run with, is **clang 18** (Ubuntu 24.04's `clang` package) with llvmlite
0.50. Check yours with:

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
| `--no-blueprint` | do not print the blueprint string |
| `--no-copy` | do not copy the blueprint to the clipboard |

### Running a program in game

Programs with branches or loops are placed with the `RESET` constant
combinator switched on, which holds the circuit at its start. Switch it off to
run; switch it on and off again to rerun. Argument values sit in constant
combinators; change them in game before rerunning. When the program halts, the
**OUTPUT** terminal (an empty constant combinator to wire your factory to)
carries the result and `signal-check = 1`, and a display panel shows reset,
running or done.

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
- **Screen.** Define `SCREEN_W` and `SCREEN_H` before the include to get
  `screen[]`: a grid of lamps above the circuit, each word a packed `0xRRGGBB`
  colour (`RGB()` and colour names are provided). Any size works; large
  screens get substations inside to power them.
- **Joystick.** `joystick[8]` (`JOY_N` .. `JOY_NW`, or `joy_dx()` /
  `joy_dy()`) reads an 8-direction joystick built above and left of the
  screen: a small cage with sensor gates around it. Switch on the tagged
  constant combinator to open the cage door, stand on the middle tile, switch
  it off, then hold WASD; leaning towards a gate trips its sensor. See
  `examples/joystick.c`. The joystick layout has not yet been tested in game.
- **`ticks()`**, a free-running game-tick counter.

### Examples

```
python -m factorio_llvm examples/m1.c -f mix --args 6 7
python -m factorio_llvm examples/m2.c -f gcd --args 84 36
python -m factorio_llvm examples/m4.c -f draw --args 3
python -m factorio_llvm examples/joystick.c
python -m factorio_llvm examples/allsignals.c   # one lamp per signal, to check signals in game
```

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
