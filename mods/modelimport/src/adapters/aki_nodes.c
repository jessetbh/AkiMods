// The adapter for AKI's node-based wrestler renderer, shared by the games that run it (No Mercy, VPW2; WM2000 next).
// A game's adapter file sets four things and includes this one:
//
//     #define AN_PROFILE "nomercy"           // the profile a pack must be built for (pack header bytes 8..15)
//     #define AN_PARTS 28                    // body parts per slot
//     #define AN_EMITTER "func_800028B4"     // the part emitter, (gfx**, parity, list): hooked to swap the parts
//     #include "aki_nodes.c"
//
// plus, in mod.psd1, MI_ACTORS / MI_WRESTLER_IDS / MI_MEM_* / MI_SCRATCH and, where the game keeps it, MI_COSTUMES
// (s16 per slot) or MI_COSTUMES_U8 (a byte per slot).
//
// The layout (aki-roadmap#130 step 1-3 write-ups for No Mercy, Soldier Ant's VPW2 RE): 4 wrestler slots of AN_PARTS
// part actors, 0xDC bytes each (MI_ACTORS + slot * AN_PARTS * 0xDC). Each part's render node (actor+0x14) has a
// vertex block (+0x30, count +0x2C) that the emitter loads right after forcing the part's matrix (gSPForceMatrix of
// node+0x38+parity*0x40), and a display list (+0x34) drawn over that cache. A slot's nodes are contiguous (part N's
// node = part 0's + N x 0xB8), which is what welded packs rely on.
#include "../adapter.h"

#if !defined(AN_PROFILE) || !defined(AN_PARTS) || !defined(AN_EMITTER)
#error "define AN_PROFILE, AN_PARTS and AN_EMITTER before including aki_nodes.c"
#endif

#define AN_SLOTS 4
#define AN_ACTOR_SIZE 0xDC
#define AN_SLOT_SIZE (AN_PARTS * AN_ACTOR_SIZE)
#define AN_NODE_STRIDE 0xB8
#define AN_NODE_MATRIX 0x38
#define AN_PARITY_STRIDE 0x40

const char mi_profile_id[8] = AN_PROFILE;

int mi_slots(void) { return AN_SLOTS; }
int mi_parts(void) { return AN_PARTS; }

static u32 an_actors(int slot) { return MI_ACTORS + (u32)slot * AN_SLOT_SIZE; }

u32 mi_part_node(int slot, int part) {
    u32 node = RAM32(an_actors(slot) + (u32)part * AN_ACTOR_SIZE + 0x14);
    return mi_is_ram(node) ? node : 0;
}

s32 mi_slot_wrestler(int slot) { return (s32)(u16)RAM16(MI_WRESTLER_IDS + slot * 2); } // as the Log prints it

#if defined(MI_COSTUMES)
// The costume the game built the slot with, stored next to the wrestler id (s16 per slot).
s32 mi_slot_costume(int slot) { return (s32)RAM16(MI_COSTUMES + slot * 2); }
#elif defined(MI_COSTUMES_U8)
// The same, one byte per slot.
s32 mi_slot_costume(int slot) { return (s32)RAM8(MI_COSTUMES_U8 + slot); }
#else
// Not known for this game: models.txt costume rules never match (the Log option prints the wrestler ids).
s32 mi_slot_costume(int slot) { return -1; }
#endif

int mi_slot_active(int slot) { return mi_part_node(slot, 0) != 0 && mi_slot_wrestler(slot) != 0; }

u32 mi_part_matrix(u32 node, int parity) { return node + AN_NODE_MATRIX + (u32)parity * AN_PARITY_STRIDE; }

int mi_matrices_contiguous(int slot) {
    u32 node0 = mi_part_node(slot, 0);
    if (!node0) return 0;
    for (int i = 1; i < AN_PARTS; i++)
        if (mi_part_node(slot, i) != node0 + (u32)i * AN_NODE_STRIDE) return 0;
    return 1;
}

