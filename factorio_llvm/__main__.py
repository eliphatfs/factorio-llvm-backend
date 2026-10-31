"""Command line: compile a C or LLVM IR function to a Factorio blueprint.

    python -m factorio_llvm examples/m1.c --function mix --args 3 4

Without --function, it compiles main, or the only function in the file.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .blueprint import export_string
from .cfg import SteppedCompiled
from .frontend import Unsupported, compile_c, read_function, screen_width_of
from .lower import lower


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factorio_llvm", description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help=".c file (compiled with clang -O2) or .ll file")
    p.add_argument("--function", "-f", help="function to compile (default: main, else the only one)")
    p.add_argument("--args", type=int, nargs="*", default=None, help="argument values to simulate and bake in")
    p.add_argument(
        "--io",
        action="append",
        default=[],
        metavar="ARRAY=SIG,SIG,...",
        help="signals for the words of an extern array (memory-mapped I/O); default signal-0, signal-1, ...",
    )
    p.add_argument("--screen-width", type=int, help="lamps per row for extern screen* arrays (default: square)")
    p.add_argument("--schedule", action="store_true", help="print the step schedule of a function with control flow")
    p.add_argument("--unroll", type=int, default=1, help="unroll every loop N times (default: only #pragma unroll loops)")
    p.add_argument("--emit-llvm", action="store_true", help="print the LLVM IR clang produced and stop")
    p.add_argument("--no-sim", action="store_true", help="bake --args into the blueprint without simulating (for programs that run for a long time)")
    p.add_argument(
        "--layout",
        choices=("dense", "sparse"),
        default="dense",
        help="dense (default): pack tightly, spread out and retry if routing fails; sparse: one try, always routes, ~50x the area",
    )
    p.add_argument("--no-blueprint", action="store_true", help="do not print the blueprint string")
    p.add_argument("--no-copy", action="store_true", help="do not copy the blueprint string to the clipboard")
    ns = p.parse_args(argv)

    try:
        ir = compile_c(ns.source, unroll=ns.unroll) if ns.source.suffix == ".c" else ns.source.read_text()
    except subprocess.CalledProcessError as e:
        print(e.stderr, end="", file=sys.stderr)
        return 1
    if ns.emit_llvm:
        print(ir)
        return 0
    try:
        io = {}
        for spec in ns.io:
            name, _, sigs = spec.partition("=")
            io[name] = [x.strip() for x in sigs.split(",") if x.strip()]
        compiled = lower(read_function(ir, ns.function), io, ns.screen_width or screen_width_of(ir))
    except Unsupported as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    n = len(compiled.netlist.entities)
    stepped = isinstance(compiled, SteppedCompiled)
    if stepped:
        print(f"@{compiled.function}: {n} combinators, halts when S = {compiled.halt}", file=sys.stderr)
    else:
        print(f"@{compiled.function}: {n} combinators, latency {compiled.latency} ticks", file=sys.stderr)
    for c, s in zip(compiled.params, compiled.param_signals):
        print(f"  {c.label} -> {s}", file=sys.stderr)
    if stepped:
        print(f"  result -> {compiled.result} (valid once S = {compiled.halt})", file=sys.stderr)
    elif not isinstance(compiled.result, int):
        print(f"  result -> {compiled.result.signal}", file=sys.stderr)
    if stepped:
        print(f"  OUTPUT terminal: {compiled.result} = result and signal-check = 1 once halted", file=sys.stderr)
        for name, sigs in compiled.io.items():
            if name == "factorio_clock":
                side = "tick counter"
            elif name == "joystick" and _reads_only(compiled, name):
                side = "joystick cage"
            elif name.startswith("screen") and not _reads_only(compiled, name):
                side = "lamp screen (packed RGB)"
            else:
                side = "INPUT terminal" if _reads_only(compiled, name) else "OUTPUT terminal"
            print(f"  {side}: @{name}[i] = " + ", ".join(sigs), file=sys.stderr)
    if stepped and ns.schedule:
        print(compiled.schedule, file=sys.stderr)
    if ns.args is not None and ns.no_sim:
        if len(ns.args) != len(compiled.params):
            print(f"error: @{compiled.function} takes {len(compiled.params)} arguments", file=sys.stderr)
            return 1
        for c, sig, a in zip(compiled.params, compiled.param_signals, ns.args):
            c.signals = {sig: a}
    elif ns.args is not None:
        value = compiled.run(*ns.args)
        took = f" after {compiled.last_ticks} ticks" if stepped else ""
        print(f"  simulated result: {value}{took}", file=sys.stderr)
    if stepped:
        compiled.reset.enabled = True  # place it held in reset; switch off to run
    if not ns.no_blueprint:
        bp = export_string(compiled.netlist, f"@{compiled.function}", ns.layout)
        print(bp)
        if not ns.no_copy and sys.stdout.isatty():
            tool = copy_to_clipboard(bp)
            if tool:
                print(f"  blueprint copied to clipboard ({tool})", file=sys.stderr)
    return 0


_CLIPBOARD_TOOLS = (
    ["pbcopy"],
    ["clip.exe"],
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
)


def copy_to_clipboard(text: str) -> str | None:
    """Best-effort copy to the system clipboard; returns the tool used, or None."""
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return "pyperclip"
    except Exception:
        pass
    for cmd in _CLIPBOARD_TOOLS:
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return cmd[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _reads_only(compiled, name: str) -> bool:
    return not any(st.array == name for b in compiled.schedule.blocks for st in b.stores)


if __name__ == "__main__":
    sys.exit(main())
