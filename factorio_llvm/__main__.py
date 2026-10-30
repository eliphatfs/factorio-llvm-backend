"""Command line: compile a C or LLVM IR function to a Factorio blueprint.

    python -m factorio_llvm examples/m1.c --function mix --args 3 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .blueprint import export_string
from .frontend import Unsupported, compile_c, read_function
from .lower import lower


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factorio_llvm", description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help=".c file (compiled with clang -O2) or .ll file")
    p.add_argument("--function", "-f", help="function to compile (default: the only one)")
    p.add_argument("--args", type=int, nargs="*", default=None, help="argument values to simulate and bake in")
    p.add_argument("--no-blueprint", action="store_true", help="do not print the blueprint string")
    ns = p.parse_args(argv)

    ir = compile_c(ns.source) if ns.source.suffix == ".c" else ns.source.read_text()
    try:
        compiled = lower(read_function(ir, ns.function))
    except Unsupported as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    n = len(compiled.netlist.entities)
    print(f"@{compiled.function}: {n} combinators, latency {compiled.latency} ticks", file=sys.stderr)
    for c, s in zip(compiled.params, compiled.param_signals):
        print(f"  {c.label} -> {s}", file=sys.stderr)
    if not isinstance(compiled.result, int):
        print(f"  result -> {compiled.result.signal}", file=sys.stderr)
    if ns.args is not None:
        print(f"  simulated result: {compiled.run(*ns.args)}", file=sys.stderr)
    if not ns.no_blueprint:
        print(export_string(compiled.netlist, f"@{compiled.function}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