// The pack's welded list loads part N's matrix from segment 0xD + N x 0xB8 + 0x38 (the profile's node_stride and
// node_matrix): segment 0xD = part 0's node + this parity's matrix offset. These games use segment 1, not 0xD.
u32 mi_matrix_segment(int slot, int parity) { return mi_part_node(slot, 0) + (u32)parity * AN_PARITY_STRIDE; }

static void put(u32** out, u32 w0, u32 w1) {
    (*out)[0] = w0;
    (*out)[1] = w1;
    *out += 2;
}

// The depth clear (aki-roadmap#130): No Mercy has no depth buffer at all (no gDPSetDepthImage, nothing writes depth), so
// the imported models clear one, once per frame, with a screen-covering quad that only writes far depth (Z_UPD, no
// compare; the blender keeps the framebuffer's colour), and draw with Z_CMP | Z_UPD: their parts occlude each other
// and one model's parts the other's (Banjo's backpack, Snake's gear, Banjo behind Snake). They still don't depth-test
// against the arena or the stock wrestlers, which write no depth.
// Once per frame whatever the slots' draw order: each list calls the clear through segment 0xC (unused by these games),
// the clear ends by pointing 0xC at an empty list, and before the game draws the parts each frame the emitter hook adds
// a gSPSegment pointing 0xC back at the clear (mi_arm_depth_clear). The quad's matrix, vertices and both lists live past
// the core's scratch (32 KB).
#define MI_CLEAR (MI_SCRATCH + 0x8000u)
#define MI_CLEAR_DL (MI_CLEAR + 0x80u)
#define MI_CLEAR_NOOP (MI_CLEAR + 0x100u)
// The depth image (480 x 240 x 16 bits): the pack window's top 0x38400 bytes. Set once from the emitter hook and never
// reset (aki-roadmap#141): RT64 starts a new framebuffer pair on every depth image change, and its ray tracer gathers only
// the first pair's perspective scene, so setting and resetting it around each list left the imports (and everything
// after them) out of the traced scene. The clear writes the largest depth (primitive depth 0x7FFF), so the game's own
// draws that test depth (the ring ropes' render modes) pass everywhere but behind an import.
#define MI_DEPTH (MI_MEM_BASE + MI_MEM_SIZE)
static void write_clear(void) {
    // Identity MVP but w = 10000 (N64 fixed point: 16 integer halves, then 16 fractions): vertices at (+-20000, +-20000,
    // 9999) cover twice the screen (frame interpolation may move the camera) at depth 0.9999, the far end of the range.
    static const u32 ints[8] = { 0x00010000u, 0, 0x00000001u, 0, 0, 0x00010000u, 0, 0x00002710u };
    for (int i = 0; i < 8; i++) RAM32(MI_CLEAR + i * 4) = ints[i];
    for (int i = 0; i < 8; i++) RAM32(MI_CLEAR + 32 + i * 4) = 0;
    static const s16 xy[4][2] = { { -20000, -20000 }, { 20000, -20000 }, { 20000, 20000 }, { -20000, 20000 } };
    for (int v = 0; v < 4; v++) {
        u32 at = MI_CLEAR + 64 + v * 16;
        RAM16(at + 0) = xy[v][0];
        RAM16(at + 2) = xy[v][1];
        RAM16(at + 4) = 9999;
        RAM16(at + 6) = 0;
        RAM32(at + 8) = 0;
        RAM32(at + 12) = 0;
    }
    u32* out = (u32*)MI_CLEAR_DL;
    put(&out, 0xE7000000u, 0);              // G_RDPPIPESYNC
    put(&out, 0xD7000000u, 0);              // texture off for the clear
    put(&out, 0xE300080Fu, 0x00082C00u);    // othermode H as the lists set it (1-cycle)
    put(&out, 0xE200001Cu, 0x5F5A4060u);    // Z_UPD, IM_RD, FORCE_BL; blender (MEM * 0 + MEM * 1): colour kept
    put(&out, 0xE2001D00u, 0x00000004u);    // Z source: primitive depth (G_ZS_PRIM), the largest:
    put(&out, 0xEE000000u, 0x7FFF0000u);    // gDPSetPrimDepth(0x7FFF, 0), so later game draws that test depth pass
    put(&out, 0xFCFFFFFFu, 0xFFFE7D3Eu);    // combiner alpha 1, so an alpha compare the game left on keeps the quad
    put(&out, 0xD9C0F9FAu, 0x00000001u);    // geometry: G_ZBUFFER only (no culling, lighting or shade)
    put(&out, 0xDC38000Eu, MI_CLEAR - 0x80000000u); // gSPForceMatrix
    put(&out, 0x01004008u, MI_CLEAR + 64 - 0x80000000u); // 4 vertices
    put(&out, 0x06000204u, 0x00000406u);    // the two triangles
    put(&out, 0xE7000000u, 0);
    put(&out, 0xE2001D00u, 0);              // Z source: the pixel's again
    put(&out, 0xDB060030u, MI_CLEAR_NOOP - 0x80000000u); // segment 0xC: the empty list, for the rest of this frame
    put(&out, 0xDF000000u, 0);
    out = (u32*)MI_CLEAR_NOOP;
    put(&out, 0xDF000000u, 0);
}

