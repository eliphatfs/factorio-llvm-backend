"""M3: fixed-size arrays, checked against native code."""

import ctypes
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.cfg import STEP
from factorio_llvm.frontend import compile_c, read_function
from factorio_llvm.layout import check_reach
from factorio_llvm.lower import lower
from factorio_llvm.sim import Simulator

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

EXAMPLE = Path(__file__).parent.parent / "examples" / "m3.c"

EXTRA = r"""
int w[3] = {5, 6, 7};
int bump(int i) { w[(unsigned)i % 3] += i; return w[0] * 10000 + w[1] * 100 + w[2]; }
int g2[4];
int peek(int a) { g2[0] = a; return g2[1] + g2[0]; }
int big[800];
int chunks(int a) { big[a] = a + 1; big[a + 400] = 7; big[799] = 5; return big[a] * 100 + big[a + 400] * 10 + big[799 - a % 2]; }
int f3[4];
int fill(int a) { for (int i = 0; i < 4; i++) f3[i] = a * i; return f3[3] - f3[1]; }
int rev(int a, int b, int c) {
    int v[3] = {a, b, c}, r[3];
    for (int i = 0; i < 3; i++) r[2 - i] = v[i];
    return r[0] * 100 + r[1] * 10 + r[2];
}
"""

CASES = [
    ("table", lambda r: (r.randint(-20, 20),)),
    ("sieve", lambda r: (r.randint(-2, 70),)),
    ("bubble", lambda r: tuple(r.randint(0, 9) for _ in range(4))),
    ("histogram", lambda r: (r.randint(-9, 9), r.randint(-9, 9))),
    ("bump", lambda r: (r.randint(0, 9),)),
    ("chunks", lambda r: (r.randint(0, 399),)),
    ("fill", lambda r: (r.randint(-9, 9),)),
    ("rev", lambda r: tuple(r.randint(0, 9) for _ in range(3))),
]


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    d = tmp_path_factory.mktemp("m3")
    src = d / "m3.c"
    src.write_text(EXAMPLE.read_text() + EXTRA)
    lib = d / "m3.so"
    subprocess.run(["clang", "-O2", "-fwrapv", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True)
    return compile_c(src), str(lib)


@pytest.mark.parametrize("name,gen", CASES, ids=[c[0] for c in CASES])
def test_matches_native(build, name, gen):
    ir, lib_path = build
    c = lower(read_function(ir, name))
    rng = random.Random(name)
    for _ in range(4):
        args = gen(rng)
        # A fresh library per call: globals start from their initial values,
        # as the circuit's do after reset.
        want = getattr(ctypes.CDLL(_copy(lib_path)), name)(*args)
        assert c.run(*args) == want, (name, args)


_copies = iter(range(1 << 30))


def _copy(path: str) -> str:
    """dlopen caches by path; a copy gets fresh globals."""
    dst = f"{path}.{next(_copies)}.so"
    Path(dst).write_bytes(Path(path).read_bytes())
    return dst


def test_store_splits_a_block_only_when_needed(build):
    ir, _ = build
    assert not any(b.name.endswith(".1") for b in lower(read_function(ir, "fill")).schedule.blocks)
    assert any(b.name.endswith(".1") for b in lower(read_function(ir, "peek")).schedule.blocks)


def test_schedule_shows_memory(build):
    s = str(lower(read_function(build[0], "histogram")).schedule)
    assert "clear hist" in s and "read hist[" in s and "hist[%" in s


def test_blueprint_round_trip_with_memory(build):
    c = lower(read_function(build[0], "histogram"))
    want = c.run(3, 1)
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []
    sim = Simulator(back)
    bus = next(e for e in back.entities if e.position == c.bus.entity.position).out
    sim.run_until(lambda s: s.read(bus, STEP) == c.halt)
    sim.step()
    assert sim.read(bus, c.result) == want


def test_reset_clears_memory_for_a_rerun(build):
    c = lower(read_function(build[0], "bump"))
    sim = Simulator(c.netlist)
    c.params[0].signals = {c.param_signals[0]: 2}
    c.reset.enabled = False
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step()
    first = sim.read(c.bus, c.result)
    c.reset.enabled = True
    sim.step(5)
    c.reset.enabled = False
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step()
    assert sim.read(c.bus, c.result) == first == 50609  # w = {5, 6, 9}, not {5, 6, 11}



def test_reset_held_long_then_released(build):
    # While reset holds S at 1 no gate may fire, or the store to g2[0] would
    # leak into every word of g2 (cells copy the bus's V while S <= 1).
    c = lower(read_function(build[0], "peek"))
    c.params[0].signals = {c.param_signals[0]: 7}
    c.reset.enabled = True
    sim = Simulator(c.netlist)
    sim.step(20)
    c.reset.enabled = False
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step()
    assert sim.read(c.bus, c.result) == 7


def test_constant_byte_gep_into_a_global():
    # Newer clang folds big[392] into a byte offset.
    ir = """
@big = dso_local global [400 x i32] zeroinitializer, align 16
define dso_local i32 @f(i32 %n) {
entry:
  store i32 %n, ptr getelementptr inbounds nuw (i8, ptr @big, i64 1568), align 16
  %1 = load i32, ptr getelementptr inbounds nuw (i8, ptr @big, i64 1568), align 16
  ret i32 %1
}
"""
    assert lower(read_function(ir, "f")).run(7) == 7
