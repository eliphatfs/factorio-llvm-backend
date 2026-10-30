/* M4: memory-mapped I/O. extern arrays are wired to the factory:
 * one the program only reads is an INPUT (signals read live), one it writes
 * is an OUTPUT (each word shown as a signal on the OUTPUT terminal). */

extern volatile int in[2];   /* in[0]: start flag, in[1]: a value */
extern volatile int out[2];  /* out[0]: progress, out[1]: last square */

int wait_and_square(int n) {
    while (!in[0])
        ;                    /* wait for the factory to set in[0] */
    int s = 0;
    for (int i = 1; i <= n; i++) {
        s += in[1] * i;
        out[0] = i;
        out[1] = i * i;
    }
    return s;
}

/* A lamp screen: extern arrays named screen* become a grid of lamps showing
 * each word as a packed 0xRRGGBB colour (here 8 x 8, the default square). */
extern volatile int screen[64];

int draw(int t) {
    for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++)
            screen[y * 8 + x] = ((x + y + t) & 1) ? (x * 32) << 16 | (y * 32) << 8 | 0x40 : 0;
    return t;
}