void mi_arm_depth_clear(u32 gfx) {
    u32 head = RAM32(gfx);
    if (!mi_is_ram(head)) return;
    write_clear();
    RAM32(head) = 0xFE000000u;              // gDPSetDepthImage, the same every frame (see MI_DEPTH)
    RAM32(head + 4) = MI_DEPTH - 0x80000000u;
    RAM32(head + 8) = 0xDB060030u;          // gSPSegment(0xC, the clear) in the game's list, before the parts
    RAM32(head + 12) = MI_CLEAR_DL - 0x80000000u;
    RAM32(gfx) = head + 16;
}

u32* mi_list_open(u32* out) {
    put(&out, 0xE7000000u, 0);              // G_RDPPIPESYNC
    put(&out, 0xDE000000u, 0x0C000000u);    // the frame's depth clear, the first time (segment 0xC, see above)
    put(&out, 0xE7000000u, 0);              // (a sync between the clear's triangles and the state below)
    put(&out, 0xD7000002u, 0xFFFFFFFFu);    // gSPTexture on, full scale, tile 0 (as the stock parts)
    // othermode H bits 8-23 (dither as the game has it): 1-cycle, G_TP_PERSP, G_TD_CLAMP, G_TL_TILE, G_TT_NONE,
    // G_TF_BILERP, G_TC_FILT, G_CK_NONE. A pack's material sets its own TLUT mode and combiner.
    put(&out, 0xE300080Fu, 0x00082C00u);
    put(&out, 0xE200001Cu, 0x0F0A7038u);    // the stock parts' render mode plus Z_CMP | Z_UPD
    put(&out, 0xFCFFFFFFu, 0xFFFE7D3Eu);    // combiner SHADE (colour), 1 (alpha): untextured materials
    // Geometry as the game's preamble leaves it, whichever list drew the clear: lighting, fog, texgen, LOD and culling
    // off (pack vertices carry colours); G_ZBUFFER, shade and smooth shading on.
    put(&out, 0xD9C0F9FFu, 0x00200005u);
    return out;
}

u32* mi_list_close(u32* out) {
    put(&out, 0xE7000000u, 0);
    put(&out, 0xE3001001u, 0);              // TLUT off again (CI4 materials turn it on)
    put(&out, 0xE200001Cu, 0x0F0A7008u);    // the stock render mode (no depth)
    put(&out, 0xD9FFFFFEu, 0x00020400u);    // G_ZBUFFER off; lighting and back-face culling back on, as the stock parts leave them
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
RECOMP_HOOK(AN_EMITTER) void mi_aki_nodes_emit(u32 gfx, s32 parity, s32 list) {
    mi_core_emit(parity & 1);
    if (mi_core_replacing()) mi_arm_depth_clear(gfx);
}

AKI_ON_FRAME void mi_aki_nodes_frame(void) { mi_core_frame(); }
