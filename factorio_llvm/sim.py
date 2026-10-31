"""Tick-accurate simulator for a combinator netlist.

Model, matching the game:

* Each circuit network carries a map signal -> int32. Every output connected to
  it is summed in; a signal whose sum is 0 is absent.
* Every combinator reads its input networks during tick t and its result is on
  its output networks during tick t+1. Constant combinators output immediately.

Edge cases were checked in game (Factorio 2.1) with examples/calibrate.py.
"""

from __future__ import annotations

import operator
from collections import defaultdict
from typing import Iterable, Mapping

from .netlist import (
    BOTH,
    GREEN,
    RED,
    Arithmetic,
    Color,
    Cond,
    Connector,
    Constant,
    Decider,
    Entity,
    Gate,
    Lamp,
    Wall,
    Netlist,
    Panel,
    Out,
    Pole,
    Sig,
)
from .signals import ANYTHING, EACH, EVERYTHING

Signals = dict[str, int]

_MASK = (1 << 32) - 1


def wrap(x: int) -> int:
    """Wrap to a signed 32-bit integer."""
    x &= _MASK
    return x - (1 << 32) if x >> 31 else x


def _trunc_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def arith(op: str, a: int, b: int) -> int:
    if op == "+":
        return wrap(a + b)
    if op == "-":
        return wrap(a - b)
    if op == "*":
        return wrap(a * b)
    if op == "/":
        # Checked in game: x / 0 and INT_MIN / -1 both give 0.
        if b == 0 or (a == -(2**31) and b == -1):
            return 0
        return _trunc_div(a, b)
    if op == "%":
        return 0 if b == 0 else wrap(a - b * _trunc_div(a, b))
    if op == "^":
        # Checked in game: a negative exponent gives 0.
        return 0 if b < 0 else wrap(pow(a, b, 1 << 32))
    if op == "<<":
        # Checked in game: the shift amount uses its low 5 bits.
        return wrap(a << (b & 31))
    if op == ">>":
        # Checked in game: right shift is arithmetic (sign-extending).
        return wrap(a >> (b & 31))
    if op == "AND":
        return wrap(a & b)
    if op == "OR":
        return wrap(a | b)
    if op == "XOR":
        return wrap(a ^ b)
    raise ValueError(op)


def compare(cmp: str, a: int, b: int) -> bool:
    return {
        "<": a < b,
        ">": a > b,
        "=": a == b,
        "!=": a != b,
        "<=": a <= b,
        ">=": a >= b,
    }[cmp]


class _Inputs:
    """The red and green input networks of one combinator for one tick."""

    def __init__(self, red: Mapping[str, int], green: Mapping[str, int]) -> None:
        self.red = red
        self.green = green

    def get(self, name: str, networks: Iterable[Color] = BOTH) -> int:
        total = 0
        if RED in networks:
            total += self.red.get(name, 0)
        if GREEN in networks:
            total += self.green.get(name, 0)
        return wrap(total)

    def signals(self, networks: Iterable[Color] = BOTH) -> list[str]:
        names: dict[str, None] = {}
        if RED in networks:
            names.update(dict.fromkeys(self.red))
        if GREEN in networks:
            names.update(dict.fromkeys(self.green))
        return [n for n in names if self.get(n, networks) != 0]

    def operand(self, o: Sig | int, each: str | None = None) -> int:
        if isinstance(o, int):
            return o
        name = each if o.name == EACH else o.name
        return self.get(name, o.networks)


def _add(out: defaultdict[str, int], name: str, value: int) -> None:
    out[name] += value


def _finish(out: Mapping[str, int]) -> Signals:
    return {k: w for k, v in out.items() if (w := wrap(v)) != 0}


def eval_arithmetic(c: Arithmetic, inp: _Inputs) -> Signals:
    out: defaultdict[str, int] = defaultdict(int)
    if not c.uses_each:
        _add(out, c.output, arith(c.op, inp.operand(c.left), inp.operand(c.right)))
        return _finish(out)
    each_op = c.left if isinstance(c.left, Sig) and c.left.name == EACH else c.right
    assert isinstance(each_op, Sig)
    for s in inp.signals(each_op.networks):
        r = arith(c.op, inp.operand(c.left, s), inp.operand(c.right, s))
        _add(out, s if c.output == EACH else c.output, r)
    return _finish(out)


