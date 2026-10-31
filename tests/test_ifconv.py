"""If-conversion, block merging, store waves, and phis read after their loop."""

import shutil
from pathlib import Path

import pytest

from factorio_llvm.frontend import compile_c, read_function, screen_width_of
from factorio_llvm.lower import lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def test_joystick_loop_is_one_block():
    ir = compile_c(Path(__file__).parent.parent / "examples" / "joystick.c")
    c = lower(read_function(ir, "move"), screen_width=screen_width_of(ir))
    assert c.run(5) == 27
    body = [b for b in c.schedule.blocks if any(st.array == "screen" for st in b.stores) and b.name != "entry"]
    assert len(body) == 1
    stores = body[0].stores
    assert [st.step - stores[0].step for st in stores] == [0, 1]  # erase, then draw a step later
    assert all(st.enable is not None for st in stores)  # only when the dot moved


@pytest.mark.parametrize("args", [(5, 1), (5, 0), (-2, 1), (-2, 0)])
def test_guarded_stores(args):
    src = (
        "int f(int a, int b) { int v[4] = {0}; for (int i = 0; i < 3; i++) {"
        " if (b) v[i] = a + i; else if (a > 0) v[3] = a; }"
        " return v[0] + v[1] + v[2] + v[3] - (b ? 3 : 0) * (a < 0 ? 0 : 1) + (a < 0 ? 3 : 0); }\n"
    )
    a, b = args
    v = [0] * 4
    for i in range(3):
        if b:
            v[i] = a + i
        elif a > 0:
            v[3] = a
    expect = sum(v) - (3 if b else 0) * (0 if a < 0 else 1) + (3 if a < 0 else 0)
    assert lower(read_function(compile_c(src), "f")).run(*args) == expect


def test_phi_read_after_its_loop_sees_the_last_value():
    # The loop's phi of a is overwritten for the next round as the loop exits;
    # the store after the loop must see this round's a.
    src = (
        "int f(int a, int b, int c) { int v[8] = {0}; for (int i = 0; i < (c & 3) + 1; i++) {"
        " v[c & 7] = (a > -3) - (i < c); if (i < c) a = -(b < c); a = a - 2 - (b > i && i != 2); }"
        " int s = 0; for (int k = 0; k < 8; k++) s = s * 7 + v[k]; return s; }\n"
    )
    assert lower(read_function(compile_c(src), "f")).run(7, 7, 4) == 0
