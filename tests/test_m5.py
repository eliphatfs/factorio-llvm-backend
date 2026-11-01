"""M5: unrolled loops, parallel write ports and chunked arrays, against native code."""

import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.frontend import compile_c, read_function
from factorio_llvm.layout import check_reach
from factorio_llvm.lower import lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

EXAMPLE = Path(__file__).parent.parent / "examples" / "m5.c"


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    d = tmp_path_factory.mktemp("m5")
    lib = d / "m5.so"
    subprocess.run(["clang", "-O2", "-fwrapv", "-shared", "-fPIC", "-o", str(lib), str(EXAMPLE)], check=True)
    return compile_c(EXAMPLE), ctypes.CDLL(str(lib))


@pytest.mark.parametrize("name,args", [("squares", [0, 5, -9]), ("scatter", [0, 3, 11]), ("chunks", [0, 2, -4])])
def test_matches_native(build, name, args):
    ir, lib = build
    c = lower(read_function(ir, name))
    for a in args:
        assert c.run(a) == getattr(lib, name)(a), (name, a)
    back = import_string(export_string(c.netlist))
    assert check_reach(back) == []


def _ports(c, block):
    return sorted(st.port for st in c.schedule.block(block).stores)


def test_unrolled_stores_share_a_block(build):
    ir, _ = build
    c = lower(read_function(ir, "squares"))
    assert any(_ports(c, b.name) == [0, 1, 2, 3] for b in c.schedule.blocks)


def test_stores_to_unknown_addresses_take_turns(build):
    ir, _ = build
    c = lower(read_function(ir, "scatter"))
    inv = [b for b in c.schedule.blocks if any(st.array == "inv" for st in b.stores)]
    # They share a block, one step apart (each may overwrite the one before).
    for b in inv:
        steps = sorted(st.step for st in b.stores if st.array == "inv")
        assert steps == list(range(steps[0], steps[0] + len(steps)))


def test_big_array_is_chunked(build):
    ir, _ = build
    c = lower(read_function(ir, "chunks"))
    names = [n.name for n in c.netlist.logical]
    assert {"big[0..]", "big[394..]", "big[788..]"} <= set(names)


@pytest.mark.parametrize("name,arg,want", [("squares", 5, 85664), ("scatter", 3, 992), ("chunks", 2, 12142)])
def test_clang20_ir(name, arg, want):
    # IR from clang 20 (samesign compares, byte-offset constant geps).
    ir = (Path(__file__).parent / "data" / "m5_clang20.ll").read_text()
    c = lower(read_function(ir, name))
    assert c.run(arg) == want
    assert check_reach(import_string(export_string(c.netlist))) == []
