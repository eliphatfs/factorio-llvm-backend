/* factorio.h: the in-game devices factorio-llvm can build for you.
 *
 * The compiler finds this header by itself: #include <factorio.h>.
 * Everything here is an ordinary C declaration; the compiler recognises the
 * array names. Using joystick or screen without the include does not
 * compile, since C has no declaration for them.
 *
 * Joystick
 *   extern volatile int joystick[8], read-only. joystick[d] is 1 while the
 *   player walks towards direction d (JOY_N .. JOY_NW), else 0. Diagonals
 *   are their own entries, not N + E. In game it is a 3x2 walled cage left of
 *   the circuit, below the screen, with 8 sensor gates 7 tiles out around it: switch on
 *   the constant combinator tagged "JOYSTICK" to open the cage's door, step
 *   onto the middle tile, switch it off, then hold WASD to lean towards a gate.
 *   Signals: up-arrow, up-right-arrow, right-arrow, down-right-arrow,
 *   down-arrow, down-left-arrow, left-arrow, up-left-arrow.
 *
 * Screen
 *   Define SCREEN_W and SCREEN_H before the include to get
 *   extern volatile int screen[SCREEN_H * SCREEN_W], a grid of always-on
 *   lamps, row-major, top-left first. Write a packed colour from RGB(); 0 is
 *   off. Any size; past 16 rows or columns each 18x18 tiles lose 2x2 lamps to a power substation. Writes show up a few ticks after the store.
 *
 * Clock
 *   ticks() is a counter that adds 1 every game tick (60 a second), from the
 *   moment the blueprint is built; it keeps counting through reset and halt.
 *   Reading it costs one tick, so ticks() - ticks() style timing is exact to
 *   the tick. Wraps after about 414 days.
 *
 * Joystick and screen are read and written like normal arrays (screen[y * SCREEN_W + x] = c).
 * volatile keeps the optimiser from caching joystick reads in a loop.
 */
#ifndef FACTORIO_H
#define FACTORIO_H

enum { JOY_N, JOY_NE, JOY_E, JOY_SE, JOY_S, JOY_SW, JOY_W, JOY_NW };

extern volatile int joystick[8];

/* -1, 0 or 1: x grows to the right, y grows downwards (like the screen). */
static inline int joy_dx(void) {
    return ((joystick[JOY_E] | joystick[JOY_NE] | joystick[JOY_SE]) != 0) - ((joystick[JOY_W] | joystick[JOY_NW] | joystick[JOY_SW]) != 0);
}
static inline int joy_dy(void) {
    return ((joystick[JOY_S] | joystick[JOY_SE] | joystick[JOY_SW]) != 0) - ((joystick[JOY_N] | joystick[JOY_NE] | joystick[JOY_NW]) != 0);
}

extern volatile int factorio_clock[1];

/* Game ticks since the circuit was built. */
static inline int ticks(void) { return factorio_clock[0]; }

/* A lamp colour from 0..255 channels. */
#define RGB(r, g, b) ((int)(((r) & 255) << 16 | ((g) & 255) << 8 | ((b) & 255)))
#define BLACK RGB(0, 0, 0)
#define WHITE RGB(255, 255, 255)
#define RED RGB(255, 0, 0)
#define GREEN RGB(0, 255, 0)
#define BLUE RGB(0, 0, 255)
#define YELLOW RGB(255, 255, 0)

/* Vector ops: whole-array primitives that run on every word at once.
 *
 * An array's words sit side by side on its memory loop, so one combinator
 * can work on all of them in the same tick. These take whole arrays (not
 * pointers), all of one size, and cost a few ticks however long the arrays
 * are: about 3 for the op, and the block gets at least 3 ticks deep. They
 * cost combinators instead: a few per op per 395-word chunk.
 *   vadd(d, a, b)   d[i] = a[i] + b[i]        vsub(d, a, b)  d[i] = a[i] - b[i]
 *   vaddk(d, a, k)  d[i] = a[i] + k           vmulk(d, a, k) d[i] = a[i] * k
 *   vandk(d, a, k)  d[i] = a[i] & k           vshlk / vshrk  d[i] = a[i] << k, >> k
 *   vsel(d, m, a)   d[i] = m[i] ? a[i] : 0
 *   vshift(d, a, k) d[i] = a[i + k], 0 past either end (costs one combinator
 *                   per word for each k used, shared by all vshifts by k)
 *   vlt / vle / vgt / vge / veq / vne(d, a, b)  d[i] = a[i] < b[i] (0 or 1), ...
 *   vltk ... vnek(d, a, k)                     d[i] = a[i] < k, ...
 *   vsum(a)         the sum of all words
 * k is a compile-time constant. Comparisons compute a[i] - b[i], so they are
 * wrong if that overflows. d may be one of the sources. Not for I/O arrays.
 */
