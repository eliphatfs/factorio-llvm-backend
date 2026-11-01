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

Entities in group ``FIXED`` (a screen's lamps) keep the position they were
given, and so do the substations of the screen (see :func:`screen_poles`),
which power the lamps and join them to the relay network.

Routing. Each logical net becomes a minimum spanning tree over its members.
A tree edge within combinator reach is one wire. A longer edge goes through
substations instead: from one end to a substation in reach, along substation
hops, to the other end. A substation joins everything wired to it on one
colour, so each substation carries at most one red and one green net.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict, deque

from .frontend import Unsupported
from .netlist import GREEN, RED, Color, Connector, Constant, Entity, Gate, Lamp, Net, Panel, Wall, Netlist, Pole, WireSeg

CELL = 12
COMBINATOR_REACH = 9.0
SUBSTATION_REACH = 18.0
_SUB_LO = CELL // 2 - 1
# Top-left tiles of the 2x2 substation sites in a cell; the first one powers it.
SITES = ((_SUB_LO, _SUB_LO), (2, 4), (8, 6), (4, 0), (6, 10), (0, 8), (10, 2))


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


FIXED = "screen"


SUPPLY = 9  # tiles from a substation's centre that it powers


def _axis(lo: int, n: int) -> list[int]:
    """Top-left coordinates of substations along one side of ``n`` lamps from
    ``lo``: one just outside each end, plus as few inside as power needs (each
    inside one costs two rows or columns of lamps)."""
    first, last = lo - 2, lo + n  # just outside: they power SUPPLY - 1 lamps in
    out = [first]
    covered = lo + SUPPLY - 1  # first lamp not powered yet
    while covered < last - (SUPPLY - 1):
        p = min(covered + SUPPLY - 1, last - 2)  # tiles p, p + 1; powers p - 8 .. p + 9
        out.append(p)
        covered = p + SUPPLY + 1
    out.append(last)
    return out


def screen_poles(x0: int, y0: int, w: int, h: int) -> list[tuple[int, int]]:
    """Top-left tiles of the substations of a ``w`` x ``h`` lamp screen whose
    top-left lamp is at (x0, y0): a ring around it for wire channels, and a grid
    for power. Grid points inside the screen take 2x2 lamps ("dead pixels"),
    and there are only as many of them as needed to power every lamp."""
    xs, ys = _axis(x0, w), _axis(y0, h)
    out = {(x, y) for x in xs for y in ys}
    # More ring substations between the grid ones: each carries one net per
    # colour, and every lamp block's net leaves the screen through the ring.
    for x in range(xs[0], xs[-1], 4):
        if all(abs(x - g) >= 2 for g in xs):
            out |= {(x, ys[0]), (x, ys[-1])}
    for y in range(ys[0], ys[-1], 4):
        if all(abs(y - g) >= 2 for g in ys):
            out |= {(xs[0], y), (xs[-1], y)}
    return sorted(out)


def place(netlist: Netlist, per_cell: int = SLOTS_PER_CELL) -> list[Pole]:
    """Give every entity a position, at most ``per_cell`` to a cell (spread over
    its slots); return all substation sites, power ones first."""
    fixed = [e for e in netlist.entities if e.group == FIXED and e.position is not None]
    fixed_poles = [Pole(label=e.label, position=e.position, group="power") for e in fixed if isinstance(e, Pole)]
    fixed_ids = {id(e) for e in fixed}
    entities = _ordered([e for e in netlist.entities if not isinstance(e, Pole) and id(e) not in fixed_ids])
    ncells = max(1, math.ceil(len(entities) / per_cell))
    width = max(1, math.ceil(math.sqrt(ncells)))
    # Fixed entities in the circuit's corner (the controls under the screen)
    # push the circuit's cells down below them.
    below = [e.position[1] for e in fixed if not isinstance(e, Pole) and e.position[0] >= 0 and e.position[1] >= 0]
    shift = math.floor(max(below)) + 2 if below else 0
    slots = _cell_slots()
    poles = []
    for k, (sx, sy) in enumerate(SITES):
        for i in range(ncells):
            ox, oy = _cell_origin(i, width)
            oy += shift
            poles.append(Pole(label="substation", position=(ox + sx + 1.0, oy + sy + 1.0), group="power" if k == 0 else "relay"))
    for n, e in enumerate(entities):
        cell, k = divmod(n, per_cell)
        ox, oy = _cell_origin(cell, width)
        oy += shift
        col, rp = slots[k * SLOTS_PER_CELL // per_cell]
        if isinstance(e, (Constant, Lamp, Panel, Wall, Gate)):
            e.position = (ox + col + 0.5, oy + 2 * rp + 0.5)
        else:
            e.position = (ox + col + 0.5, oy + 2 * rp + 1.0)
    return fixed_poles + poles


def _dist(a: Entity, b: Entity) -> float:
    assert a.position is not None and b.position is not None
    return math.dist(a.position, b.position)


def _square(e: Entity, size: float) -> tuple[int, int]:
    assert e.position is not None
    return (math.floor(e.position[0] / size), math.floor(e.position[1] / size))


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
        # Bucket the poles by reach-sized squares, so finding neighbours looks
        # only at the 3x3 squares around each pole (all pairs is quadratic).
        r = SUBSTATION_REACH
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, p in enumerate(poles):
            grid[_square(p, r)].append(i)
        self.neighbours = []
        for i, p in enumerate(poles):
            gx, gy = _square(p, r)
            near = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((gx + dx, gy + dy), ())]
            self.neighbours.append(sorted(j for j in near if j != i and _dist(p, poles[j]) <= r))
        self.wires: list[WireSeg] = []

    def _free(self, i: int, net: Net) -> bool:
        return self.owner.get((i, net.color), net) is net

    def _relay(self, a: Connector, b: Connector, net: Net) -> None:
        # A screen's lamps are all joined by short wires, so the relay may
        # start from whichever lamp of the net is nearest a free substation.
        froms = [m for m in net.members if isinstance(m.entity, Lamp)] if isinstance(a.entity, Lamp) else [a]
        start_at: dict[int, Connector] = {}
        for m in froms:
            for i, p in enumerate(self.poles):
                if i not in start_at and _dist(p, m.entity) <= COMBINATOR_REACH and self._free(i, net):
                    start_at[i] = m
        starts = list(start_at)
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
        hops = [start_at[path[0]]] + [self.poles[i].out for i in path] + [b]
        for x, y in zip(hops, hops[1:]):
            if x != y:
                self.wires.append(WireSeg(net.color, x, y))

    def route(self, net: Net) -> None:
        for a, b in _mst(net.members):
            d = _dist(a.entity, b.entity)
            if d <= min(_reach(a.entity), _reach(b.entity)):
                if a != b:
                    self.wires.append(WireSeg(net.color, a, b))
            elif b.entity.group == FIXED:
                self._relay(b, a, net)
            else:
                self._relay(a, b, net)


LAYOUTS = ("dense", "sparse")


def physical(netlist: Netlist, layout: str = "dense") -> Netlist:
    """A placed and routed copy: same entities, plus substations, with wires only.

    ``dense`` packs a cell full and spreads thinner each time routing runs
    out of substation channels; ``sparse`` goes straight to one entity per
    cell, which always routes in one try but takes about 50 times the area."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}")
    # A denser layout needs fewer substations but may run out of channels;
    # on failure, spread the entities thinner and retry. At one entity per
    # cell every cell has 7 relay sites to itself, so this ends.
    per_cell = SLOTS_PER_CELL if layout == "dense" else 1
    while True:
        poles = place(netlist, per_cell)
        router = _Router(poles)
        try:
            # Big nets first: they are dense and rarely need substations; small
            # sparse nets then take the substation channels they need.
            for net in sorted(netlist.logical, key=lambda n: -len(n.members)):
                router.route(net)
            break
        except Unsupported:
            if per_cell == 1:
                raise
            per_cell = max(1, per_cell * 2 // 3)
    out = Netlist()
    for e in _ordered(netlist.entities):
        if not isinstance(e, Pole):
            out.add(e)
    out.wires = list(netlist.wires)
    used = {i for i, _ in router.owner}
    for i, p in enumerate(poles):
        if p.group == "power" or i in used:
            out.add(p)
    out.wires += router.wires
    return out


def check_reach(netlist: Netlist) -> list[WireSeg]:
    """Wires longer than the game allows (empty for a routed netlist)."""
    return [w for w in netlist.wires if _dist(w.a.entity, w.b.entity) > min(_reach(w.a.entity), _reach(w.b.entity))]
