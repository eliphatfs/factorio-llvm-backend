/* One full lamp block: 395 pixels on a 20-wide screen, each on its own signal.
 * Every lamp should light; pixel i gets a distinct non-zero colour.
 * Build: python -m factorio_llvm examples/allsignals.c --screen-width 20
 *
 * 38 of the signals are hidden base-game items (hidden = true in
 * wube/factorio-data 2.1.24). If a lamp stays dark or the blueprint import
 * reports a missing signal, check it against this list (0-based, top-left):
 *   row  2, col  4: artillery-wagon-cannon
 *   row  3, col  0: bottomless-chest
 *   row  3, col  3: burner-generator
 *   row  3, col 16: coin
 *   row  4, col  5: copy-paste-tool
 *   row  4, col  8: cut-paste-tool
 *   row  5, col  4: electric-energy-interface
 *   row  5, col  5: electric-energy-interface-equipment
 *   row  5, col 10: empty-module-slot
 *   row  5, col 19: express-loader
 *   row  6, col  4: fast-loader
 *   row  7, col  1: heat-interface
 *   row  7, col  6: infinity-cargo-wagon
 *   row  7, col  7: infinity-chest
 *   row  7, col  8: infinity-pipe
 *   row  7, col 18: lane-splitter
 *   row  8, col  4: linked-belt
 *   row  8, col  5: linked-chest
 *   row  8, col  6: loader
 *   row  8, col 18: no-item
 *   row  9, col  3: one-way-valve
 *   row  9, col  4: overflow-valve
 *   row 10, col  7: proxy-container
 *   row 11, col  5: rocket-part
 *   row 11, col  8: science
 *   row 11, col  9: selection-tool
 *   row 16, col 15: simple-entity-with-force
 *   row 16, col 16: simple-entity-with-owner
 *   row 17, col  8: spidertron-remote
 *   row 17, col  9: spidertron-rocket-launcher-1
 *   row 17, col 10: spidertron-rocket-launcher-2
 *   row 17, col 11: spidertron-rocket-launcher-3
 *   row 17, col 12: spidertron-rocket-launcher-4
 *   row 18, col 12: tank-cannon
 *   row 18, col 13: tank-flamethrower
 *   row 18, col 14: tank-machine-gun
 *   row 18, col 15: top-up-valve
 *   row 19, col 10: vehicle-machine-gun
 */
extern volatile int screen[395];

int main(void) {
    for (int i = 0; i < 395; i++)
        screen[i] = 0x100000 + i * 0x2713;
    return 0;
}
