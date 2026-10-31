/* A tiny factory game on a 256x192 lamp screen (32x24 tiles of 8x8 pixels).

   An endless chest of iron plates, an inserter that lifts plates out of it
   onto a belt, and a little engineer you walk around with the joystick.

   Controls (joystick only):
   - Walk with the 8 directions. The engineer moves one tile per frame.
   - The bottom row is the toolbar: hand, belt, inserter, eraser. Walk onto a
     slot to pick that tool (the picked slot has a white frame).
   - With the belt or inserter tool, every tile you walk off gets one, facing
     the way you walked. Walking over an existing one again re-aims it, so
     that is how you rotate. The eraser removes belts and inserters. The hand
     just walks. The chest can't be overwritten.

   Rules: an inserter takes from the tile behind it (the chest, or a plate on
   a belt) and drops onto the tile in front (an empty belt, or a chest, which
   swallows plates). Each frame a belt moves its plate one tile forward if the
   next belt is free. Plates reaching a chest are counted; game() returns
   that count after `frames` frames.

   python -m factorio_llvm examples/factory.c -f game --args 1000000 --no-copy */
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
enum { S_GROUND, S_CHEST, S_BELT, S_INSERTER, S_ENGINEER, S_PLATE, S_HAND, S_ERASER };

/* generated sprite ROM: 0=ground, 1=chest, 2=belt, 3=inserter, 4=engineer, 5=plate, 6=hand, 7=eraser */
static const int sprites[512] = {
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

int kind[NT];   /* EMPTY / CHEST / BELT / INSERTER */
int dir[NT];    /* 0 east, 1 south, 2 west, 3 north */
int item[NT];   /* a plate on this belt, or in this inserter's hand */
int moved[NT];  /* frame in which a plate arrived here (moves once a frame) */
int slot[NT];   /* index of this tile in its list */
int marked[NT]; /* frame in which this tile was queued for redraw */
int lists[2 * MAXE]; /* belts, then inserters */
int count[2];        /* how many belts, inserters */
int dirty[NT];
int ndirty, frame;
volatile int joy[8]; /* this frame's joystick, copied once (see read_joystick) */

/* The joystick can change in the middle of a block, and a block that computes
   from it directly could then see half the old and half the new state.
   Copying it to memory first and computing from the copy (a later block,
   since a load after a store to the same array starts one) keeps every frame
   consistent. */
static inline void read_joystick(int *dx, int *dy) {
    for (int k = 0; k < 8; k++) joy[k] = joystick[k];
    *dx = ((joy[JOY_E] | joy[JOY_NE] | joy[JOY_SE]) != 0) - ((joy[JOY_W] | joy[JOY_NW] | joy[JOY_SW]) != 0);
    *dy = ((joy[JOY_S] | joy[JOY_SE] | joy[JOY_SW]) != 0) - ((joy[JOY_N] | joy[JOY_NE] | joy[JOY_NW]) != 0);
}

static inline int step_x(int d) { return (d == 0) - (d == 2); }
static inline int step_y(int d) { return (d == 1) - (d == 3); }

static inline void mark(int t) {
    if (marked[t] != frame) {
        marked[t] = frame;
        dirty[ndirty++] = t;
    }
}

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
    mark(t);
}

/* Redraw one 8x8 tile: ground or a building, a plate, the engineer, and the
   toolbar frame. Directional sprites are drawn facing east and rotated. */
static inline void draw(int t, int ex, int ey, int tool) {
    int tx = t & (TW - 1), ty = t / TW;
    int base, over, d;
    if (ty == TH - 1) {
        /* toolbar: the four tool icons, then black */
        base = tx == T_HAND ? S_ENGINEER : tx == T_BELT ? S_BELT : tx == T_INSERTER ? S_INSERTER : S_ERASER;
        over = -1;
        d = 0;
    } else {
        int k = kind[t];
        base = k == CHEST ? S_CHEST : k == BELT ? S_BELT : k == INSERTER ? S_INSERTER : S_GROUND;
        over = item[t] ? (k == BELT ? S_PLATE : S_HAND) : -1;
        d = dir[t];
    }
    int icon = ty == TH - 1 && tx < 4;
    int framed = icon && tx == tool;
    int hero = tx == ex && ty == ey;
    /* source pixel = c0 + cx * x + cy * y for each rotation */
    int c0 = d == 0 ? 0 : d == 1 ? 56 : d == 2 ? 63 : 7;
    int cx = d == 0 ? 1 : d == 1 ? -8 : d == 2 ? -1 : 8;
    int cy = d == 0 ? 8 : d == 1 ? 1 : d == 2 ? -8 : -1;
    int b0 = base * 64 + c0, o0 = over * 64 + c0;
    int pix = ty * 8 * SCREEN_W + tx * 8;
    for (int y = 0; y < 8; y++) {
        int row = pix + y * SCREEN_W;
        int edge = framed & (y == 0 | y == 7);
#pragma unroll
        for (int x = 0; x < 8; x++) {
            int s = cx * x + cy * y;
            int c = sprites[b0 + s];
            if (!icon && ty == TH - 1) c = BLACK;
            if (over >= 0) {
                int o = sprites[o0 + s];
                if (o) c = o;
            }
            if (hero) {
                int e = sprites[S_ENGINEER * 64 + y * 8 + x];
                if (e) c = e;
            }
            if (edge | (framed & (x == 0 | x == 7))) c = WHITE;
            screen[row + x] = c;
        }
    }
}

