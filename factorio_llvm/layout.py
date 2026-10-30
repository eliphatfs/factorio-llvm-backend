"""Place entities on a grid of substation cells and route logical nets as wires.

Placement. The map is cut into CELL x CELL tile cells. Each cell has a
substation (2x2) at its centre, which powers the whole cell, a few more
reserved 2x2 sites for relay substations (built only if routing uses them),
and slots for
1x2 combinators in rows of two tiles. Every slot is within combinator wire
reach (9) of its cell's substation, and neighbouring substations (CELL apart,
or CELL * sqrt(2) diagonally) are within substation reach (18) of each other.
Entities are placed group by group (a basic block, a register, ...) in a
serpentine order, so a group's members sit next to each other.

Routing. Each logical net becomes a minimum spanning tree over its members.
A tree edge within combinator reach is one wire. A longer edge goes through
substations instead: from one end to a substation in reach, along substation
hops, to the other end. A substation joins everything wired to it on one
colour, so each substation carries at most one red and one green net.
"""

from __future__ import annotations

import heapq
import math
from collections import deque

from .frontend import Unsupported
from .netlist import GREEN, RED, Color, Connector, Constant, Entity, Lamp, Net, Netlist, Pole, WireSeg

CELL = 12
COMBINATOR_REACH = 9.0
SUBSTATION_REACH = 18.0
_SUB_LO = CELL // 2 - 1
# Top-left tiles of the 2x2 substation sites in a cell; the first one powers it.
SITES = ((_SUB_LO, _SUB_LO), (2, 4), (8, 6), (4, 0), (6, 10))


