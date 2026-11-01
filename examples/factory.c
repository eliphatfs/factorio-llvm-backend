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
enum { S_GROUND, S_CHEST, S_BELT, S_INSERTER, S_ENGINEER, S_PLATE, S_HAND, S_ERASER, S_BLANK, S_WALK, S_ARM_BACK = S_WALK + 8 };
/* Toolbar tiles get kinds of their own: K_TOOL + tool, and K_OFF past the icons. */
enum { K_TOOL = 4, K_OFF = 8 };

/* What to draw for each kind, and over it (index kind * 2 + item). */
static const int base_sprite[9] = {S_GROUND, S_CHEST, S_BELT, S_INSERTER, S_ENGINEER, S_BELT, S_INSERTER, S_ERASER, S_BLANK};
static const int over_sprite[18] = {
    S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_PLATE, S_ARM_BACK, S_HAND,
    S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_ARM_BACK, S_BLANK, S_BLANK, S_BLANK, S_BLANK, S_BLANK};
/* Rotations: source pixel = rot0 + rotx * x + roty * y, facing east, south, west, north. */
/* walk input (0 stop, 1 up, 2 right, 3 down, 4 left) -> step and facing;
   hold input -> what it builds. Tables, not if/else chains: see draw_row. */
static const int walk_dx[8] = {0, 0, 1, 0, -1, 0, 0, 0};
static const int walk_dy[8] = {0, -1, 0, 1, 0, 0, 0, 0};
static const int walk_dir[8] = {0, 3, 0, 1, 2, 0, 0, 0};
static const int tool_kind[4] = {EMPTY, BELT, INSERTER, EMPTY};
static const int rot0[4] = {0, 56, 63, 7};
static const int rotx[4] = {1, -8, -1, 8};
static const int roty[4] = {8, 1, -8, -1};

/* sprite ROM (from a small art script): 0=ground, 1=chest, 2=belt, 3=inserter base, 4=engineer, 5=plate, 6=inserter arm reaching forward with a plate (white dot),
   7=eraser, 8=blank (all 0: black, or see-through as an overlay),
   9..16=the walking engineer: facing east, south, west, north, each standing then mid-step,
   17=inserter arm reaching back, empty */
