/* M5: unrolled loops and parallel memory.
 *
 * A block's stores go out together on separate write ports when their
 * addresses provably differ (i, i + 1, ...: what `#pragma unroll` produces),
 * and its loads use separate read ports. Arrays larger than 394 words are
 * split into chunks of 394. */

int sq[64];

/* Parallel: unrolled 4x, so each block writes 4 words at once and the sum
 * reads 4 at once. Returns sum of (i*i + n) for i < 64. */
int squares(int n) {
#pragma unroll 4
    for (int i = 0; i < 64; i++)
        sq[i] = i * i + n;
    int s = 0;
#pragma unroll 4
    for (int i = 0; i < 64; i++)
        s += sq[i];
    return s;
}

int perm[16];

/* Not provably parallel: the addresses come from memory, so the unrolled
 * stores still go one block at a time. Builds a permutation and checks it. */
int scatter(int k) {
#pragma unroll 4
    for (int i = 0; i < 16; i++)
        perm[i] = (i * 5 + k) & 15;
    int inv[16];
#pragma unroll 4
    for (int i = 0; i < 16; i++)
        inv[perm[i]] = i;
    int s = 0;
    for (int i = 0; i < 16; i++)
        s += inv[i] * i;
    return s;
}

int big[1000];

/* Big array: 1000 words in 3 chunks (394 + 394 + 212). Writes a stride-7
 * pattern across all of them and reads back words from each chunk
 * (0 in chunk 0, 399 in chunk 1, 791 and 994 in chunk 2). */
int chunks(int n) {
#pragma unroll 4
    for (int i = 0; i < 1000; i += 7)
        big[i] = i + n;
    return big[0] + big[399] * 3 + big[791] * 5 + big[994] * 7;
}
