// Virtual Pro Wrestling 64 (Japan): AKI's node renderer with 23 parts per slot, on F3DEX 1.x (mod.psd1 defines
// MI_F3DEX1: gbi.h writes the lists in its encoding). Emitter func_800029BC: per node, the matrix as four G_MOVEMEMs,
// vertices 32 at a time, then the node's list. Part 0 and 20 have no render node; parts 1-19 are contiguous, and the
// sole pieces 21-22 (the feet's matrices) get the two nodes after part 19's, so a weld only uses parts 0-19. Evidence:
// aki-roadmap#160 (static analysis), the #163 part dump and AkiTools model-templates/profiles/vpw64.json.
#define AN_PROFILE "vpw64"
#define AN_PARTS 23
#define AN_WELD_PARTS 20     // parts 20-22: no node, or a node out of the weld's place (profile nodeless_parts)
#define AN_DEFER_HUNG_LISTS  // the emitter loads a group's vertices before its lists: packs draw after the group
#define AN_EMITTER "func_800029BC"
#include "aki_nodes.c"