#define FV_N_(d) ((int)(sizeof(d) / sizeof((d)[0])))
#ifdef __FACTORIO__
#define FV_DECL2_(k) void __fv_##k(int *, const int *, const int *, int);
#define FV_DECLK_(k) void __fv_##k(int *, const int *, int, int);
FV_DECL2_(add) FV_DECL2_(sub) FV_DECL2_(sel) FV_DECL2_(lt) FV_DECL2_(le) FV_DECL2_(gt) FV_DECL2_(ge) FV_DECL2_(eq) FV_DECL2_(ne)
FV_DECLK_(shift) FV_DECLK_(addk) FV_DECLK_(mulk) FV_DECLK_(andk) FV_DECLK_(shlk) FV_DECLK_(shrk)
FV_DECLK_(ltk) FV_DECLK_(lek) FV_DECLK_(gtk) FV_DECLK_(gek) FV_DECLK_(eqk) FV_DECLK_(nek)
int __fv_sum(const int *, int);
#else /* native builds (tests): plain loops */
#define FV_DEF2_(k, e) static inline void __fv_##k(int *d, const int *a, const int *b, int n) { for (int i = 0; i < n; i++) d[i] = (e); }
#define FV_DEFK_(k, e) static inline void __fv_##k(int *d, const int *a, int c, int n) { for (int i = 0; i < n; i++) d[i] = (e); }
FV_DEF2_(add, (int)((unsigned)a[i] + (unsigned)b[i])) FV_DEF2_(sub, (int)((unsigned)a[i] - (unsigned)b[i]))
FV_DEF2_(sel, a[i] ? b[i] : 0)
FV_DEF2_(lt, a[i] < b[i]) FV_DEF2_(le, a[i] <= b[i]) FV_DEF2_(gt, a[i] > b[i])
FV_DEF2_(ge, a[i] >= b[i]) FV_DEF2_(eq, a[i] == b[i]) FV_DEF2_(ne, a[i] != b[i])
static inline void __fv_shift(int *d, const int *a, int k, int n) {
    int t[n]; /* d may be a */
    for (int i = 0; i < n; i++) t[i] = i + k >= 0 && i + k < n ? a[i + k] : 0;
    for (int i = 0; i < n; i++) d[i] = t[i];
}
FV_DEFK_(addk, (int)((unsigned)a[i] + (unsigned)c)) FV_DEFK_(mulk, (int)((unsigned)a[i] * (unsigned)c))
FV_DEFK_(andk, a[i] & c) FV_DEFK_(shlk, (int)((unsigned)a[i] << (c & 31))) FV_DEFK_(shrk, a[i] >> (c & 31))
FV_DEFK_(ltk, a[i] < c) FV_DEFK_(lek, a[i] <= c) FV_DEFK_(gtk, a[i] > c)
FV_DEFK_(gek, a[i] >= c) FV_DEFK_(eqk, a[i] == c) FV_DEFK_(nek, a[i] != c)
static inline int __fv_sum(const int *a, int n) { unsigned s = 0; for (int i = 0; i < n; i++) s += (unsigned)a[i]; return (int)s; }
#endif
#define vadd(d, a, b) __fv_add(d, a, b, FV_N_(d))
#define vsub(d, a, b) __fv_sub(d, a, b, FV_N_(d))
#define vsel(d, m, a) __fv_sel(d, m, a, FV_N_(d))
#define vlt(d, a, b) __fv_lt(d, a, b, FV_N_(d))
#define vle(d, a, b) __fv_le(d, a, b, FV_N_(d))
#define vgt(d, a, b) __fv_gt(d, a, b, FV_N_(d))
#define vge(d, a, b) __fv_ge(d, a, b, FV_N_(d))
#define veq(d, a, b) __fv_eq(d, a, b, FV_N_(d))
#define vne(d, a, b) __fv_ne(d, a, b, FV_N_(d))
#define vshift(d, a, k) __fv_shift(d, a, k, FV_N_(d))
#define vaddk(d, a, k) __fv_addk(d, a, k, FV_N_(d))
#define vmulk(d, a, k) __fv_mulk(d, a, k, FV_N_(d))
#define vandk(d, a, k) __fv_andk(d, a, k, FV_N_(d))
#define vshlk(d, a, k) __fv_shlk(d, a, k, FV_N_(d))
#define vshrk(d, a, k) __fv_shrk(d, a, k, FV_N_(d))
#define vltk(d, a, k) __fv_ltk(d, a, k, FV_N_(d))
#define vlek(d, a, k) __fv_lek(d, a, k, FV_N_(d))
#define vgtk(d, a, k) __fv_gtk(d, a, k, FV_N_(d))
#define vgek(d, a, k) __fv_gek(d, a, k, FV_N_(d))
#define veqk(d, a, k) __fv_eqk(d, a, k, FV_N_(d))
#define vnek(d, a, k) __fv_nek(d, a, k, FV_N_(d))
#define vsum(a) __fv_sum(a, FV_N_(a))

#if defined(SCREEN_W) && defined(SCREEN_H)
extern volatile int screen[SCREEN_H * SCREEN_W];
/* Tells the compiler how wide to lay out the lamps (like --screen-width). */
__attribute__((used)) static const int screen_width = SCREEN_W;
#endif

#endif
