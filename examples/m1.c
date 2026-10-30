// Straight-line functions the M1 compiler handles.
//   python -m factorio_llvm examples/m1.c -f mix --args 6 7

int mix(int a, int b) {
    return a * b + ((a ^ b) >> 2) - (a & 0xff);
}

int clamp(int x, int lo, int hi) {
    return x < lo ? lo : (x > hi ? hi : x);
}
