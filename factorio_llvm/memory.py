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

I/O. An ``extern`` array is memory-mapped I/O on two red nets, each with a
terminal constant combinator (empty, labelled INPUT / OUTPUT) for wiring the
circuit to the factory. If the program never stores to it, it is an input:
word ``i`` is ``x + 0`` of signal ``io[i]`` on the INPUT net, read live. Else it
is an output: ordinary RAM whose words are also copied, as ``io[i]``, onto the
OUTPUT net.

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
from .frontend import Unsupported
from .lower import Lowered, Wire, _Builder
from .netlist import GREEN, RED, Arithmetic, Cond, Constant, Decider, Net, Out, Sig
from .signals import allocatable_signals

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
    stored: bool = False  # the program writes to it
    io: list[str] = field(default_factory=list)  # external arrays: signal per word

    @property
    def is_input(self) -> bool:
        return self.array.external and not self.stored


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

    def assign_io(self, chosen: dict[str, list[str]], taken_on_output: set[str]) -> dict[str, list[str]]:
        """Pick the I/O signal of every word of every external array."""
        pools = {True: list(_IO_ORDER), False: [s for s in _IO_ORDER if s not in taken_on_output]}
        used: dict[bool, set[str]] = {True: set(), False: set(taken_on_output)}
        out: dict[str, list[str]] = {}
        for bank in self.banks.values():
            a = bank.array
            if not a.external:
                continue
            side = bank.is_input
            if a.name in chosen:
                sigs = chosen[a.name]
                if len(sigs) != a.size:
                    raise Unsupported(f"--io {a.name}: {len(sigs)} signals for {a.size} words")
            else:
                sigs = [s for s in pools[side] if s not in used[side]][: a.size]
            clash = used[side] & set(sigs)
            if clash or len(set(sigs)) != len(sigs):
                raise Unsupported(f"I/O signal used twice on the {'INPUT' if side else 'OUTPUT'} net: {sorted(clash) or sigs}")
            used[side] |= set(sigs)
            bank.io = sigs
            out[a.name] = sigs
        return out

    def build(self, inputs: Net | None, outputs: Net) -> None:
        """Place every word and its readers."""
        s_red = Sig(self.step, frozenset({RED}))
        for bank in self.banks.values():
            a = bank.array
            group = f"array {a.name}"
            for i, init in enumerate(a.init):
                loop = self.b.nl.net(GREEN, f"{a.name}[{i}]")
                bank.words.append(loop)
                if bank.is_input:
                    assert inputs is not None
                    pin = Arithmetic(Sig(bank.io[i], frozenset({RED})), "+", 0, VALUE, label=f"{a.name}[{i}] <- {bank.io[i]}")
                    self.b.place(pin, (), loop, group)
                    inputs.attach(pin.inp)
                    continue
                if a.external:
                    pin = Arithmetic(Sig(VALUE, frozenset({GREEN})), "+", 0, bank.io[i], label=f"{a.name}[{i}] -> {bank.io[i]}")
                    self.b.place(pin, (), outputs, group)
                    loop.attach(pin.inp)
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


# Default I/O signals: digits, then letters, then the rest.
_IO_ORDER = [s for s in allocatable_signals() if s.startswith("signal-") and s[-1].isdigit()] + [
    s for s in allocatable_signals() if not (s.startswith("signal-") and s[-1].isdigit()) and s != "signal-check"
]


def _depth_of(v: Lowered) -> int:
    return 0 if isinstance(v, int) else v.depth
