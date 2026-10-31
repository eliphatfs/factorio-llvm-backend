/* A dot you steer with the joystick on an 8x8 screen. Runs `steps` moves,
   one per joystick poll, and returns the dot's last position (y * 8 + x).

   python -m factorio_llvm examples/joystick.c --function move */
#define SCREEN_W 8
#define SCREEN_H 8
#include <factorio.h>

int move(int steps) {
    int x = 3, y = 3;
    screen[y * SCREEN_W + x] = YELLOW;
    for (int i = 0; i < steps; i++) {
        int nx = x + joy_dx(), ny = y + joy_dy();
        if (nx < 0) nx = 0;
        if (nx >= SCREEN_W) nx = SCREEN_W - 1;
        if (ny < 0) ny = 0;
        if (ny >= SCREEN_H) ny = SCREEN_H - 1;
        if (nx != x || ny != y) {
            screen[y * SCREEN_W + x] = BLACK;
            screen[ny * SCREEN_W + nx] = YELLOW;
            x = nx;
            y = ny;
        }
    }
    return y * SCREEN_W + x;
}
