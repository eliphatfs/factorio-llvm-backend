"""The schedule: when each block runs, what it keeps, and where it jumps.

This is the layer between LLVM-level blocks and combinators. The step signal
``S`` acts as a program counter, but there is no fetch: every block is its own
hardware, always computing. The schedule says when each block's results are
sampled and which way control goes:

    block gcd.loop  steps 7..12
      @10  signal-C <- %r (local signal-A)
      @12  if %c (local signal-B) != 0 goto gcd.loop  (S += -6)
      @12  if %c (local signal-B) = 0 goto exit  (S += 0)

Writes and jumps refer to block-local values as ``Lowered`` (a combinator
output wire or a constant); ``cfg.py`` builds the schedule and then turns each
entry into gates, registers and jump deciders.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lower import Lowered, Wire

GATE_TO_LATCH = 2  # ticks from a gate sampling a value to its register latching it


def _show(v: Lowered, source: str) -> str:
    if isinstance(v, int):
        return str(v)
    where = "bus" if v.color.value == "red" else "local"
    return f"{source} ({where} {v.signal})" if source else f"{where} {v.signal}"


@dataclass
class Register:
    """A bus register: one signal, latched after each write to it, held otherwise."""

    signal: str
    label: str = ""

    def __str__(self) -> str:
        return f"{self.signal} ({self.label})" if self.label else self.signal


@dataclass
class Write:
    """At ``step`` a gate samples ``value``; ``register`` latches it GATE_TO_LATCH later."""

    step: int
    value: Lowered
    register: Register
    label: str = ""
    source: str = ""  # the LLVM value, for printing

    def __str__(self) -> str:
        return f"@{self.step}  {self.register.signal} <- {_show(self.value, self.source)}"


@dataclass
class Test:
    """``value cmp constant``, one condition of a jump."""

    value: Wire
    cmp: str
    constant: int
    source: str = ""

    def __str__(self) -> str:
        return f"{_show(self.value, self.source)} {self.cmp} {self.constant}"


@dataclass
class Jump:
    """At ``step``, if every test holds, add ``delta`` to S (go to ``target``)."""

    step: int
    tests: list[Test]
    delta: int
    target: str
    label: str = ""

    def __str__(self) -> str:
        cond = f"if {' and '.join(map(str, self.tests))} " if self.tests else ""
        return f"@{self.step}  {cond}goto {self.target}  (S += {self.delta})"


@dataclass
class BlockSchedule:
    name: str
    base: int  # first step
    depth: int  # ticks for the block's kept values to settle
    writes: list[Write] = field(default_factory=list)
    jumps: list[Jump] = field(default_factory=list)

    @property
    def gate(self) -> int:
        return self.base + self.depth

    @property
    def jump(self) -> int:
        return self.gate + GATE_TO_LATCH

    def __str__(self) -> str:
        lines = [f"block {self.name}  steps {self.base}..{self.jump}  (depth {self.depth})"]
        lines += [f"  {x}" for x in (*self.writes, *self.jumps)]
        return "\n".join(lines)


@dataclass
class Schedule:
    blocks: list[BlockSchedule]
    registers: list[Register]
    result: Register
    halt: int

    def block(self, name: str) -> BlockSchedule:
        return next(b for b in self.blocks if b.name == name)

    def latch_steps(self, r: Register) -> list[int]:
        return sorted({w.step + GATE_TO_LATCH for b in self.blocks for w in b.writes if w.register is r})

    def __str__(self) -> str:
        return "\n".join(
            [
                "registers:",
                *(f"  {r}" for r in self.registers),
                *map(str, self.blocks),
                f"halt at S = {self.halt}, result in {self.result.signal}",
            ]
        )