def _cell_slots() -> list[tuple[int, int]]:
    """(column, row pair) slots of one cell, in serpentine order."""
    slots = []
    for rp in range(CELL // 2):
        cols = range(CELL) if rp % 2 == 0 else range(CELL - 1, -1, -1)
        for col in cols:
            tiles_y = {2 * rp, 2 * rp + 1}
            if any(col in (x, x + 1) and tiles_y & {y, y + 1} for x, y in SITES):
                continue
            slots.append((col, rp))
    return slots


SLOTS_PER_CELL = len(_cell_slots())


def _cell_origin(index: int, width: int) -> tuple[int, int]:
    row, col = divmod(index, width)
    if row % 2 == 1:
        col = width - 1 - col
    return col * CELL, row * CELL


def _ordered(entities: list[Entity]) -> list[Entity]:
    """Entities grouped by ``group``, groups in order of first appearance."""
    order: dict[str, list[Entity]] = {}
    for e in entities:
        order.setdefault(e.group, []).append(e)
    return [e for members in order.values() for e in members]


def place(netlist: Netlist) -> list[Pole]:
    """Give every entity a position; return all substation sites, power ones first."""
    entities = _ordered([e for e in netlist.entities if not isinstance(e, Pole)])
    ncells = max(1, math.ceil(len(entities) / SLOTS_PER_CELL))
    width = max(1, math.ceil(math.sqrt(ncells)))
    slots = _cell_slots()
    poles = []
    for k, (sx, sy) in enumerate(SITES):
        for i in range(ncells):
            ox, oy = _cell_origin(i, width)
            poles.append(Pole(label="substation", position=(ox + sx + 1.0, oy + sy + 1.0), group="power" if k == 0 else "relay"))
    for n, e in enumerate(entities):
        cell, slot = divmod(n, SLOTS_PER_CELL)
        ox, oy = _cell_origin(cell, width)
        col, rp = slots[slot]
        if isinstance(e, (Constant, Lamp)):
            e.position = (ox + col + 0.5, oy + 2 * rp + 0.5)
        else:
            e.position = (ox + col + 0.5, oy + 2 * rp + 1.0)
    return poles


def _dist(a: Entity, b: Entity) -> float:
    assert a.position is not None and b.position is not None
    return math.dist(a.position, b.position)


def _reach(e: Entity) -> float:
    return SUBSTATION_REACH if isinstance(e, Pole) else COMBINATOR_REACH


def _mst(members: list[Connector]) -> list[tuple[Connector, Connector]]:
    """Prim's algorithm over member positions."""
    if len(members) < 2:
        return []
    inside = {0}
    best = {i: (_dist(members[0].entity, members[i].entity), 0) for i in range(1, len(members))}
    heap = [(d, i, j) for i, (d, j) in best.items()]
    heapq.heapify(heap)
    edges = []
    while heap and len(inside) < len(members):
        d, i, j = heapq.heappop(heap)
        if i in inside or best[i] != (d, j):
            continue
        inside.add(i)
        edges.append((members[j], members[i]))
        for k in range(len(members)):
            if k not in inside:
                dk = _dist(members[i].entity, members[k].entity)
                if dk < best[k][0]:
                    best[k] = (dk, i)
                    heapq.heappush(heap, (dk, k, i))
    return edges


class _Router:
    def __init__(self, poles: list[Pole]) -> None:
        self.poles = poles
        self.owner: dict[tuple[int, Color], Net] = {}  # (pole index, colour) -> net using it
        self.neighbours = [
            [j for j, q in enumerate(poles) if j != i and _dist(p, q) <= SUBSTATION_REACH] for i, p in enumerate(poles)
        ]
        self.wires: list[WireSeg] = []

    def _free(self, i: int, net: Net) -> bool:
        return self.owner.get((i, net.color), net) is net

    def _relay(self, a: Connector, b: Connector, net: Net) -> None:
        starts = [i for i, p in enumerate(self.poles) if _dist(p, a.entity) <= COMBINATOR_REACH and self._free(i, net)]
        goals = {i for i, p in enumerate(self.poles) if _dist(p, b.entity) <= COMBINATOR_REACH and self._free(i, net)}
        prev: dict[int, int | None] = {i: None for i in starts}
        queue = deque(starts)
        end = None
        while queue:
            i = queue.popleft()
            if i in goals:
                end = i
                break
            for j in self.neighbours[i]:
                if j not in prev and self._free(j, net):
                    prev[j] = i
                    queue.append(j)
        if end is None:
            raise Unsupported(f"cannot route {net.name}: no free substation path for its {net.color.value} wire")
        path = []
        i: int | None = end
        while i is not None:
            path.append(i)
            i = prev[i]
        path.reverse()
        for i in path:
            self.owner[(i, net.color)] = net
        hops = [a] + [self.poles[i].out for i in path] + [b]
        for x, y in zip(hops, hops[1:]):
            if x != y:
                self.wires.append(WireSeg(net.color, x, y))

    def route(self, net: Net) -> None:
        for a, b in _mst(net.members):
            d = _dist(a.entity, b.entity)
            if d <= min(_reach(a.entity), _reach(b.entity)):
                if a != b:
                    self.wires.append(WireSeg(net.color, a, b))
            else:
                self._relay(a, b, net)


def physical(netlist: Netlist) -> Netlist:
    """A placed and routed copy: same entities, plus substations, with wires only."""
    poles = place(netlist)
    out = Netlist()
    for e in _ordered(netlist.entities):
        out.add(e)
    out.wires = list(netlist.wires)
    router = _Router(poles)
    # Big nets first: they are dense and rarely need substations; small sparse
    # nets then take the substation channels they need.
    for net in sorted(netlist.logical, key=lambda n: -len(n.members)):
        router.route(net)
    used = {i for i, _ in router.owner}
    for i, p in enumerate(poles):
        if p.group == "power" or i in used:
            out.add(p)
    out.wires += router.wires
    return out


def check_reach(netlist: Netlist) -> list[WireSeg]:
    """Wires longer than the game allows (empty for a routed netlist)."""
    return [w for w in netlist.wires if _dist(w.a.entity, w.b.entity) > min(_reach(w.a.entity), _reach(w.b.entity))]
