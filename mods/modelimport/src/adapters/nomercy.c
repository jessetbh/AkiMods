// WWF No Mercy (USA, Rev 1): AKI's node renderer, 28 parts per slot, emitter func_800028B4. The costume: a byte per
// slot at 0x8007626C (MI_COSTUMES_U8 in mod.psd1), stored by the wrestler build func_800140B4(slot, wrestler,
// costume) between the wrestler id arrays, and passed with the id to the appearance lookup func_8000A900. Evidence: the
// aki-roadmap#130 step 1-3 write-ups.
#define AN_PROFILE "nomercy"
#define AN_PARTS 28
#define AN_EMITTER "func_800028B4"
#include "aki_nodes.c"
