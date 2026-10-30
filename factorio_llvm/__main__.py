"""Command line: compile a C or LLVM IR function to a Factorio blueprint.

    python -m factorio_llvm examples/m1.c --function mix --args 3 4

Without --function, it compiles main, or the only function in the file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .blueprint import export_string
from .cfg import SteppedCompiled
from .frontend import Unsupported, compile_c, read_function
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
    p.add_argument("--no-blueprint", action="store_true", help="do not print the blueprint string")
    ns = p.parse_args(argv)

    ir = compile_c(ns.source) if ns.source.suffix == ".c" else ns.source.read_text()
    try:
        io = {}
        for spec in ns.io:
            name, _, sigs = spec.partition("=")
            io[name] = [x.strip() for x in sigs.split(",") if x.strip()]
        compiled = lower(read_function(ir, ns.function), io, ns.screen_width)
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
            if name.startswith("screen") and not _reads_only(compiled, name):
                side = "lamp screen (packed RGB)"
            else:
                side = "INPUT terminal" if _reads_only(compiled, name) else "OUTPUT terminal"
            print(f"  {side}: @{name}[i] = " + ", ".join(sigs), file=sys.stderr)
    if stepped and ns.schedule:
        print(compiled.schedule, file=sys.stderr)
    if ns.args is not None:
        value = compiled.run(*ns.args)
        took = f" after {compiled.last_ticks} ticks" if stepped else ""
        print(f"  simulated result: {value}{took}", file=sys.stderr)
    if stepped:
        compiled.reset.enabled = True  # place it held in reset; switch off to run
    if not ns.no_blueprint:
        print(export_string(compiled.netlist, f"@{compiled.function}"))
    return 0


def _reads_only(compiled, name: str) -> bool:
    return not any(st.array == name for b in compiled.schedule.blocks for st in b.stores)


if __name__ == "__main__":
    sys.exit(main())
