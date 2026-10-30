// Functions with branches and loops (M2).
//   python -m factorio_llvm examples/m2.c -f gcd --args 84 36

int gcd(int a, int b) {
    while (b != 0) {
        int t = a % b;
        a = b;
        b = t;
    }
    return a;
}

int collatz(int n) {
    int steps = 0;
    while (n > 1) {
        n = (n & 1) ? 3 * n + 1 : n / 2;
        steps++;
    }
    return steps;
}
