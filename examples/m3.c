/* M3: fixed-size arrays (local and global, read-only tables). */

static const int squares[8] = {0, 1, 4, 9, 16, 25, 36, 49};

int table(int i) { return squares[i & 7] + 1; }

int sieve(int n) {
    int composite[64];
    for (int i = 0; i < 64; i++) composite[i] = 0;
    int count = 0;
    for (int i = 2; i < n && i < 64; i++) {
        if (!composite[i]) {
            count++;
            for (int j = i * i; j < 64; j += i) composite[j] = 1;
        }
    }
    return count;
}

int bubble(int a, int b, int c, int d) {
    int v[4];
    v[0] = a; v[1] = b; v[2] = c; v[3] = d;
    for (int i = 0; i < 4; i++)
        for (int j = 0; j + 1 < 4 - i; j++)
            if (v[j] > v[j + 1]) { int t = v[j]; v[j] = v[j + 1]; v[j + 1] = t; }
    return v[0] * 1000 + v[1] * 100 + v[2] * 10 + v[3];
}

int hist[16];

int histogram(int x, int y) {
    for (int i = 0; i < 16; i++) hist[i] = 0;
    for (int k = 0; k < 20; k++) hist[(x * k + y) & 15]++;
    int best = 0;
    for (int i = 0; i < 16; i++) if (hist[i] > best) best = hist[i];
    return best;
}