def _cond_true(c: Cond, inp: _Inputs, each: str | None) -> bool:
    right = inp.operand(c.right)
    if c.left.name == ANYTHING:
        return any(compare(c.cmp, inp.get(s, c.left.networks), right) for s in inp.signals(c.left.networks))
    if c.left.name == EVERYTHING:
        return all(compare(c.cmp, inp.get(s, c.left.networks), right) for s in inp.signals(c.left.networks))
    return compare(c.cmp, inp.operand(c.left, each), right)


def _expr_true(conds: list[Cond], inp: _Inputs, each: str | None) -> bool:
    group = True
    for i, c in enumerate(conds):
        if i > 0 and c.join == "or":
            if group:
                return True
            group = True
        group = group and _cond_true(c, inp, each)
    return group


def _emit(outs: list[Out], inp: _Inputs, out: defaultdict[str, int], each: str | None) -> None:
    for o in outs:
        if o.signal == EACH:
            names = [each] if each is not None else inp.signals(o.networks)
            for s in names:
                _add(out, s, inp.get(s, o.networks) if o.copy else o.constant)
        else:
            # With an Each condition, a specific output sums over the passing signals.
            src = each if each is not None else o.signal
            _add(out, o.signal, inp.get(src, o.networks) if o.copy else o.constant)


def eval_decider(c: Decider, inp: _Inputs) -> Signals:
    out: defaultdict[str, int] = defaultdict(int)
    if c.each_condition:
        fast = _each_fast(c, inp)
        if fast is not None:
            return fast
        nets: set[Color] = set()
        for cond in c.conditions:
            if cond.left.name == EACH:
                nets |= cond.left.networks
        for s in inp.signals(nets):
            passed = _expr_true(c.conditions, inp, s)
            _emit(c.outputs if passed else c.else_outputs, inp, out, s)
    else:
        passed = _expr_true(c.conditions, inp, None)
        _emit(c.outputs if passed else c.else_outputs, inp, out, None)
    return _finish(out)


_CMP = {"<": operator.lt, ">": operator.gt, "=": operator.eq, "!=": operator.ne, "<=": operator.le, ">=": operator.ge}


def _each_fast(c: Decider, inp: _Inputs) -> Signals | None:
    """Each conditions of the form ``each <cmp> X`` without else outputs (what
    memory uses): evaluate the other conditions once, then filter the input
    signals by value instead of re-evaluating the whole expression per signal.
    None when the decider does not have that shape."""
    if c.else_outputs:
        return None
    groups: list[list[Cond]] = [[]]
    for i, cond in enumerate(c.conditions):
        if i > 0 and cond.join == "or":
            groups.append([])
        groups[-1].append(cond)
    nets: set[Color] = set()
    for cond in c.conditions:
        if isinstance(cond.right, Sig) and cond.right.name == EACH:
            return None
        if cond.left.name == EACH:
            nets |= cond.left.networks
    if any(cond.left.name in (ANYTHING, EVERYTHING) for cond in c.conditions):
        return None
    names = inp.signals(nets)
    passed: set[str] = set()
    for g in groups:
        if not all(_cond_true(x, inp, None) for x in g if x.left.name != EACH):
            continue
        cand = names
        for x in g:
            if x.left.name == EACH:
                right, f = inp.operand(x.right), _CMP[x.cmp]
                cand = [s for s in cand if f(inp.get(s, x.left.networks), right)]
        passed.update(cand)
    out: defaultdict[str, int] = defaultdict(int)
    for s in names:
        if s in passed:
            _emit(c.outputs, inp, out, s)
    return _finish(out)


