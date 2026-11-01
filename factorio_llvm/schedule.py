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

from .fuse import Bool
from .lower import Lowered, Wire

GATE_TO_LATCH = 1  # ticks from a gate sampling a value to its register latching it


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
    when: Bool | None = None  # a branch condition folded into the jump's decider

    def __str__(self) -> str:
        parts = [str(t) for t in self.tests]
        if self.when is not None:
            parts.append(" or ".join(" and ".join(f"{a.a.signal} {a.cmp} {getattr(a.b, 'signal', a.b)}" for a in c) for c in self.when.clauses))
        cond = f"if {' and '.join(parts)} " if parts else ""
        return f"@{self.step}  {cond}goto {self.target}  (S += {self.delta})"


@dataclass
class Load:
    """While S is in the block's steps, drive ``address`` onto read port ``port``."""

    array: str
    port: int
    address: Lowered
    source: str = ""
    lead: int = 0  # also drive it in each predecessor's last ``lead`` steps (an early load)

    def __str__(self) -> str:
        early = f"  (from {self.lead} steps before, in the predecessors)" if self.lead else ""
        return f"read {self.array}[{_show(self.address, self.source)}] on port {self.port}{early}"


@dataclass
class Store:
    """At ``step``, write ``value`` to ``array[address]``; with ``clear``, zero the array."""

    step: int
    array: str
    array_id: int
    address: Lowered = 0
    value: Lowered = 0
    clear: bool = False
    sources: tuple[str, str] = ("", "")
    port: int = 0  # bus write port
    wave: int = 0  # steps after the block's first stores (see split_after_stores)
    enable: Lowered | None = None  # E to send when guarded: array id (or -id) if the guard holds, else 0
    delay: int | None = None  # pipelined loop: ticks after its iteration starts, not a step


    def __str__(self) -> str:
        if self.delay is not None:
            return f"iteration +{self.delay}  " + Store.__str__(Store(**{**self.__dict__, "delay": None})).split("  ", 1)[1]
        if self.clear:
            return f"@{self.step}  clear {self.array}  (write port {self.port})"
        a, v = self.sources
        return f"@{self.step}  {self.array}[{_show(self.address, a)}] <- {_show(self.value, v)}  (write port {self.port})"


@dataclass
class BlockSchedule:
    name: str
    base: int  # first step
    depth: int  # ticks for the block's kept values to settle
    writes: list[Write] = field(default_factory=list)
    jumps: list[Jump] = field(default_factory=list)
    loads: list[Load] = field(default_factory=list)
    stores: list[Store] = field(default_factory=list)
    pipelined: bool = False  # a loop whose iterations overlap (see cfg.py)
    drain: int = 0  # steps after the jump for a pipelined loop's last stores
    prologue: int = 0  # a pipelined loop's steps before its first iteration starts

    @property
    def gate(self) -> int:
        return self.base + self.depth

    @property
    def jump(self) -> int:
        return self.gate + GATE_TO_LATCH

    def __str__(self) -> str:
        lines = [f"block {self.name}  steps {self.base}..{self.jump}  (depth {self.depth})"]
        if self.pipelined:
            lines[0] += f"  pipelined: a new iteration every {self.jump - self.base - self.prologue + 1} ticks, prologue {self.prologue}, drain {self.drain}"
        lines += [f"  @{self.base}..{self.jump}  {x}" for x in self.loads]
        lines += [f"  {x}" for x in (*self.writes, *self.stores, *self.jumps)]
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