int game(int frames) {
    int delivered = 0;
    int ex = 3, ey = SY + 2, tool = T_HAND;

    frame = 1;  /* the setup's redraws go out with the first frame's */
    /* the starting factory: chest -> inserter -> belt running east */
    kind[SY * TW + 1] = CHEST;
    build(SY * TW + 2, INSERTER, 0);
    for (int x = 3; x < BELT_END; x++) build(SY * TW + x, BELT, 0);
    kind[SY * TW + BELT_END] = CHEST;  /* a second chest at the end swallows plates */
    for (int t = 0; t < NT; t++) mark(t);

    for (; frame <= frames; frame++) {
        /* 1. the engineer */
        int dx, dy;
        read_joystick(&dx, &dy);
        int nx = ex + dx, ny = ey + dy;
        nx = nx < 0 ? 0 : nx >= TW ? TW - 1 : nx;
        ny = ny < 0 ? 0 : ny >= TH ? TH - 1 : ny;
        if (nx != ex || ny != ey) {
            int t = ey * TW + ex;
            int d = dx > 0 ? 0 : dx < 0 ? 2 : dy > 0 ? 1 : 3;
            if (ey < TH - 1 && tool != T_HAND)
                build(t, tool == T_BELT ? BELT : tool == T_INSERTER ? INSERTER : EMPTY, d);
            mark(t);
            mark(ny * TW + nx);
            ex = nx;
            ey = ny;
            if (ey == TH - 1 && ex < 4 && ex != tool) {
                mark((TH - 1) * TW + tool);
                tool = ex;
                mark((TH - 1) * TW + tool);
            }
        }

        /* 2. inserters: grab from behind, drop in front */
        for (int i = 0; i < count[1]; i++) {
            int t = lists[MAXE + i], d = dir[t];
            int ddx = step_x(d), ddy = step_y(d);
            int tx = t & (TW - 1), ty = t / TW;
            int sx = tx - ddx, sy = ty - ddy, fx = tx + ddx, fy = ty + ddy;
            if (!item[t]) {
                if (sx >= 0 && sx < TW && sy >= 0 && sy < TH - 1) {
                    int s = sy * TW + sx, k = kind[s];
                    if (k == CHEST) {
                        item[t] = 1;
                        mark(t);
                    } else if (k == BELT && item[s]) {
                        item[s] = 0;
                        item[t] = 1;
                        mark(s);
                        mark(t);
                    }
                }
            } else if (fx >= 0 && fx < TW && fy >= 0 && fy < TH - 1) {
                int f = fy * TW + fx, k = kind[f];
                if (k == CHEST) {
                    item[t] = 0;
                    delivered++;
                    mark(t);
                } else if (k == BELT && !item[f]) {
                    item[f] = 1;
                    moved[f] = frame;
                    item[t] = 0;
                    mark(f);
                    mark(t);
                }
            }
        }

        /* 3. belts: each plate moves one tile forward */
        for (int i = 0; i < count[0]; i++) {
            int t = lists[i];
            if (item[t] && moved[t] != frame) {
                int d = dir[t];
                int fx = (t & (TW - 1)) + step_x(d), fy = (t / TW) + step_y(d);
                if (fx >= 0 && fx < TW && fy >= 0 && fy < TH - 1) {
                    int f = fy * TW + fx, k = kind[f];
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
        }

        /* 4. redraw what changed */
        for (int i = 0; i < ndirty; i++) draw(dirty[i], ex, ey, tool);
        ndirty = 0;
    }
    return delivered;
}
