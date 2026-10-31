/* A tiny factory game on a 256x192 lamp screen (32x24 tiles of 8x8 pixels).

   An endless chest of iron plates, an inserter that lifts plates out of it
   onto a belt, and a little engineer you steer from two input terminals.

   Controls: two constant combinators, INPUT @walk and INPUT @hold, each
   holding one signal that stays set (so no input is missed however slow a
   frame is). Set them by pasting a blueprint of a constant combinator onto
   them, which works from remote view too; examples/factory_buttons.txt is a
   blueprint book with one page per setting.
   - walk: 0 stop, 1 up, 2 right, 3 down, 4 left. The engineer moves one tile
     per frame while it is set.
   - hold: 0 nothing, 1 belt, 2 inserter, 3 eraser. The bottom row shows
     which (white frame).
   - Holding a belt or inserter, every tile you walk off gets one, facing the
     way you walked. Walking over an existing one again re-aims it, so that is
     how you rotate. The eraser removes belts and inserters. The chest can't
     be overwritten.

   Rules: an inserter takes from the tile behind it (the chest, or a plate on
   a belt) and drops onto the tile in front (an empty belt, or a chest, which
   swallows plates). Each frame a belt moves its plate one tile forward if the
   next belt is free. Plates reaching a chest are counted; game() returns
   that count after `frames` frames.

   python -m factorio_llvm examples/factory.c -f game --args 1000000000 --no-sim \
       --io walk=signal-W --io hold=signal-H */
#ifndef TW
#define TW 32           /* tiles per row (a power of two) */
#define TH 24           /* tile rows; the last one is the toolbar */
#endif
#define SCREEN_W (TW * 8)
#define SCREEN_H (TH * 8)
#include <factorio.h>

#define NT (TW * TH)
#ifndef SY
#define SY 5          /* row of the starting factory */
#define BELT_END 16
#endif
#define MAXE 96         /* belts or inserters at most, each */

enum { EMPTY, CHEST, BELT, INSERTER };
enum { T_HAND, T_BELT, T_INSERTER, T_ERASER };
enum { S_GROUND, S_CHEST, S_BELT, S_INSERTER, S_ENGINEER, S_PLATE, S_HAND, S_ERASER, S_BLANK };
/* Toolbar tiles get kinds of their own: K_TOOL + tool, and K_OFF past the icons. */
enum { K_TOOL = 4, K_OFF = 8 };

/* What to draw for each kind, and over it (index kind * 2 + item). */
static const int base_sprite[9] = {S_GROUND, S_CHEST, S_BELT, S_INSERTER, S_ENGINEER, S_BELT, S_INSERTER, S_ERASER, S_BLANK};
static const int over_sprite[18] = {
    S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_PLATE, S_BLANK, S_HAND,
    S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK};
/* Rotations: source pixel = rot0 + rotx * x + roty * y, facing east, south, west, north. */
static const int rot0[4] = {0, 56, 63, 7};
static const int rotx[4] = {1, -8, -1, 8};
static const int roty[4] = {8, 1, -8, -1};

/* sprite ROM (from a small art script): 0=ground, 1=chest, 2=belt, 3=inserter, 4=engineer, 5=plate, 6=hand,
   7=eraser, 8=blank (all 0: black, or see-through as an overlay) */
