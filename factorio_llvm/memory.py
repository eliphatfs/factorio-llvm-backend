"""Fixed-size arrays (M3): one memory cell per word, read ports, bus writes.

Cell. Word ``i`` of an array is one decider on a private green loop: it
outputs ``V`` and reads it back on green, so it holds its value. Its red input
is the bus. When the bus carries a write to it (``E == array id`` and
``A == i``) it copies ``V`` from the bus instead; while ``S <= 1`` (reset) or
on ``E == -array id`` (clear) it copies the bus's ``V``, which is then absent,
so the word becomes 0. A word with a non-zero initial value also has an init
decider that outputs the value onto the loop while ``S <= 1``.

A read-only array (a constant global) is a ROM: a constant combinator per
non-zero word on the same kind of private net.

Write. A store is three pulses onto the bus at its block's gate step:
address as ``A``, value as ``V``, array id as ``E``. Cells update one tick
after registers would (step gate + 3, the next block's first step). Blocks end
after each store (see ``frontend.split_after_stores``), so a later load always
sees it.

Read port. Port ``j`` of an array has an address net (signal ``A``) and a
result net. Each word has a reader decider per port: ``A == i`` -> copy ``V``
from the word's loop onto the result net. A load drives the address net only
while ``S`` is inside its block's steps, so blocks share ports; a block with
several loads of one array uses ports 0, 1, ... A load costs 4 ticks on top of
its address: drive, rename to ``A``, reader, rename to a block-local signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .frontend import Array
from .lower import Lowered, Wire, _Builder
from .netlist import GREEN, RED, Arithmetic, Cond, Constant, Decider, Net, Out, Sig

ADDRESS = "signal-A"
VALUE = "signal-V"
ENABLE = "signal-E"
LOAD_LATENCY = 4  # ticks from a settled address to the loaded value


@dataclass
class Ptr:
    """A pointer while lowering: an array and an element index."""

    array: Array
    index: Lowered


@dataclass
class _Port:
    address: Net  # red: drivers' ``A`` -> readers
    result: Net  # red: readers' ``V`` -> each load's rename


@dataclass
class _Bank:
    array: Array
    id: int
    words: list[Net] = field(default_factory=list)  # each word's private green loop
    ports: list[_Port] = field(default_factory=list)


class Memory:
    def __init__(self, b: _Builder, step: str, arrays: list[Array]) -> None:
        self.b = b
        self.step = step
        for s in (ADDRESS, VALUE, ENABLE):
            b.reserve(s)
        self.banks = {id(a): _Bank(a, i + 1) for i, a in enumerate(arrays)}

    def bank(self, a: Array) -> _Bank:
        return self.banks[id(a)]

    def port(self, a: Array, j: int) -> _Port:
        bank = self.bank(a)
        while len(bank.ports) <= j:
            n = len(bank.ports)
            bank.ports.append(_Port(self.b.nl.net(RED, f"{a.name} port {n} address"), self.b.nl.net(RED, f"{a.name} port {n} data")))
        return bank.ports[j]

    def load_result(self, a: Array, j: int, address: Lowered, label: str) -> Wire:
        """The block-local copy of port ``j``'s data (placed in the current block)."""
        out = self.b.local_signal()
        rename = Arithmetic(Sig(VALUE, frozenset({RED})), "+", 0, out, label=f"{label} (data)")
        self.b.place(rename, (), GREEN)
        self.port(a, j).result.attach(rename.inp)
        return Wire(out, _depth_of(address) + LOAD_LATENCY)

    def build(self) -> None:
        """Place every word and its readers."""
        s_red = Sig(self.step, frozenset({RED}))
        for bank in self.banks.values():
            a = bank.array
            group = f"array {a.name}"
            for i, init in enumerate(a.init):
                loop = self.b.nl.net(GREEN, f"{a.name}[{i}]")
                bank.words.append(loop)
                if a.readonly:
                    if init:
                        loop.attach(self.b.place(Constant({VALUE: init}, label=f"{a.name}[{i}]"), (), None, group).out)
                    continue
                cell = Decider(
                    [
                        Cond(s_red, "<=", 1),
                        Cond(Sig(ENABLE, frozenset({RED})), "=", -bank.id, join="or"),
                        Cond(Sig(ENABLE, frozenset({RED})), "=", bank.id, join="or"),
                        Cond(Sig(ADDRESS, frozenset({RED})), "=", i),
                    ],
                    [Out(VALUE, networks=RED)],
                    [Out(VALUE, networks=GREEN)],
                    label=f"{a.name}[{i}]",
                )
                self.b.place(cell, (RED,), loop, group)
                loop.attach(cell.inp)
                if init:
                    seed = Decider([Cond(s_red, "<=", 1)], [Out(VALUE, copy=False, constant=init)], label=f"{a.name}[{i}] init")
                    self.b.place(seed, (RED,), loop, group)
            for j, port in enumerate(bank.ports):
                for i, loop in enumerate(bank.words):
                    if a.readonly and not a.init[i]:
                        continue
                    reader = Decider(
                        [Cond(Sig(ADDRESS, frozenset({RED})), "=", i)],
                        [Out(VALUE, networks=GREEN)],
                        label=f"{a.name}[{i}] read port {j}",
                    )
                    self.b.place(reader, (), port.result, group)
                    port.address.attach(reader.inp)
                    loop.attach(reader.inp)


def _depth_of(v: Lowered) -> int:
    return 0 if isinstance(v, int) else v.depth
