"""Print the button blueprint book for examples/factory.c.

Each page is one constant combinator holding one setting of an input
terminal. Pick a page and paste it onto the matching terminal (INPUT @walk
or INPUT @hold); this works from remote view and needs no robots, since
pasting onto an existing combinator only changes its settings.

    python -m examples.factory_buttons > examples/factory_buttons.txt
"""

from factorio_llvm.blueprint import BLUEPRINT_VERSION, _signal, encode

WALK = "signal-W"  # --io walk=signal-W
HOLD = "signal-H"  # --io hold=signal-H

PAGES = [
    ("walk: stop", WALK, 0, "signal-no-entry"),
    ("walk: up", WALK, 1, "up-arrow"),
    ("walk: right", WALK, 2, "right-arrow"),
    ("walk: down", WALK, 3, "down-arrow"),
    ("walk: left", WALK, 4, "left-arrow"),
    ("hold: nothing", HOLD, 0, "signal-deny"),
    ("hold: belt", HOLD, 1, "transport-belt"),
    ("hold: inserter", HOLD, 2, "inserter"),
    ("hold: eraser", HOLD, 3, "deconstruction-planner"),
]


def page(label: str, signal: str, value: int, icon: str) -> dict:
    filters = [{"index": 1, **_signal(signal), "quality": "normal", "comparator": "=", "count": value}]
    combinator = {
        "entity_number": 1,
        "name": "constant-combinator",
        "position": {"x": 0.5, "y": 0.5},
        "control_behavior": {"sections": {"sections": [{"index": 1, "filters": filters}]}},
        "player_description": label,
    }
    return {
        "item": "blueprint",
        "label": label,
        "icons": [{"signal": _signal(icon), "index": 1}],
        "entities": [combinator],
        "version": BLUEPRINT_VERSION,
    }


def book() -> str:
    pages = [{"index": i, "blueprint": page(*p)} for i, p in enumerate(PAGES)]
    return encode(
        {
            "blueprint_book": {
                "item": "blueprint-book",
                "label": "factory game buttons",
                "blueprints": pages,
                "active_index": 0,
                "version": BLUEPRINT_VERSION,
            }
        }
    )


if __name__ == "__main__":
    print(book())
