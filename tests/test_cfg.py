"""M2: functions with branches and loops, checked against native code."""

import ctypes
import random
import shutil
import subprocess

import pytest

from factorio_llvm.layout import check_reach
from factorio_llvm.netlist import Pole

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.cfg import STEP
from factorio_llvm.frontend import CLANG_FLAGS, compile_c, read_function
from factorio_llvm.lower import lower
from factorio_llvm.sim import Simulator

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

SOURCE = r"""
int gcd(int a, int b) { while (b != 0) { int t = a % b; a = b; b = t; } return a; }
int sumsq(int n) { int s = 0; for (int i = 0; i < n; i++) s += i * i; return s; }
int fib(int n) { int a = 0, b = 1; for (int i = 0; i < n; i++) { int t = a + b; a = b; b = t; } return a; }
int collatz(int n) { int k = 0; while (n > 1) { n = (n & 1) ? 3 * n + 1 : n / 2; k++; } return k; }
int popc(int x) { unsigned u = x; int c = 0; while (u) { c += u & 1; u >>= 1; } return c; }
int nested(int n) { int s = 0; for (int i = 0; i < n; i++) for (int j = 0; j < i; j++) s += i ^ j; return s; }
int early(int a, int b) { if (a < 0) return -1; if (b == 0) return 0; return a / b; }
int sw(int x) { switch (x) { case 1: return 10; case 5: return 7; case 9: return x * 2; case -4: return x - 1; default: return -1; } }
int isprime(int n) { if (n < 2) return 0; for (int d = 2; d * d <= n; d++) if (n % d == 0) return 0; return 1; }
unsigned udivs(unsigned a, unsigned b) { return a / b + a % b; }
int ipow(int b, int e) { int r = 1; while (e > 0) { if (e & 1) r *= b; b *= b; e >>= 1; } return r; }
"""

# name, arity, input generator
SMALL = list(range(-3, 30))
CASES = [
    ("gcd", 2, lambda r: (r.randint(-500, 500), r.randint(-500, 500))),
    ("sumsq", 1, lambda r: (r.choice(SMALL),)),
    ("fib", 1, lambda r: (r.choice(SMALL),)),
    ("collatz", 1, lambda r: (r.randint(-5, 60),)),
    ("popc", 1, lambda r: (r.randint(-(2**31), 2**31 - 1),)),
    ("nested", 1, lambda r: (r.randint(-2, 12),)),
    ("early", 2, lambda r: (r.randint(-50, 50), r.randint(-3, 3))),
    ("sw", 1, lambda r: (r.choice([1, 5, 9, -4, 0, 2, 100, -1]),)),
    ("isprime", 1, lambda r: (r.randint(-3, 120),)),
    ("udivs", 2, lambda r: (r.randint(0, 2**32 - 1), r.choice([1, 2, 3, 7, 1000, 2**31 - 1, 2**31, 2**32 - 1, r.randint(1, 2**32 - 1)]))),
    ("ipow", 2, lambda r: (r.randint(-9, 9), r.randint(-2, 12))),
]


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    d = tmp_path_factory.mktemp("native")
    src, lib = d / "m2.c", d / "m2.so"
    src.write_text(SOURCE)
    subprocess.run(["clang", "-O2", "-fwrapv", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True)
    return ctypes.CDLL(str(lib))


@pytest.fixture(scope="module")
def ir():
    return compile_c(SOURCE)


@pytest.mark.parametrize("name,arity,gen", CASES, ids=[c[0] for c in CASES])
def test_matches_native(name, arity, gen, native, ir):
    compiled = lower(read_function(ir, name))
    rng = random.Random(name)
    for _ in range(25):
        args = gen(rng)
        assert compiled.run(*args) == getattr(native, name)(*args), (name, args)


def test_loop_time_grows_with_trip_count(ir):
    c = lower(read_function(ir, "sumsq"))
    c.run(1)
    short = c.last_ticks
    c.run(20)
    assert c.last_ticks > short + 19 * 3  # at least a few ticks per iteration


def test_halts_and_holds(ir):
    c = lower(read_function(ir, "fib"))
    assert c.run(10) == 55
    sim = Simulator(c.netlist)
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step(50)
    assert sim.read(c.bus, STEP) == c.halt
    assert sim.read(c.bus, c.result) == 55


def test_blueprint_round_trip(ir):
    c = lower(read_function(ir, "gcd"))
    c.run(84, 36)  # bakes the arguments into the constant combinators
    back = import_string(export_string(c.netlist))
    assert any(isinstance(e, Pole) for e in back.entities)
    assert check_reach(back) == []
    sim = Simulator(back)
    bus = next(e for e in back.entities if e.position == c.bus.entity.position).out
    sim.run_until(lambda s: s.read(bus, STEP) == c.halt)
    sim.step()
    assert sim.read(bus, c.result) == 12


def test_clang_flags_avoid_closed_forms():
    assert "-replexitval=never" in CLANG_FLAGS
    text = compile_c("int sum(int n) { int s = 0; for (int i = 0; i < n; i++) s += i; return s; }")
    body = text[text.index("define") :]
    assert "i33" not in body and "i64" not in body


def test_reset_holds_and_reruns(ir):
    c = lower(read_function(ir, "gcd"))
    c.run(84, 36)
    sim = Simulator(c.netlist)
    c.reset.enabled = True
    sim.step(40)
    assert sim.read(c.bus, STEP) == 1
    c.reset.enabled = False
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step()
    assert sim.read(c.bus, c.result) == 12
    # change the arguments, pulse reset, run again
    c.params[0].signals = {c.param_signals[0]: 270}
    c.params[1].signals = {c.param_signals[1]: 192}
    c.reset.enabled = True
    sim.step(3)
    c.reset.enabled = False
    sim.run_until(lambda s: s.read(c.bus, STEP) == c.halt)
    sim.step()
    assert sim.read(c.bus, c.result) == 6


def test_large_program_is_laid_out_and_routed(tmp_path):
    # Four blocks of ~270 values each: more than the 395 signals one net can
    # name, so this needs the per-block local nets and substation relays.
    lines = ["int big(int a, int b) { int x = a, y = b;"]
    for blk in range(4):
        lines.append(f"if (x > {blk}) {{")
        for i in range(45):
            lines.append(f"x = x * {i % 7 + 3} ^ (y + {i}); y = y + (x >> {i % 5 + 1});")
        lines.append("} else { y = y - x; }")
    lines.append("return x + y; }")
    src = tmp_path / "big.c"
    src.write_text("\n".join(lines))
    c = lower(read_function(compile_c(src), "big"))
    assert len(c.netlist.entities) > 900
    want = c.run(5, 7)
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []
    sim = Simulator(back)
    bus = next(e for e in back.entities if e.position == c.bus.entity.position).out
    sim.run_until(lambda s: s.read(bus, STEP) == c.halt)
    sim.step()
    assert sim.read(bus, c.result) == want
