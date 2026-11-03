// WCW vs. nWo World Tour (USA): AKI's node renderer with 23 parts per slot, on F3DEX 1.x (mod.psd1 defines
// MI_F3DEX1). Emitter func_80002904 is VPW64's func_800029BC instruction for instruction (list table 0x80041C70): per
// group, every node's matrix and vertices first, then the nodes' lists, so pack lists are deferred past the group
// (AN_DEFER_HUNG_LISTS). The port's per-frame render entry (patches/raytracing.c, func_8000329C) may split a frame
// over several calls. The part layout is VPW64's (part dump). Evidence: aki-roadmap#160, the #164 part dump and
// AkiTools model-templates/profiles/wt.json.
#define AN_PROFILE "wt"
#define AN_PARTS 23
#define AN_WELD_PARTS 20     // as VPW64: parts 0/20 have no node, the soles 21-22 get the nodes after part 19's
#define AN_DEFER_HUNG_LISTS  // the emitter loads a group's vertices before its lists: packs draw after the group
#define AN_EMITTER "func_80002904"
#include "aki_nodes.c"