static const int sprites[576] = {
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x324428, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x324428, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x324428, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a,
    0x5a3a1a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x5a3a1a,
    0x5a3a1a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x5a3a1a,
    0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0xf0c020, 0xf0c020, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a,
    0x5a3a1a, 0x8a5a2a, 0x8a5a2a, 0xf0c020, 0xf0c020, 0x8a5a2a, 0x8a5a2a, 0x5a3a1a,
    0x5a3a1a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x5a3a1a,
    0x5a3a1a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x8a5a2a, 0x5a3a1a,
    0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a, 0x5a3a1a,
    0x505050, 0x505050, 0x505050, 0x505050, 0x505050, 0x505050, 0x505050, 0x505050,
    0x303030, 0x303030, 0x303030, 0x303030, 0x303030, 0x303030, 0x303030, 0x303030,
    0x303030, 0xc8a020, 0xc8a020, 0x303030, 0x303030, 0xc8a020, 0xc8a020, 0x303030,
    0x303030, 0x303030, 0xc8a020, 0xc8a020, 0x303030, 0x303030, 0xc8a020, 0xc8a020,
    0x303030, 0x303030, 0xc8a020, 0xc8a020, 0x303030, 0x303030, 0xc8a020, 0xc8a020,
    0x303030, 0xc8a020, 0xc8a020, 0x303030, 0x303030, 0xc8a020, 0xc8a020, 0x303030,
    0x303030, 0x303030, 0x303030, 0x303030, 0x303030, 0x303030, 0x303030, 0x303030,
    0x505050, 0x505050, 0x505050, 0x505050, 0x505050, 0x505050, 0x505050, 0x505050,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0xf0c020, 0xf0c020, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x303030, 0x505050, 0xf0c020, 0xf0c020, 0xf0c020, 0xf0c020, 0xf0c020,
    0x2a3a22, 0x303030, 0x505050, 0xf0c020, 0xf0c020, 0xf0c020, 0xf0c020, 0xf0c020,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0xf0c020, 0xf0c020, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22, 0x2a3a22,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0x404858, 0x404858, 0xe0b090, 0xe0b090, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000,
    0xe07020, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0xe07020,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0xa8b4c0, 0xa8b4c0, 0xa8b4c0, 0xa8b4c0, 0x000000, 0x000000,
    0x000000, 0x000000, 0xa8b4c0, 0xd8e0e8, 0xd8e0e8, 0xa8b4c0, 0x000000, 0x000000,
    0x000000, 0x000000, 0xa8b4c0, 0xd8e0e8, 0xd8e0e8, 0xa8b4c0, 0x000000, 0x000000,
    0x000000, 0x000000, 0xa8b4c0, 0xa8b4c0, 0xa8b4c0, 0xa8b4c0, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0xa8b4c0, 0xa8b4c0,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0xa8b4c0, 0xd8e0e8,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0xa8b4c0, 0xd8e0e8,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0xa8b4c0, 0xa8b4c0,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0xe02020, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0xe02020,
    0x000000, 0xe02020, 0x000000, 0x000000, 0x000000, 0x000000, 0xe02020, 0x000000,
    0x000000, 0x000000, 0xe02020, 0x000000, 0x000000, 0xe02020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe02020, 0xe02020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe02020, 0xe02020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe02020, 0x000000, 0x000000, 0xe02020, 0x000000, 0x000000,
    0x000000, 0xe02020, 0x000000, 0x000000, 0x000000, 0x000000, 0xe02020, 0x000000,
    0xe02020, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0xe02020,
};

#define OFF NT    /* a tile index past the field: always EMPTY */
int kind[NT + 1];   /* EMPTY / CHEST / BELT / INSERTER (or a toolbar kind) */
int dir[NT];        /* 0 east, 1 south, 2 west, 3 north */
int item[NT + 1];   /* a plate on this belt, or in this inserter's hand */
int moved[NT + 1];  /* frame in which a plate arrived here (moves once a frame) */
int slot[NT];       /* index of this tile in its list */
int ahead[NT];      /* the tile this belt or inserter faces, or OFF */
int behind[NT];     /* the tile behind it, or OFF */
int lists[2 * MAXE]; /* belts, then inserters */
int count[2];        /* how many belts, inserters */
int dirty[2 * NT]; /* tiles to redraw this frame (setup queues all of them) */
int ndirty, frame;
extern volatile int walk[1], hold[1]; /* the two input terminals */
volatile int input[2]; /* this frame's inputs, copied once (see read_input) */

/* An input can change in the middle of a block, and a block that computes
   from it directly could then see half the old and half the new state.
   Copying it to memory first and computing from the copy (a later block,
   since a load after a store to the same array starts one) keeps every frame
   consistent. */
#ifndef HOST_POLL
#define HOST_POLL() /* a native test harness can step its inputs here */
#endif
static inline void read_input(int *w, int *h) {
    HOST_POLL();
    input[0] = walk[0];
    input[1] = hold[0];
    *w = input[0];
    *h = input[1];
}
static inline int step_x(int d) { return (d == 0) - (d == 2); }
static inline int step_y(int d) { return (d == 1) - (d == 3); }

/* Queue tile t for redraw. A tile queued twice is drawn twice (24 ticks),
   which is cheaper than checking for it on every mark. */
static inline void mark(int t) { dirty[ndirty++] = t; }

/* Belts are list 0 and inserters list 1 of one array (the compiler wants
   every pointer to stay inside one array). */
static inline void unlist(int t) {
    int k = kind[t];
    if (k == BELT || k == INSERTER) {
        int l = k == INSERTER, i = slot[t];
        int last = lists[l * MAXE + --count[l]];
        lists[l * MAXE + i] = last;
        slot[last] = i;
    }
}

/* Put `k` (BELT, INSERTER or EMPTY) facing `d` on tile t. */
/* The tile next to t in direction d, or OFF past the edge of the field. */
static inline int neighbour(int t, int d) {
    int x = (t & (TW - 1)) + step_x(d), y = t / TW + step_y(d);
    return x >= 0 && x < TW && y >= 0 && y < TH - 1 ? y * TW + x : OFF;
}

static inline void build(int t, int k, int d) {
    int old = kind[t];
    if (old == CHEST) return;
    if (old != k) {
        unlist(t);
        int l = k == INSERTER;
        if (k != EMPTY && count[l] < MAXE) {
            slot[t] = count[l];
            lists[l * MAXE + count[l]++] = t;
        } else {
            k = EMPTY;
        }
        kind[t] = k;
        item[t] = 0;
    }
    dir[t] = d;
    ahead[t] = neighbour(t, d);
    behind[t] = neighbour(t, (d + 2) & 3);
    mark(t);
}

