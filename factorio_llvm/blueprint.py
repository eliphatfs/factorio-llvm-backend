"""Blueprint string export and import (Factorio 2.1 format).

A blueprint string is ``"0" + base64(zlib(json))``. Combinator wire connector
ids are 1/2 for the red/green input side and 3/4 for the red/green output side;
a constant combinator has only 1 (red) and 2 (green).
"""

from __future__ import annotations

import math

import base64
import json
import zlib
from typing import Any

from .netlist import (
    BOTH,
    GREEN,
    RED,
    Arithmetic,
    Color,
    Cond,
    Connector,
    Constant,
    Lamp,
    Message,
    Panel,
    Decider,
    Entity,
    Pole,
    Netlist,
    Out,
    Sig,
)
from .signals import signal_type

# 2.1.23.0, the version decider-compiler's 2.1 blueprints carry.
BLUEPRINT_VERSION = (2 << 48) | (1 << 32) | (23 << 16)

_CMP_TO_GAME = {"<=": "≤", ">=": "≥", "!=": "≠"}
_CMP_FROM_GAME = {v: k for k, v in _CMP_TO_GAME.items()} | {"==": "="}

COLUMNS = 8  # auto layout: combinators per row


# --------------------------------------------------------------------------- encoding


def encode(obj: dict[str, Any]) -> str:
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
    return "0" + base64.b64encode(zlib.compress(data, 9)).decode()


