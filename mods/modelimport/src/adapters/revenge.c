// WCW/nWo Revenge (USA): No Mercy's node renderer with 23 parts per slot. Emitter func_80002454 (No Mercy's,
// instruction for instruction). The slot setup at 0x8000CB00 stores the wrestler id (packed group << 8 | index + 1)
// at 0x8006A2A4 + slot * 2 and the costume, clamped to the wrestler's costume count, at 0x8007DFB0 + slot * 2.
// Evidence: aki-roadmap#160 (static analysis, Expansion RAM free in a match), #162 and AkiTools
// model-templates/profiles/revenge.json.
#define AN_PROFILE "revenge"
#define AN_PARTS 23
#define AN_WELD_PARTS 21     // parts 21-22 are bones only (profile static_parts); a node of theirs elsewhere mustn't block welds
#define AN_EMITTER "func_80002454"
#include "aki_nodes.c"
