"""Catalog of circuit signals available in the base game (Factorio 2.1, no Space Age)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

EACH = "signal-each"
ANYTHING = "signal-anything"
EVERYTHING = "signal-everything"
WILDCARDS = frozenset({EACH, ANYTHING, EVERYTHING})


@lru_cache(maxsize=None)
def _catalog() -> dict[str, str]:
    """Map signal name -> signal type ("item", "fluid" or "virtual")."""
    raw = json.loads(resources.files(__package__).joinpath("data/base_signals.json").read_text())
    table = {s["name"]: s["type"] for s in raw["signals"]}
    for w in WILDCARDS:
        table[w] = "virtual"
    return table


def signal_type(name: str) -> str:
    try:
        return _catalog()[name]
    except KeyError:
        raise KeyError(f"unknown signal {name!r}") from None


def rich_text(name: str) -> str:
    """The in-game rich text tag that shows a signal's icon, e.g. ``[virtual-signal=signal-G]``."""
    kind = signal_type(name)
    return f"[{'virtual-signal' if kind == 'virtual' else kind}={name}]"


def is_known(name: str) -> bool:
    return name in _catalog()


# Base-game signals that Space Age deletes (its base-data-updates.lua removes
# the satellite item). A blueprint using one loses those entities on import
# with Space Age on, so they are never allocated.
SPACE_AGE_REMOVED = frozenset({"satellite"})


def allocatable_signals() -> list[str]:
    """Every concrete (non-wildcard) signal that exists with and without Space
    Age, in a stable order.

    Virtual letters and digits come first so small netlists stay readable in game.
    """
    names = [n for n in _catalog() if n not in WILDCARDS and n not in SPACE_AGE_REMOVED]
    letters = [f"signal-{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]
    rest = sorted(n for n in names if n not in letters)
    return letters + rest
