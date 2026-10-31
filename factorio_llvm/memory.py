"""Fixed-size arrays (M3): chunked "each" memories, read ports, bus writes.

Chunks. An array is cut into chunks of CHUNK words (every usable signal, 395).
Word ``i`` is signal ``sigs[i % CHUNK]`` of chunk ``i // CHUNK``; external
arrays use their I/O signals, so they are one chunk. Each chunk has:

- a private green *loop* holding its words (absent = 0);
- a *key*, a constant combinator on a private green net with each word's
  signal = its address + 1 (addresses travel as address + 1, so word 0 is not
  an absent signal);
- a *cell* decider on the loop: each word passes unless masked;
- a *write mask* decider: ``each(key) == A`` and ``E == array id`` (a store),
  or ``E == -array id`` (clear) or ``S <= 1`` (reset) -> each = 1, onto a
  private red net read by the cell;
- a *writer*: the bus's ``V`` delayed one tick, times the mask, onto the loop.
  The cell drops the old word the tick the writer puts the new one, and keeps
  it from then on;
- with non-zero initial values, a *seed* decider that puts them onto the
  loop while ``S <= 2`` (one tick past the mask, so the cell keeps them).

A read-only array (a constant global) is a ROM: one constant combinator per
chunk with the non-zero words, as its loop.

Write. A store pulses the bus at its block's gate step: address + 1 as ``A``
(two pulses, the address and a 1), value as ``V``, array id as ``E``. The word
is on the loop three ticks later, before the next block can read it. A block
is split after a store that is followed by another store or by a load of the
same array (see ``frontend.split_after_stores``).

Read port. Port ``j`` of an array has an address net (``A`` = address + 1) and
a result net. Per chunk it has a *read mask* (``each(key) == A`` -> each = 1)
and a *reader*: for the masked signal, output its loop value as ``V`` onto the
result net (only one chunk matches). A load drives the address net only while
``S`` is inside its block's steps, so blocks share ports; a block with several
loads of one array uses ports 0, 1, ... A load costs 5 ticks on top of its
address: drive, rename to ``A`` (+1), mask, reader, rename to a block-local
signal.

I/O. An ``extern`` array is memory-mapped I/O on two red nets, each with a
terminal constant combinator (empty, labelled INPUT / OUTPUT) for wiring the
circuit to the factory. If the program never stores to it, it is an input:
its loop is an ``each + 0`` copy of the INPUT net. Else it is an output: RAM
whose loop is also copied (``each + 0``) onto the OUTPUT net.

Screen. A written extern array whose name starts with ``screen`` is a
display instead: a grid of lamps (``screen_width`` wide) on its loop, each
showing its word as a packed 0xRRGGBB colour. The grid sits above the rest of
the layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .frontend import Array
from .frontend import Unsupported
from .lower import Lowered, Wire, _Builder
from .netlist import GREEN, RED, Arithmetic, Cond, Constant, Decider, Lamp, Net, Out, Sig
from .signals import EACH, allocatable_signals

ADDRESS = "signal-A"
VALUE = "signal-V"
ENABLE = "signal-E"
LOAD_LATENCY = 5  # ticks from a settled address to the loaded value
CHUNK = len(allocatable_signals())  # words per chunk: every usable signal


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
    loops: list[Net] = field(default_factory=list)  # each chunk's words
    ports: list[_Port] = field(default_factory=list)
    stored: bool = False  # the program writes to it
    io: list[str] = field(default_factory=list)  # external arrays: signal per word

    @property
    def is_input(self) -> bool:
        return self.array.external and not self.stored

    @property
    def is_screen(self) -> bool:
        return self.array.external and self.stored and self.array.name.startswith("screen")

    @property
    def side(self) -> str:
        """Which net its I/O signals are on."""
        return "INPUT" if self.is_input else f"screen {self.array.name}" if self.is_screen else "OUTPUT"


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
        used: dict[str, set[str]] = {"OUTPUT": set(taken_on_output)}
        out: dict[str, list[str]] = {}
        for bank in self.banks.values():
            a = bank.array
            if not a.external:
                continue
            side = bank.side
            used.setdefault(side, set())
            if a.name in chosen:
                sigs = chosen[a.name]
                if len(sigs) != a.size:
                    raise Unsupported(f"--io {a.name}: {len(sigs)} signals for {a.size} words")
            else:
                sigs = [s for s in _IO_ORDER if s not in used[side]][: a.size]
                if len(sigs) < a.size:
                    raise Unsupported(f"@{a.name} has more words than there are signals")
            clash = used[side] & set(sigs)
            if clash or len(set(sigs)) != len(sigs):
                raise Unsupported(f"I/O signal used twice on the {side} net: {sorted(clash) or sigs}")
            used[side] |= set(sigs)
            bank.io = sigs
            out[a.name] = sigs
        return out

    def _screen(self, bank: _Bank, loop: Net, width: int | None, x0: int) -> int:
        """Place the lamp grid of a screen array from column ``x0``; return the next free column."""
        a = bank.array
        w = width or max(1, round(a.size**0.5))
        rows = -(-a.size // w)
        if rows > MAX_SCREEN_ROWS:
            raise Unsupported(f"@{a.name}: {rows} rows of lamps; at most {MAX_SCREEN_ROWS} (pass a larger --screen-width)")
        for i, sig in enumerate(bank.io):
            r, c = divmod(i, w)
            # Rows end 3 tiles above y = 0, leaving room for a row of substations.
            lamp = Lamp(rgb=sig, label=f"{a.name}[{i}]", position=(x0 + c + 0.5, r - rows - 2 + 0.5))
            self.b.place(lamp, (), None, "screen")
            loop.attach(lamp.inp)
        return x0 + w + 1

    def build(self, inputs: Net | None, outputs: Net, screen_width: int | None = None) -> None:
        """Place every chunk and its readers."""
        screen_x = 0
        for bank in self.banks.values():
            a = bank.array
            group = f"array {a.name}"
            sigs = bank.io if a.external else _CHUNK_SIGNALS
            for base in range(0, a.size, CHUNK):
                words = list(zip(sigs, a.init[base : base + CHUNK]))
                loop = self.b.nl.net(GREEN, f"{a.name}[{base}..]")
                bank.loops.append(loop)
                key_net = self._chunk(bank, base, words, loop, inputs, outputs, group)
                if bank.is_screen:
                    screen_x = self._screen(bank, loop, screen_width, screen_x)
                for j, port in enumerate(bank.ports):
                    self._reader(bank, base, j, port, key_net, loop, group)

    def _key(self, a: Array, base: int, words: list[tuple[str, int]], group: str) -> Net:
        net = self.b.nl.net(GREEN, f"{a.name}[{base}..] key")
        key = Constant({s: base + k + 1 for k, (s, _) in enumerate(words)}, label=f"{a.name}[{base}..] addresses + 1")
        net.attach(self.b.place(key, (), None, group).out)
        return net

    def _chunk(
        self, bank: _Bank, base: int, words: list[tuple[str, int]], loop: Net, inputs: Net | None, outputs: Net, group: str
    ) -> Net:
        """Place one chunk's storage (or input copy, or ROM constant) on ``loop``; return its key net."""
        a = bank.array
        name = f"{a.name}[{base}..]"
        s_red = Sig(self.step, frozenset({RED}))
        if bank.is_input:
            assert inputs is not None
            copy = Arithmetic(Sig(EACH, frozenset({RED})), "+", 0, EACH, label=f"{name} <- INPUT")
            self.b.place(copy, (), loop, group)
            inputs.attach(copy.inp)
            return self._key(a, base, words, group)
        if a.readonly:
            rom = {s: v for s, v in words if v}
            if rom:
                loop.attach(self.b.place(Constant(rom, label=name), (), None, group).out)
            return self._key(a, base, words, group) if bank.ports else loop
        key_net = self._key(a, base, words, group)
        mask = self.b.nl.net(RED, f"{name} write mask")
        e = Sig(ENABLE, frozenset({RED}))
        write_mask = Decider(
            [
                Cond(Sig(EACH, frozenset({GREEN})), "=", Sig(ADDRESS, frozenset({RED}))),
                Cond(e, "=", bank.id),
                Cond(e, "=", -bank.id, join="or"),
                Cond(s_red, "<=", 1, join="or"),
            ],
            [Out(EACH, copy=False, constant=1)],
            label=f"{name} write mask",
        )
        self.b.place(write_mask, (RED,), mask, group)
        key_net.attach(write_mask.inp)
        delayed = self.b.nl.net(GREEN, f"{name} value")
        self.b.place(Arithmetic(Sig(VALUE, frozenset({RED})), "+", 0, VALUE, label=f"{name} value (delay)"), (RED,), delayed, group)
        writer = Arithmetic(Sig(EACH, frozenset({RED})), "*", Sig(VALUE, frozenset({GREEN})), EACH, label=f"{name} writer")
        self.b.place(writer, (), loop, group)
        mask.attach(writer.inp)
        delayed.attach(writer.inp)
        cell = Decider(
            [Cond(Sig(EACH, frozenset({GREEN})), "!=", 0), Cond(Sig(EACH, frozenset({RED})), "=", 0)],
            [Out(EACH, networks=GREEN)],
            label=name,
        )
        self.b.place(cell, (), loop, group)
        loop.attach(cell.inp)
        mask.attach(cell.inp)
        init = [Out(s, copy=False, constant=v) for s, v in words if v]
        if init:
            self.b.place(Decider([Cond(s_red, "<=", 2)], init, label=f"{name} init"), (RED,), loop, group)
        if a.external and not bank.is_screen:
            pin = Arithmetic(Sig(EACH, frozenset({GREEN})), "+", 0, EACH, label=f"{name} -> OUTPUT")
            self.b.place(pin, (), outputs, group)
            loop.attach(pin.inp)
        return key_net

    def _reader(self, bank: _Bank, base: int, j: int, port: _Port, key_net: Net, loop: Net, group: str) -> None:
        name = f"{bank.array.name}[{base}..] port {j}"
        mask = self.b.nl.net(RED, f"{name} mask")
        pick = Decider(
            [Cond(Sig(EACH, frozenset({GREEN})), "=", Sig(ADDRESS, frozenset({RED})))],
            [Out(EACH, copy=False, constant=1)],
            label=f"{name} mask",
        )
        self.b.place(pick, (), mask, group)
        key_net.attach(pick.inp)
        port.address.attach(pick.inp)
        reader = Decider([Cond(Sig(EACH, frozenset({RED})), "!=", 0)], [Out(VALUE, networks=GREEN)], label=f"{name} reader")
        self.b.place(reader, (), port.result, group)
        mask.attach(reader.inp)
        loop.attach(reader.inp)

MAX_SCREEN_ROWS = 16  # two rows of substations power at most this many


# Default I/O signals: digits, then letters, then the rest.
_CHUNK_SIGNALS = allocatable_signals()

_IO_ORDER = [s for s in allocatable_signals() if s.startswith("signal-") and s[-1].isdigit()] + [
    s for s in allocatable_signals() if not (s.startswith("signal-") and s[-1].isdigit()) and s != "signal-check"
]


def _depth_of(v: Lowered) -> int:
    return 0 if isinstance(v, int) else v.depth
