// WWF WrestleMania 2000 (USA): No Mercy's node renderer with 25 parts per slot; the root part (0) has no render node,
// parts 1-20 are contiguous at 0xB8 (node_layout.h); 21-24 are bones only (aki-model moves geometry off them). Emitter func_80002B84 (VPW2's, instruction for instruction).
// Evidence: aki-roadmap#160 (runtime probe of actors, nodes, ids and costumes) and AkiTools
// model-templates/profiles/wm2k.json.
#define AN_PROFILE "wm2k"
#define AN_PARTS 25
#define AN_WELD_PARTS 21     // parts 21-24 are bones only (profile static_parts); part 24 can have a node elsewhere
#define AN_EMITTER "func_80002B84"
#include "aki_nodes.c"