/* Redraw one pixel row of a queued 8x8 tile: ground or a building, a plate, the engineer, and the
   toolbar frame. Directional sprites are drawn facing east and rotated. */
static inline void draw_row(int r, int ex, int ey, int tool) {
    int t = dirty[r >> 3], y = r & 7;
    int tx = t & (TW - 1), ty = t / TW;
    int k = kind[t], d = dir[t], it = item[t];
    /* Table lookups rather than if/else chains, so this is one block and
       the loop over all rows of all redrawn tiles pipelines. */
    int b0 = base_sprite[k] * 64 + rot0[d], o0 = over_sprite[k * 2 + it] * 64 + rot0[d];
    int cx = rotx[d], cy = roty[d];
    int e0 = (tx == ex && ty == ey ? S_ENGINEER : S_BLANK) * 64;
    int framed = k == K_TOOL + tool;
    int edge = framed & (y == 0 | y == 7);
    int row = (ty * 8 + y) * SCREEN_W + tx * 8;
#pragma unroll
    for (int x = 0; x < 8; x++) {
        int s = cx * x + cy * y;
        int c = sprites[b0 + s];
        int o = sprites[o0 + s];
        int e = sprites[e0 + y * 8 + x];
        c = o ? o : c;
        c = e ? e : c;
        c = edge | (framed & (x == 0 | x == 7)) ? WHITE : c;
        screen[row + x] = c;
    }
}

int game(int frames) {
    int delivered = 0;
    int ex = 3, ey = SY + 2, tool = T_HAND;

    frame = 1;  /* the setup's redraws go out with the first frame's */
    /* the starting factory: chest -> inserter -> belt running east */
    for (int x = 0; x < TW; x++) kind[(TH - 1) * TW + x] = x < 4 ? K_TOOL + x : K_OFF;
    kind[SY * TW + 1] = CHEST;
    build(SY * TW + 2, INSERTER, 0);
    for (int x = 3; x < BELT_END; x++) build(SY * TW + x, BELT, 0);
    kind[SY * TW + BELT_END] = CHEST;  /* a second chest at the end swallows plates */
    for (int t = 0; t < NT; t++) mark(t);

    for (; frame <= frames; frame++) {
        /* 1. the engineer */
        int w, h;
        read_input(&w, &h);
        if (h != tool && h >= T_HAND && h <= T_ERASER) {
            mark((TH - 1) * TW + tool);
            tool = h;
            mark((TH - 1) * TW + tool);
        }
        int dx = (w == 2) - (w == 4), dy = (w == 3) - (w == 1);
        int nx = ex + dx, ny = ey + dy;
        nx = nx < 0 ? 0 : nx >= TW ? TW - 1 : nx;
        ny = ny < 0 ? 0 : ny >= TH - 1 ? TH - 2 : ny;
        if (nx != ex || ny != ey) {
            int t = ey * TW + ex;
            if (tool != T_HAND)
                build(t, tool == T_BELT ? BELT : tool == T_INSERTER ? INSERTER : EMPTY, w == 2 ? 0 : w == 3 ? 1 : w == 4 ? 2 : 3);
            mark(t);
            mark(ny * TW + nx);
            ex = nx;
            ey = ny;
        }

        /* 2. inserters: grab from behind, drop in front */
        for (int i = 0; i < count[1]; i++) {
            int t = lists[MAXE + i];
            if (!item[t]) {
                /* grab every third frame: plates 3 tiles apart read clearly as
                   moving forward (2 apart, a step forward looks like a step back) */
                int s = behind[t], k = kind[s];
                if (frame % 3 == 0 && (k == CHEST || (k == BELT && item[s]))) {
                    if (k == BELT) {
                        item[s] = 0;
                        mark(s);
                    }
                    item[t] = 1;
                    mark(t);
                }
            } else {
                int f = ahead[t], k = kind[f];
                if (k == CHEST || (k == BELT && !item[f])) {
                    if (k == CHEST) {
                        delivered++;
                    } else {
                        item[f] = 1;
                        moved[f] = frame;
                        mark(f);
                    }
                    item[t] = 0;
                    mark(t);
                }
            }
        }

        /* 3. belts: each plate moves one tile forward */
        for (int i = 0; i < count[0]; i++) {
            int t = lists[i];
            if (item[t] && moved[t] != frame) {
                int f = ahead[t], k = kind[f];
                if (k == CHEST || (k == BELT && !item[f])) {
                    item[t] = 0;
                    mark(t);
                    if (k == CHEST) {
                        delivered++;
                    } else {
                        item[f] = 1;
                        moved[f] = frame;
                        mark(f);
                    }
                }
            }
        }

        /* 4. redraw what changed */
        for (int r = 0; r < ndirty * 8; r++) draw_row(r, ex, ey, tool);
        ndirty = 0;
    }
    return delivered;
}
