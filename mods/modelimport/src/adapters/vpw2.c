// Virtual Pro Wrestling 2 (Japan) adapter. Evidence: the aki-roadmap#130 VPW2 write-ups and AkiTools
// model-templates/profiles/vpw2.json.
//
// VPW2 runs No Mercy's wrestler renderer with 25 parts per slot: 4 wrestler slots of 25 part actors (0xDC bytes each,
// MI_ACTORS + slot * 0x157C, built by func_80014730). Each part's render node (actor+0x14) has a vertex block (+0x30,
// count +0x2C) that the emitter (func_80002B84) loads right after forcing the part's matrix (gSPForceMatrix of
// node+0x38+parity*0x40), and a display list (+0x34) drawn over that cache. A slot's 25 nodes are contiguous (part N's
// node = part 0's + N x 0xB8), which is what welded packs rely on.
#include "../adapter.h"

#define V2_SLOTS 4
#define V2_PARTS 25
#define V2_ACTOR_SIZE 0xDC
#define V2_SLOT_SIZE (V2_PARTS * V2_ACTOR_SIZE)
#define V2_NODE_STRIDE 0xB8
#define V2_NODE_MATRIX 0x38
#define V2_PARITY_STRIDE 0x40

const char mi_profile_id[8] = "vpw2";

int mi_slots(void) { return V2_SLOTS; }
int mi_parts(void) { return V2_PARTS; }

static u32 v2_actors(int slot) { return MI_ACTORS + (u32)slot * V2_SLOT_SIZE; }

u32 mi_part_node(int slot, int part) {
    u32 node = RAM32(v2_actors(slot) + (u32)part * V2_ACTOR_SIZE + 0x14);
    return mi_is_ram(node) ? node : 0;
}

s32 mi_slot_wrestler(int slot) { return (s32)(u16)RAM16(MI_WRESTLER_IDS + slot * 2); } // as the Log prints it

// func_80014730 stores the costume it builds the slot with next to the wrestler id.
s32 mi_slot_costume(int slot) { return (s32)RAM16(MI_COSTUMES + slot * 2); }

int mi_slot_active(int slot) { return mi_part_node(slot, 0) != 0 && mi_slot_wrestler(slot) != 0; }

u32 mi_part_matrix(u32 node, int parity) { return node + V2_NODE_MATRIX + (u32)parity * V2_PARITY_STRIDE; }

int mi_matrices_contiguous(int slot) {
    u32 node0 = mi_part_node(slot, 0);
    if (!node0) return 0;
    for (int i = 1; i < V2_PARTS; i++)
        if (mi_part_node(slot, i) != node0 + (u32)i * V2_NODE_STRIDE) return 0;
    return 1;
}

// The pack's welded list loads part N's matrix from segment 0xD + N x 0xB8 + 0x38: segment 0xD = part 0's node + this
// parity's matrix offset.
u32 mi_matrix_segment(int slot, int parity) { return mi_part_node(slot, 0) + (u32)parity * V2_PARITY_STRIDE; }

static void put(u32** out, u32 w0, u32 w1) {
    (*out)[0] = w0;
    (*out)[1] = w1;
    *out += 2;
}

// The same render state as No Mercy's parts (same engine, same microcode).
u32* mi_list_open(u32* out) {
    put(&out, 0xE7000000u, 0);              // G_RDPPIPESYNC
    put(&out, 0xD7000002u, 0xFFFFFFFFu);    // gSPTexture on, full scale, tile 0
    put(&out, 0xE300080Fu, 0x00082C00u);    // othermode H: 1-cycle, persp, clamp, tile, TT_NONE, bilerp, filt
    put(&out, 0xE200001Cu, 0x0F0A7008u);    // the parts' render mode
    put(&out, 0xFCFFFFFFu, 0xFFFE7D3Eu);    // combiner SHADE (colour), 1 (alpha): untextured materials
    put(&out, 0xD9FDF9FFu, 0);              // lighting off (pack vertices carry colours), no culling
    return out;
}

u32* mi_list_close(u32* out) {
    put(&out, 0xE7000000u, 0);
    put(&out, 0xE3001001u, 0);              // TLUT off again (CI4 materials turn it on)
    put(&out, 0xD9FFFFFFu, 0x00020400u);    // lighting and back-face culling back on
    return out;
}

void mi_save_part(u32 node, MiSavedPart* s) {
    s->node = node;
    s->w0 = RAM32(node + 0x30);
    s->w1 = (u32)(u16)RAM16(node + 0x2C);
    s->w2 = RAM32(node + 0x34);
}

int mi_part_draws(const MiSavedPart* s) { return mi_is_ram(s->w2); }

void mi_hide_part(u32 node) {
    RAM32(node + 0x30) = 0;
    RAM16(node + 0x2C) = 0;
    RAM32(node + 0x34) = 0;
}

void mi_hang_list(u32 node, u32 dl) {
    RAM32(node + 0x30) = 0;
    RAM16(node + 0x2C) = 0;
    RAM32(node + 0x34) = dl;
}

void mi_restore_part(const MiSavedPart* s, u32 dl0, u32 dl1) {
    u32 dl = RAM32(s->node + 0x34);
    if (dl != 0 && dl != dl0 && dl != dl1) return; // the game reused the node: leave it alone
    RAM32(s->node + 0x30) = s->w0;
    RAM16(s->node + 0x2C) = (s16)s->w1;
    RAM32(s->node + 0x34) = s->w2;
}

// The emitter draws every slot's visible parts; its parity argument says which matrix half it uses this frame.
RECOMP_HOOK("func_80002B84") void mi_vpw2_emit(u32 gfx, s32 parity, s32 list) { mi_core_emit(parity & 1); }

AKI_ON_FRAME void mi_vpw2_frame(void) { mi_core_frame(); }