static const int sprites[1152] = {
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
    0x2a3a22, 0x2a3a22, 0x606060, 0x606060, 0x606060, 0x606060, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x606060, 0x8a8a8a, 0x8a8a8a, 0x606060, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x606060, 0x8a8a8a, 0x8a8a8a, 0x606060, 0x2a3a22, 0x2a3a22,
    0x2a3a22, 0x2a3a22, 0x606060, 0x606060, 0x606060, 0x606060, 0x2a3a22, 0x2a3a22,
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
    0x000000, 0x000000, 0x000000, 0x000000, 0xf0c020, 0xf0c020, 0xa8b4c0, 0xffffff,
    0x000000, 0x000000, 0x000000, 0x000000, 0xf0c020, 0xf0c020, 0xa8b4c0, 0xffffff,
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
    /* 8: blank */
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0xe0b090, 0xe0b090, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x404858, 0x404858, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x404858, 0x404858, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0xe0b090, 0xe0b090, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x404858, 0x000000, 0x000000, 0x000000, 0x000000, 0x404858, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0x404858, 0x404858, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000,
    0xe07020, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0xe07020,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0x404858, 0x404858, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0xe0b090, 0x000000, 0x000000,
    0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000,
    0xe07020, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0xe07020,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x404858, 0x000000, 0x000000, 0x000000, 0x000000, 0x404858, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0xe0b090, 0xe0b090, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x404858, 0x404858, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x404858, 0x404858, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0xe0b090, 0xe0b090, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe0b090, 0xe0b090, 0xe0b090, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0xe07020, 0xe07020, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x404858, 0x000000, 0x000000, 0x000000, 0x000000, 0x404858, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000,
    0xe07020, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0xe07020,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x604020, 0x604020, 0x604020, 0x604020, 0x000000, 0x000000,
    0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000,
    0xe07020, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0xe07020,
    0x000000, 0x000000, 0xe07020, 0xe07020, 0xe07020, 0xe07020, 0x000000, 0x000000,
    0x000000, 0x000000, 0x404858, 0x000000, 0x000000, 0x404858, 0x000000, 0x000000,
    0x000000, 0x404858, 0x000000, 0x000000, 0x000000, 0x000000, 0x404858, 0x000000,

    /* 17: inserter arm reaching back, empty */
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x303030, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x303030, 0xf0c020, 0xf0c020, 0xf0c020, 0x000000, 0x000000, 0x000000, 0x000000,
    0x303030, 0xf0c020, 0xf0c020, 0xf0c020, 0x000000, 0x000000, 0x000000, 0x000000,
    0x303030, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
    0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000, 0x000000,
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
/* The compiler latches inputs, so a block that reads them sees one
   consistent value even if a button changes mid-block. */
#ifndef HOST_POLL
#define HOST_POLL() /* a native test harness can step its inputs here */
#endif
static inline void read_input(int *w, int *h) {
    HOST_POLL();
    *w = walk[0];
    *h = hold[0];
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
static inline void draw_row(int r, int ex, int ey, int epose, int tool) {
    int t = dirty[r >> 3], y = r & 7;
    int tx = t & (TW - 1), ty = t / TW;
    int k = kind[t], d = dir[t], it = item[t];
    /* Table lookups rather than if/else chains, so this is one block and
       the loop over all rows of all redrawn tiles pipelines. */
    int b0 = base_sprite[k] * 64 + rot0[d], o0 = over_sprite[k * 2 + it] * 64 + rot0[d];
    int cx = rotx[d], cy = roty[d];
    int e0 = (tx == ex && ty == ey ? S_WALK + epose : S_BLANK) * 64;
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
    int face = 1, step = 0; /* facing (as dir[]) and walk phase */

    frame = 1;  /* the setup's redraws go out with the first frame's */
    /* the starting factory: chest -> inserter -> belt running east */
    for (int x = 0; x < TW; x++) kind[(TH - 1) * TW + x] = x < 4 ? K_TOOL + x : K_OFF;
    kind[SY * TW + 1] = CHEST;
    build(SY * TW + 2, INSERTER, 0);
    for (int x = 3; x < BELT_END - 1; x++) build(SY * TW + x, BELT, 0);
    build(SY * TW + BELT_END - 1, INSERTER, 0); /* an inserter unloads the belt */
    kind[SY * TW + BELT_END] = CHEST;            /* into a chest that swallows plates */
    for (int t = 0; t < NT; t++) mark(t);

    for (; frame <= frames; frame++) {
        /* 1. the engineer */
        int w, h;
        read_input(&w, &h);
        h &= 3;
        w &= 7;
        if (h != tool) {
            mark((TH - 1) * TW + tool);
            tool = h;
            mark((TH - 1) * TW + tool);
        }
        int dx = walk_dx[w], dy = walk_dy[w];
        int nx = ex + dx, ny = ey + dy;
        nx = nx < 0 ? 0 : nx >= TW ? TW - 1 : nx;
        ny = ny < 0 ? 0 : ny >= TH - 1 ? TH - 2 : ny;
        int blocked = kind[ny * TW + nx] == CHEST;
        if (blocked) { /* chests block the way */
            nx = ex;
            ny = ey;
        }
        /* The engineer's tile is redrawn only when they move, turn or stop,
           so standing still costs nothing. */
        int nface = w >= 1 && w <= 4 ? walk_dir[w] : face;
        int moving = nx != ex || ny != ey;
        int nstep = moving ? step ^ 1 : 0;
        if (!moving && (nface != face || nstep != step))
            mark(ey * TW + ex);
        face = nface;
        step = nstep;
        if (moving) {
            int t = ey * TW + ex;
            if (tool != T_HAND)
                build(t, tool_kind[tool], walk_dir[w]);
            mark(t);
            mark(ny * TW + nx);
            ex = nx;
            ey = ny;
        } else if (blocked && tool != T_HAND) {
            /* Walking into a chest builds on the engineer's own tile, facing
               the chest: how to put an inserter (or a belt) into one. */
            int t = ey * TW + ex, k = tool_kind[tool], d = walk_dir[w];
            if (kind[t] != k || dir[t] != d)
                build(t, k, d);
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
                if (k == BELT && !item[f]) { /* belts only feed belts; chests need an inserter */
                    item[t] = 0;
                    mark(t);
                    item[f] = 1;
                    moved[f] = frame;
                    mark(f);
                }
            }
        }

        /* 4. redraw what changed */
        for (int r = 0; r < ndirty * 8; r++) draw_row(r, ex, ey, face * 2 + step, tool);
        ndirty = 0;
    }
    return delivered;
}
