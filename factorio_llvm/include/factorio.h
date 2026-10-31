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
 *   are their own entries, not N + E. In game it is a walled cell above and
 *   left of the screen, with 8 sensor gates 7 tiles out around it: switch on
 *   the constant combinator tagged "JOYSTICK" to open the cell's door, step
 *   onto the middle tile, switch it off, then hold WASD to lean towards a gate.
 *   Signals: up-arrow, up-right-arrow, right-arrow, down-right-arrow,
 *   down-arrow, down-left-arrow, left-arrow, up-left-arrow.
 *
 * Screen
 *   Define SCREEN_W and SCREEN_H before the include to get
 *   extern volatile int screen[SCREEN_H * SCREEN_W], a grid of always-on
 *   lamps, row-major, top-left first. Write a packed colour from RGB(); 0 is
 *   off. At most 16 rows. Writes show up a few ticks after the store.
 *
 * Both are read and written like normal arrays (screen[y * SCREEN_W + x] = c).
 * volatile keeps the optimiser from caching joystick reads in a loop.
 */
#ifndef FACTORIO_H
#define FACTORIO_H

enum { JOY_N, JOY_NE, JOY_E, JOY_SE, JOY_S, JOY_SW, JOY_W, JOY_NW };

extern volatile int joystick[8];

/* -1, 0 or 1: x grows to the right, y grows downwards (like the screen). */
static inline int joy_dx(void) {
    return (joystick[JOY_E] | joystick[JOY_NE] | joystick[JOY_SE]) - (joystick[JOY_W] | joystick[JOY_NW] | joystick[JOY_SW]);
}
static inline int joy_dy(void) {
    return (joystick[JOY_S] | joystick[JOY_SE] | joystick[JOY_SW]) - (joystick[JOY_N] | joystick[JOY_NE] | joystick[JOY_NW]);
}

/* A lamp colour from 0..255 channels. */
#define RGB(r, g, b) ((int)(((r) & 255) << 16 | ((g) & 255) << 8 | ((b) & 255)))
#define BLACK RGB(0, 0, 0)
#define WHITE RGB(255, 255, 255)
#define RED RGB(255, 0, 0)
#define GREEN RGB(0, 255, 0)
#define BLUE RGB(0, 0, 255)
#define YELLOW RGB(255, 255, 0)

#if defined(SCREEN_W) && defined(SCREEN_H)
extern volatile int screen[SCREEN_H * SCREEN_W];
/* Tells the compiler how wide to lay out the lamps (like --screen-width). */
__attribute__((used)) static const int screen_width = SCREEN_W;
#endif

#endif