def decode(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text.startswith("0"):
        raise ValueError("unsupported blueprint string version")
    return json.loads(zlib.decompress(base64.b64decode(text[1:])))


# --------------------------------------------------------------------------- export


def _signal(name: str) -> dict[str, str]:
    return {"type": signal_type(name), "name": name}


def _nets(networks: frozenset[Color]) -> dict[str, bool]:
    return {"red": RED in networks, "green": GREEN in networks}


def _operand(prefix: str, o: Sig | int) -> dict[str, Any]:
    if isinstance(o, int):
        return {f"{prefix}_constant": o}
    return {f"{prefix}_signal": _signal(o.name), f"{prefix}_signal_networks": _nets(o.networks)}


def _out(o: Out) -> dict[str, Any]:
    d: dict[str, Any] = {"signal": _signal(o.signal)}
    if o.copy:
        d["networks"] = _nets(o.networks)
    else:
        d["copy_count_from_input"] = False
        d["constant"] = o.constant
    return d


def _cond(i: int, c: Cond) -> dict[str, Any]:
    d: dict[str, Any] = {
        "first_signal": _signal(c.left.name),
        "first_signal_networks": _nets(c.left.networks),
        "comparator": _CMP_TO_GAME.get(c.cmp, c.cmp),
    }
    if isinstance(c.right, int):
        d["constant"] = c.right
    else:
        d["second_signal"] = _signal(c.right.name)
        d["second_signal_networks"] = _nets(c.right.networks)
    if i > 0:
        d["compare_type"] = c.join
    return d


def _plain_cond(c: Cond) -> dict[str, Any]:
    """A lamp or panel condition: these read both wires, so no network flags."""
    return {k: v for k, v in _cond(0, c).items() if not k.endswith("_networks")}


def _cond_in(c: dict[str, Any]) -> Cond:
    right = _sig_in(c["second_signal"], None) if "second_signal" in c else int(c.get("constant", 0))
    cmp = _CMP_FROM_GAME.get(c.get("comparator", "<"), c.get("comparator", "<"))
    return Cond(_sig_in(c["first_signal"], None), cmp, right)


def _control_behavior(e: Entity) -> dict[str, Any]:
    if isinstance(e, Constant):
        filters = [
            {"index": i + 1, **_signal(n), "quality": "normal", "comparator": "=", "count": v}
            for i, (n, v) in enumerate(e.signals.items())
        ]
        cb: dict[str, Any] = {"sections": {"sections": [{"index": 1, "filters": filters}]}}
        if not e.enabled:
            cb["is_on"] = False
        return cb
    if isinstance(e, Arithmetic):
        return {
            "arithmetic_conditions": {
                **_operand("first", e.left),
                "operation": e.op,
                **_operand("second", e.right),
                "output_signal": _signal(e.output),
            }
        }
    if isinstance(e, Pole):
        return {}
    if isinstance(e, Lamp):
        if e.rgb is not None:
            return {"use_colors": True, "color_mode": 2, "rgb_signal": _signal(e.rgb)}
        assert e.condition is not None
        return {"circuit_enabled": True, "circuit_condition": _plain_cond(e.condition)}
    if isinstance(e, Panel):
        return {
            "parameters": [
                {"condition": _plain_cond(m.condition), "icon": _signal(m.icon), "text": m.text} for m in e.messages
            ]
        }
    if isinstance(e, Decider):
        dc: dict[str, Any] = {
            "conditions": [_cond(i, c) for i, c in enumerate(e.conditions)],
            "outputs": [_out(o) for o in e.outputs],
        }
        if e.else_outputs:
            dc["else_outputs"] = [_out(o) for o in e.else_outputs]
        return {"decider_conditions": dc}
    raise TypeError(type(e).__name__)


_PROTOTYPE = {Panel: "display-panel", Lamp: "small-lamp", Pole: "substation", Constant: "constant-combinator", Arithmetic: "arithmetic-combinator", Decider: "decider-combinator"}


def _position(e: Entity, slot: int) -> tuple[float, float]:
    if e.position is not None:
        return e.position
    col, row = slot % COLUMNS, slot // COLUMNS
    # Two-tile combinators face north (input south, output north); a 1x1
    # constant combinator sits in the top tile of its slot.
    if isinstance(e, (Constant, Lamp, Panel)):
        return (col + 0.5, row * 3 + 0.5)
    return (col + 0.5, row * 3 + 1.0)


def _connector_id(c: Connector, color: Color) -> int:
    if isinstance(c.entity, (Constant, Pole, Lamp, Panel)):
        return 1 if color == RED else 2
    base = 1 if c.side == "in" else 3
    return base + (0 if color == RED else 1)


COPPER = 5  # wire connector id of a pole's copper (power) wire
SUBSTATION_REACH = 18.0


def power_links(netlist: Netlist) -> list[tuple[Pole, Pole]]:
    """Copper wires joining the substations: a minimum spanning forest within wire reach."""
    poles = [e for e in netlist.entities if isinstance(e, Pole) and e.position is not None]
    links: list[tuple[Pole, Pole]] = []
    done: set[int] = set()
    for start in range(len(poles)):
        if start in done:
            continue
        done.add(start)
        best = {i: (math.dist(poles[start].position, poles[i].position), start) for i in range(len(poles)) if i not in done}
        while best:
            i, (d, j) = min(best.items(), key=lambda kv: kv[1][0])
            if d > SUBSTATION_REACH:
                break
            del best[i]
            done.add(i)
            links.append((poles[j], poles[i]))
            for k in best:
                dk = math.dist(poles[i].position, poles[k].position)
                if dk < best[k][0]:
                    best[k] = (dk, i)
    return links


def to_blueprint(netlist: Netlist, label: str = "factorio-llvm") -> dict[str, Any]:
    if netlist.logical:
        from .layout import physical

        netlist = physical(netlist)
    numbers: dict[int, int] = {}
    entities = []
    for slot, e in enumerate(netlist.entities):
        n = slot + 1
        numbers[id(e)] = n
        x, y = _position(e, slot)
        ent: dict[str, Any] = {
            "entity_number": n,
            "name": _PROTOTYPE[type(e)],
            "position": {"x": x, "y": y},
        }
        if not isinstance(e, Pole):
            ent["control_behavior"] = _control_behavior(e)
        if e.label:
            ent["player_description"] = e.label
        entities.append(ent)
    wires = [
        [numbers[id(w.a.entity)], _connector_id(w.a, w.color), numbers[id(w.b.entity)], _connector_id(w.b, w.color)]
        for w in netlist.wires
    ]
    wires += [[numbers[id(a)], COPPER, numbers[id(b)], COPPER] for a, b in power_links(netlist)]
    return {
        "blueprint": {
            "item": "blueprint",
            "label": label,
            "icons": [{"signal": {"type": "item", "name": "decider-combinator"}, "index": 1}],
            "entities": entities,
            "wires": wires,
            "version": BLUEPRINT_VERSION,
        }
    }


def export_string(netlist: Netlist, label: str = "factorio-llvm") -> str:
    return encode(to_blueprint(netlist, label))


# --------------------------------------------------------------------------- import


def _networks_in(d: dict[str, Any] | None) -> frozenset[Color]:
    if d is None:
        return BOTH
    return frozenset(c for c in (RED, GREEN) if d.get(c.value, True))


def _sig_in(d: dict[str, Any], nets: dict[str, Any] | None) -> Sig:
    return Sig(d["name"], _networks_in(nets))


def _operand_in(ac: dict[str, Any], prefix: str) -> Sig | int:
    if f"{prefix}_signal" in ac:
        return _sig_in(ac[f"{prefix}_signal"], ac.get(f"{prefix}_signal_networks"))
    return int(ac.get(f"{prefix}_constant", 0))


def _out_in(d: dict[str, Any]) -> Out:
    copy = d.get("copy_count_from_input", True)
    return Out(d["signal"]["name"], copy=copy, networks=_networks_in(d.get("networks")), constant=int(d.get("constant", 1)))


def _entity_in(d: dict[str, Any]) -> Entity:
    cb = d.get("control_behavior", {})
    pos = (float(d["position"]["x"]), float(d["position"]["y"]))
    label = d.get("player_description", "")
    name = d["name"]
    if name == "small-lamp":
        if cb.get("use_colors") and cb.get("color_mode") == 2:
            return Lamp(rgb=cb["rgb_signal"]["name"], label=label, position=pos)
        return Lamp(_cond_in(cb["circuit_condition"]), label=label, position=pos)
    if name == "display-panel":
        msgs = [Message(_cond_in(m["condition"]), m["icon"]["name"], m.get("text", "")) for m in cb.get("parameters", [])]
        return Panel(msgs, label=label, position=pos)
    if name == "constant-combinator":
        signals: dict[str, int] = {}
        for section in cb.get("sections", {}).get("sections", []):
            for f in section.get("filters", []):
                signals[f["name"]] = signals.get(f["name"], 0) + int(f.get("count", 0))
        return Constant(signals, enabled=cb.get("is_on", True), label=label, position=pos)
    if name == "arithmetic-combinator":
        ac = cb["arithmetic_conditions"]
        return Arithmetic(
            _operand_in(ac, "first"), ac.get("operation", "*"), _operand_in(ac, "second"),
            ac["output_signal"]["name"], label=label, position=pos,
        )
    if name == "decider-combinator":
        dc = cb["decider_conditions"]
        conds = []
        for c in dc["conditions"]:
            right: Sig | int = (
                _sig_in(c["second_signal"], c.get("second_signal_networks")) if "second_signal" in c else int(c.get("constant", 0))
            )
            conds.append(
                Cond(
                    _sig_in(c["first_signal"], c.get("first_signal_networks")),
                    _CMP_FROM_GAME.get(c.get("comparator", "<"), c.get("comparator", "<")),
                    right,
                    c.get("compare_type", "or"),
                )
            )
        return Decider(
            conds,
            [_out_in(o) for o in dc.get("outputs", [])],
            [_out_in(o) for o in dc.get("else_outputs", [])],
            label=label, position=pos,
        )
    raise ValueError(f"unsupported entity {name!r}")


def from_blueprint(obj: dict[str, Any]) -> Netlist:
    bp = obj["blueprint"]
    nl = Netlist()
    by_number: dict[int, Entity] = {}
    for d in bp.get("entities", []):
        if d["name"] in ("medium-electric-pole", "small-electric-pole", "substation", "big-electric-pole"):
            pos = (float(d["position"]["x"]), float(d["position"]["y"]))
            by_number[d["entity_number"]] = nl.add(Pole(label=d["name"], position=pos))
            continue
        by_number[d["entity_number"]] = nl.add(_entity_in(d))

    def conn(n: int, cid: int) -> tuple[Connector, Color]:
        e = by_number[n]
        color = RED if cid in (1, 3) else GREEN
        side = "out" if isinstance(e, (Constant, Pole)) or cid in (3, 4) else "in"
        return Connector(e, side), color

    for a, ca, b, cb in bp.get("wires", []):
        if ca == COPPER or cb == COPPER:
            continue  # power wires: rebuilt by power_links on export
        (pa, color), (pb, color_b) = conn(a, ca), conn(b, cb)
        if color != color_b:
            raise ValueError("wire joins different colours")
        nl.wire(color, pa, pb)
    return nl


def import_string(text: str) -> Netlist:
    return from_blueprint(decode(text))
