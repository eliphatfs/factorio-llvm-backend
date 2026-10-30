"""Reusable circuit building blocks from the design doc.

These are the hand-built pieces the compiler will instantiate: the step counter
that drives control, and the one-decider register that lives on a shared bus.
"""

from __future__ import annotations

from dataclasses import dataclass

from .netlist import GREEN, RED, Arithmetic, Cond, Connector, Constant, Decider, Netlist, Out, sig


@dataclass
class Counter:
    """``signal`` rises by ``step`` every tick: an arithmetic ``X + 0`` fed back on red."""

    constant: Constant
    adder: Arithmetic

    @property
    def bus(self) -> Connector:
        """A connector on the red network that carries the count."""
        return self.adder.out


def counter(nl: Netlist, signal: str, step: int = 1, *, label: str = "") -> Counter:
    c = nl.add(Constant({signal: step}, label=f"{label} step" if label else ""))
    a = nl.add(Arithmetic(sig(signal, RED), "+", 0, signal, label=label))
    nl.wire(RED, c.out, a.inp, a.out)
    return Counter(c, a)


def bus_register(nl: Netlist, signal: str, control: str, write_steps: list[int], *, label: str = "") -> Decider:
    """A register that holds ``signal`` on a shared red bus.

    The decider reads the bus (its own old value and the control signal) on red,
    and the value to write on green. When ``control`` equals any of
    ``write_steps`` it outputs the green value; otherwise it re-emits its own
    value from the bus. Callers wire ``reg.inp`` and ``reg.out`` to the bus on
    red and the data source to ``reg.inp`` on green.
    """
    if not write_steps:
        raise ValueError("a register needs at least one write step")
    conds = [Cond(sig(control, RED), "=", k, join="or") for k in write_steps]
    return nl.add(
        Decider(
            conds,
            outputs=[Out(signal, networks=GREEN)],
            else_outputs=[Out(signal, networks=RED)],
            label=label,
        )
    )
