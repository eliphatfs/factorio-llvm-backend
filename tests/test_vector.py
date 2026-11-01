"""Vector ops (factorio.h vadd & co.), checked against the native loops."""

import ctypes
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from factorio_llvm.blueprint import export_string, import_string
from factorio_llvm.frontend import compile_c, read_function
from factorio_llvm.lower import lower

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")

INCLUDE = Path(__file__).parent.parent / "factorio_llvm" / "include"

SRC = r"""
#include <factorio.h>
int big[800], big2[800];
#define N 9
int a[N], b[N], d[N];
static void fill(int x, int y) {
    for (int i = 0; i < N; i++) { a[i] = (x * (i + 3)) % 7 - 3; b[i] = (y * (i + 1)) % 5 - 2; }
}
static int digest(void) {
    int h = 0;
    for (int i = 0; i < N; i++) h = h * 31 + d[i];
    return h;
}
#define OP2(name) int t_##name(int x, int y) { fill(x, y); name(d, a, b); return digest(); }
#define OPK(name, k) int t_##name(int x, int y) { fill(x, y); name(d, a, k); return digest(); }
OP2(vadd) OP2(vsub) OP2(vsel) OP2(vlt) OP2(vle) OP2(vgt) OP2(vge) OP2(veq) OP2(vne)
OPK(vaddk, 5) OPK(vmulk, -3) OPK(vandk, 6) OPK(vshlk, 2) OPK(vshrk, 1)
OPK(vltk, 1) OPK(vlek, 1) OPK(vgtk, -1) OPK(vgek, 0) OPK(veqk, 0) OPK(vnek, 2)
int t_inplace(int x, int y) { fill(x, y); vadd(a, a, b); vsub(a, a, b); vadd(d, a, a); return digest(); }
int t_sum(int x, int y) { fill(x, y); vmulk(d, a, 2); return vsum(d) * 100 + vsum(b); }
int t_chain(int x, int y) {
    fill(x, y);
    int s = 0;
    for (int f = 0; f < 3; f++) {
        vaddk(a, a, 1);
        if (x > f) vsel(d, a, b); /* guarded */
        s += vsum(d) + a[f];
    }
    return s * 1000 + digest();
}
int t_shift(int x, int y) {
    fill(x, y);
    vshift(d, a, 1); int h = digest();
    vshift(d, b, -3); h = h * 7 + digest();
    vshift(a, a, 1); vshift(b, a, 1); /* two by 1 in one block: one a step */
    vadd(d, a, b); return h * 7 + digest();
}
int t_shift_chunks(int x, int y) {
    for (int i = 0; i < 800; i += 7) big[i] = i * x + y;
    vshift(big2, big, 395); vshift(big, big2, -400);
    return big2[0] * 10000 + big2[3] * 100 + big[399] + big[402] * 3 + big[799] * 5;
}
int t_chunks(int x, int y) {
    for (int i = 0; i < 800; i += 13) big[i] = i * x - y;
    vaddk(big2, big, 3);
    vgtk(big, big2, 100);
    return vsum(big) * 1000 + vsum(big2) % 1000 + big2[793];
}
"""

NAMES = [l.split("(")[0].split()[-1] for l in SRC.splitlines() if l.startswith("int t_")]
NAMES += ["t_" + n for n in "vadd vsub vsel vlt vle vgt vge veq vne vaddk vmulk vandk vshlk vshrk vltk vlek vgtk vgek veqk vnek".split()]


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    d = tmp_path_factory.mktemp("vec")
    src = d / "vec.c"
    src.write_text(SRC)
    lib = d / "vec.so"
    subprocess.run(["clang", "-O2", "-fwrapv", "-shared", "-fPIC", "-I", str(INCLUDE), "-o", str(lib), str(src)], check=True)
    return compile_c(src), str(lib)


_copies = iter(range(1 << 30))


def _fresh(path: str) -> ctypes.CDLL:
    dst = f"{path}.{next(_copies)}.so"
    Path(dst).write_bytes(Path(path).read_bytes())
    return ctypes.CDLL(dst)


@pytest.mark.parametrize("name", NAMES)
def test_matches_native(build, name):
    ir, lib = build
    c = lower(read_function(ir, name))
    rng = random.Random(name)
    for _ in range(3):
        args = (rng.randint(-9, 9), rng.randint(-9, 9))
        want = getattr(_fresh(lib), name)(*args)
        assert c.run(*args) == want, (name, args)


def test_vector_op_is_constant_time(build):
    s = str(lower(read_function(build[0], "t_chunks")).schedule)
    assert "<- vaddk(big, 3)" in s


def test_blueprint_exports(build):
    c = lower(read_function(build[0], "t_vle"))
    assert import_string(export_string(c.netlist))