class Simulator:
    def __init__(self, netlist: Netlist) -> None:
        self.netlist = netlist
        self.net_of = netlist.nets()
        self.outputs: dict[int, Signals] = {id(e): {} for e in netlist.entities}
        # Only these ever drive a wire; lamps, poles, panels and gates do not.
        self._drivers = [e for e in netlist.entities if isinstance(e, (Arithmetic, Decider, Wall, Constant))]
        for e in netlist.entities:
            if not isinstance(e, (Arithmetic, Decider, Wall, Constant, Pole, Lamp, Panel, Gate)):
                raise TypeError(f"cannot simulate {type(e).__name__}")
        self.tick = 0
        self._nets = self._compute_nets()

    # ---- state

    def _entity_output(self, e: Entity) -> Signals:
        if isinstance(e, Constant):
            return {k: wrap(v) for k, v in e.signals.items() if v} if e.enabled else {}
        return self.outputs[id(e)]

    def _compute_nets(self) -> dict[int, Signals]:
        nets: dict[int, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
        for e in self._drivers:
            sigs = self._entity_output(e)
            if not sigs:
                continue
            for color in (RED, GREEN):
                net = self.net_of.get((e.out, color))
                if net is None:
                    continue
                for k, v in sigs.items():
                    nets[net][k] += v
        return {n: _finish(s) for n, s in nets.items()}

    def _inputs(self, e: Entity) -> _Inputs:
        def side(color: Color) -> Signals:
            net = self.net_of.get((e.inp, color))
            return self._nets.get(net, {}) if net is not None else {}

        return _Inputs(side(RED), side(GREEN))

    # ---- stepping

    def step(self, ticks: int = 1) -> None:
        for _ in range(ticks):
            # Recompute first so edits to constant combinators count this tick.
            self._nets = self._compute_nets()
            new: dict[int, Signals] = dict(self.outputs)
            for e in self._drivers:
                if isinstance(e, Arithmetic):
                    new[id(e)] = eval_arithmetic(e, self._inputs(e))
                elif isinstance(e, Decider):
                    new[id(e)] = eval_decider(e, self._inputs(e))
                elif isinstance(e, Wall):
                    new[id(e)] = {e.sensor: 1} if e.pressed and e.sensor else {}
            self.outputs = new
            self.tick += 1
            self._nets = self._compute_nets()

    def run_until(self, predicate, max_ticks: int = 100_000) -> int:
        """Step until ``predicate(self)`` holds; return the number of ticks taken."""
        start = self.tick
        while not predicate(self):
            if self.tick - start >= max_ticks:
                raise TimeoutError(f"condition not met within {max_ticks} ticks")
            self.step()
        return self.tick - start

    # ---- observation

    def network(self, connector: Connector, color: Color) -> Signals:
        """The signals on the network attached to ``connector`` this tick."""
        net = self.net_of.get((connector, color))
        if net is None:
            # A fully unwired output connector still carries its own output (once, on red).
            unwired = all((connector, c) not in self.net_of for c in (RED, GREEN))
            if unwired and connector.side == "out" and color == RED:
                return self.output(connector.entity)
            return {}
        return dict(self._nets.get(net, {}))

    def read(self, connector: Connector, name: str, networks: Iterable[Color] = BOTH) -> int:
        return wrap(sum(self.network(connector, c).get(name, 0) for c in networks))

    def _holds(self, e: Entity, cond: Cond) -> bool:
        return eval_decider(Decider([cond], [Out("signal-check", copy=False)]), self._inputs(e)) != {}

    def lit(self, lamp: Lamp) -> bool:
        """Whether a conditional ``lamp`` is on this tick."""
        assert lamp.condition is not None
        return self._holds(lamp, lamp.condition)

    def color(self, lamp: Lamp) -> int:
        """The packed RGB colour an ``rgb`` lamp shows this tick."""
        assert lamp.rgb is not None
        inp = self._inputs(lamp)
        return wrap(inp.red.get(lamp.rgb, 0) + inp.green.get(lamp.rgb, 0))

    def message(self, panel: Panel) -> str:
        """The text a display panel shows this tick ("" if none)."""
        return next((m.text for m in panel.messages if self._holds(panel, m.condition)), panel.text)

    def output(self, e: Entity) -> Signals:
        """What ``e`` is outputting this tick."""
        return dict(self._entity_output(e))
