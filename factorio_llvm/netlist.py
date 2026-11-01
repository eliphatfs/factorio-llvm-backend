"""Netlist of Factorio combinators and the red/green wires between them.

A netlist is the compiler's lowest IR before layout: a list of entities plus
explicit wire segments. Wire segments map one-to-one onto blueprint wires, and
the circuit networks the simulator sees are the connected components of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Literal, Union

from .signals import EACH, EVERYTHING, WILDCARDS, signal_type


class Color(str, Enum):
    RED = "red"
    GREEN = "green"


RED = Color.RED
GREEN = Color.GREEN
BOTH: frozenset[Color] = frozenset({RED, GREEN})

Side = Literal["in", "out"]

ARITH_OPS = ("+", "-", "*", "/", "%", "^", "<<", ">>", "AND", "OR", "XOR")
COMPARATORS = ("<", ">", "=", "!=", "<=", ">=")


def _networks(nets: Iterable[Color] | Color | None) -> frozenset[Color]:
    if nets is None:
        return BOTH
    if isinstance(nets, Color):
        return frozenset({nets})
    out = frozenset(Color(n) for n in nets)
    if not out:
        raise ValueError("an operand must read at least one network")
    return out


@dataclass(frozen=True)
class Sig:
    """A signal operand, read from the chosen input networks."""

    name: str
    networks: frozenset[Color] = BOTH

    def __post_init__(self) -> None:
        signal_type(self.name)  # validates the name
        object.__setattr__(self, "networks", _networks(self.networks))

    @property
    def is_wildcard(self) -> bool:
        return self.name in WILDCARDS


Operand = Union[Sig, int]


def sig(name: str, networks: Iterable[Color] | Color | None = None) -> Sig:
    return Sig(name, _networks(networks))


# --------------------------------------------------------------------------- entities


@dataclass(eq=False)
class Entity:
    label: str = field(default="", kw_only=True)
    position: tuple[float, float] | None = field(default=None, kw_only=True)
    group: str = field(default="", kw_only=True)  # layout keeps a group together

    has_input = True

    def connector(self, side: Side) -> "Connector":
        if side == "in" and not self.has_input:
            raise ValueError(f"{type(self).__name__} has no input side")
        return Connector(self, side)

    @property
    def inp(self) -> "Connector":
        return self.connector("in")

    @property
    def out(self) -> "Connector":
        return self.connector("out")


@dataclass(eq=False)
class Constant(Entity):
    """Constant combinator: outputs fixed signals, no input side."""

    signals: dict[str, int] = field(default_factory=dict)
    enabled: bool = True

    has_input = False

    def __post_init__(self) -> None:
        for name in self.signals:
            if name in WILDCARDS:
                raise ValueError("a constant combinator cannot output a wildcard")
            signal_type(name)


@dataclass(eq=False)
class Pole(Entity):
    """A substation: carries red and green wires (and power), computes nothing."""

    has_input = False


@dataclass(eq=False)
class Lamp(Entity):
    """A small lamp. With ``condition`` it lights while that holds; with ``rgb``
    it is always on and shows that signal as a packed 0xRRGGBB colour. Its one
    connection point is modelled as the input side."""

    condition: "Cond | None" = None
    rgb: str | None = None


@dataclass(eq=False)
class Wall(Entity):
    """A stone wall next to a gate. It opens the gate while ``condition`` holds,
    and outputs ``sensor`` = 1 while the gate's sensor sees a player walking
    into it. ``pressed`` stands in for that player in simulation. Its one
    connection point is modelled as the output side."""

    condition: "Cond"
    sensor: str | None = None  # None: controls its gate, reads nothing
    pressed: bool = False

    has_input = False


@dataclass(eq=False)
class Gate(Entity):
    """A gate (1x1): ``vertical`` sits in a north-south wall line. No circuit
    connection of its own; the wall in line with it controls it."""

    vertical: bool = True

    has_input = False


@dataclass(frozen=True)
class Message:
    """One display panel message: shown while ``condition`` holds."""

    condition: "Cond"
    icon: str
    text: str = ""


@dataclass(eq=False)
class Panel(Entity):
    """A display panel: shows the first message whose condition holds.

    With no messages it is a tag: it always shows ``icon`` and ``text``.
    ``always_show`` keeps the text visible above it outside Alt mode too.
    """

    messages: list[Message] = field(default_factory=list)
    text: str = ""
    icon: str | None = None
    always_show: bool = False


@dataclass(eq=False)
class Arithmetic(Entity):
    """Arithmetic combinator: ``left op right -> output``."""

    left: Operand
    op: str
    right: Operand
    output: str

    def __post_init__(self) -> None:
        if self.op not in ARITH_OPS:
            raise ValueError(f"unknown arithmetic operation {self.op!r}")
        each = [o for o in (self.left, self.right) if isinstance(o, Sig) and o.name == EACH]
        if len(each) > 1:
            raise ValueError("only one operand may be Each")
        for o in (self.left, self.right):
            if isinstance(o, Sig) and o.name in WILDCARDS and o.name != EACH:
                raise ValueError("arithmetic operands may only use the Each wildcard")
        if self.output in WILDCARDS and self.output != EACH:
            raise ValueError("arithmetic output may only be Each")
        if self.output == EACH and not each:
            raise ValueError("Each output needs an Each operand")
        signal_type(self.output)

    @property
    def uses_each(self) -> bool:
        return any(isinstance(o, Sig) and o.name == EACH for o in (self.left, self.right))


@dataclass(frozen=True)
class Cond:
    """One comparison in a decider. ``join`` links it to the previous one.

    As in game, AND binds tighter than OR: the list is an OR of AND groups.
    """

    left: Sig
    cmp: str
    right: Operand
    join: Literal["and", "or"] = "and"

    def __post_init__(self) -> None:
        if self.cmp not in COMPARATORS:
            raise ValueError(f"unknown comparator {self.cmp!r}")
        if isinstance(self.right, Sig) and self.right.is_wildcard:
            raise ValueError("the right operand of a condition cannot be a wildcard")


@dataclass(frozen=True)
class Out:
    """One decider output: copy the input count, or emit a constant."""

    signal: str
    copy: bool = True
    networks: frozenset[Color] = BOTH
    constant: int = 1

    def __post_init__(self) -> None:
        signal_type(self.signal)
        object.__setattr__(self, "networks", _networks(self.networks))


@dataclass(eq=False)
class Decider(Entity):
    """Decider combinator with optional 2.1 else outputs."""

    conditions: list[Cond]
    outputs: list[Out] = field(default_factory=list)
    else_outputs: list[Out] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError("a decider needs at least one condition")
        wild = {c.left.name for c in self.conditions if c.left.is_wildcard}
        if len(wild) > 1:
            raise ValueError("a decider may use only one kind of wildcard in its conditions")
        for o in (*self.outputs, *self.else_outputs):
            if o.signal in WILDCARDS and o.signal not in (EACH, EVERYTHING):
                raise ValueError("only Each and Everything are supported as wildcard outputs")
            if o.signal == EACH and not self.each_condition:
                # The game only offers an Each output when a condition uses Each.
                raise ValueError("an Each output needs an Each condition; use Everything")

    @property
    def each_condition(self) -> bool:
        return any(c.left.name == EACH for c in self.conditions)


# --------------------------------------------------------------------------- wiring


@dataclass(frozen=True, eq=False)
class Connector:
    entity: Entity
    side: Side

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Connector) and self.entity is other.entity and self.side == other.side

    def __hash__(self) -> int:
        return hash((id(self.entity), self.side))


@dataclass(frozen=True)
class WireSeg:
    color: Color
    a: Connector
    b: Connector


class Net:
    """A logical circuit network: connectors to join, before layout picks wires."""

    def __init__(self, color: Color, name: str) -> None:
        self.color = Color(color)
        self.name = name
        self.members: list[Connector] = []

    def attach(self, *conns: Connector) -> None:
        for c in conns:
            if c not in self.members:
                self.members.append(c)

    @property
    def probe(self) -> Connector:
        """A connector on this net, for reading it in the simulator."""
        if not self.members:
            raise ValueError(f"net {self.name} has no members")
        return self.members[0]

    def __repr__(self) -> str:
        return f"Net({self.color.value} {self.name}, {len(self.members)} members)"


class Netlist:
    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.wires: list[WireSeg] = []
        self.logical: list[Net] = []

    def net(self, color: Color, name: str = "") -> Net:
        """Declare a logical net; layout turns it into wires (see layout.py)."""
        n = Net(color, name or f"net{len(self.logical)}")
        self.logical.append(n)
        return n

    def add(self, entity: Entity) -> Entity:
        if any(e is entity for e in self.entities):
            raise ValueError("entity already in netlist")
        self.entities.append(entity)
        return entity

    def wire(self, color: Color, *points: Connector) -> None:
        """Chain a wire of one colour through the given connectors."""
        if len(points) < 2:
            raise ValueError("a wire needs at least two endpoints")
        for p in points:
            if not any(e is p.entity for e in self.entities):
                raise ValueError(f"{p.entity!r} is not in the netlist")
        for a, b in zip(points, points[1:]):
            if a == b:
                raise ValueError("cannot wire a connector to itself")
            self.wires.append(WireSeg(Color(color), a, b))

    def nets(self) -> dict[tuple[Connector, Color], int]:
        """Assign a network id to every (connector, colour) that has a wire."""
        parent: dict[tuple[Connector, Color], tuple[Connector, Color]] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for w in self.wires:
            union((w.a, w.color), (w.b, w.color))
        for n in self.logical:
            for c in n.members:
                union((n.members[0], n.color), (c, n.color))
        ids: dict[tuple[Connector, Color], int] = {}
        roots: dict[tuple[Connector, Color], int] = {}
        for key in list(parent):
            root = find(key)
            ids[key] = roots.setdefault(root, len(roots))
        return ids
