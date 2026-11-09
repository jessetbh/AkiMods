// The adapter for AKI's node-based wrestler renderer, shared by the games that run it (No Mercy, VPW2, WM2000, Revenge).
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
// node = part 0's + N x 0xB8), which is what welded packs rely on. WM2000's and Revenge's root part has no node; their nodes start
// at part 1 (node_layout.h, aki-roadmap#161/#162).
#include "../adapter.h"
#include "../restore_rules.h"
#include "../node_layout.h"

#if !defined(AN_PROFILE) || !defined(AN_PARTS) || !defined(AN_EMITTER)
#error "define AN_PROFILE, AN_PARTS and AN_EMITTER before including aki_nodes.c"
#endif

// Parts a welded pack's matrices come from: the leading AN_WELD_PARTS must be contiguous; parts after them (bones
// only in the profile) may have nodes anywhere. WM2000 gives part 24 a node of its own, allocated after part 20's, for
// some wrestlers (aki-roadmap#161).
#ifndef AN_WELD_PARTS
#define AN_WELD_PARTS AN_PARTS
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

static void an_nodes(int slot, unsigned int* nodes) {
    for (int i = 0; i < AN_PARTS; i++) nodes[i] = mi_part_node(slot, i);
}

int mi_slot_active(int slot) {
    unsigned int nodes[AN_PARTS];
    an_nodes(slot, nodes);
    return mi_first_node(nodes, AN_PARTS) >= 0 && mi_slot_wrestler(slot) != 0;
}

u32 mi_part_matrix(u32 node, int parity) { return node + AN_NODE_MATRIX + (u32)parity * AN_PARITY_STRIDE; }

int mi_matrices_contiguous(int slot) {
    unsigned int nodes[AN_PARTS];
    an_nodes(slot, nodes);
    return mi_nodes_contiguous(nodes, AN_WELD_PARTS, AN_NODE_STRIDE);
}

// The pack's welded list loads part N's matrix from segment 0xD + N x 0xB8 + 0x38 (the profile's node_stride and
// node_matrix): segment 0xD = part 0's node (where it would be when part 0 has none) + this parity's matrix offset.
// These games use segment 1, not 0xD.
u32 mi_matrix_segment(int slot, int parity) {
    unsigned int nodes[AN_PARTS];
    an_nodes(slot, nodes);
    return mi_node_base(nodes, AN_PARTS, AN_NODE_STRIDE) + (u32)parity * AN_PARITY_STRIDE;
}

// The depth clear (aki-roadmap#130): No Mercy has no depth buffer at all (no gDPSetDepthImage, nothing writes depth), so
// the imported models clear one, once per frame, with a screen-covering quad that only writes far depth (Z_UPD, no
// compare; the blender keeps the framebuffer's colour), and draw with Z_CMP | Z_UPD: their parts occlude each other
// and one model's parts the other's (Banjo's backpack, Snake's gear, Banjo behind Snake). They still don't depth-test
// against the arena or the stock wrestlers, which write no depth.
// Once per frame whatever the slots' draw order: each list calls the clear through segment 0xC (unused by these games),
// the clear ends by pointing 0xC at an empty list, and before the game draws the parts each frame the emitter hook adds
// a gSPSegment pointing 0xC back at the clear (mi_arm_depth_clear). The quad's matrix, vertices and both lists live past
// the core's lists and models.txt (MI_CLEAR_OFFSET, adapter.h). The commands are in the game's microcode (gbi.h).
#define MI_CLEAR (MI_SCRATCH + MI_CLEAR_OFFSET)
#define MI_CLEAR_BODY (MI_CLEAR + 0x80u)    // the quad: depth 1.0 everywhere (up to 32 commands)
#define MI_CLEAR_DL (MI_CLEAR + 0x180u)     // segment 0xC's first target: the body, then 0xC -> the empty list
#define MI_CLEAR_NOOP (MI_CLEAR + 0x1A0u)
#define MI_CLEAR_AFTER (MI_CLEAR + 0x1B0u)  // after the wrestler parts: the body again, then the stock state
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
    u32* out = (u32*)MI_CLEAR_BODY;
    mi_gbi_put(&out, 0xE7000000u, 0);       // G_RDPPIPESYNC
    mi_gbi_texture(&out, 0, 0);             // texture off for the clear
    mi_gbi_othermode_h(&out, MI_OM_H_PIPELINE_TO_CYCLE, 0x00082C00u); // othermode H as the lists set it (1-cycle)
    mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x5F5A4060u); // Z_UPD, IM_RD, FORCE_BL; blender (MEM * 0 + MEM * 1): colour kept
    mi_gbi_othermode_l(&out, MI_OM_L_ZSRCSEL, 0x00000004u); // Z source: primitive depth (G_ZS_PRIM), the largest:
    mi_gbi_put(&out, 0xEE000000u, 0x7FFF0000u); // gDPSetPrimDepth(0x7FFF, 0), so later game draws that test depth pass
    mi_gbi_put(&out, 0xFCFFFFFFu, 0xFFFE7D3Eu); // combiner alpha 1, so an alpha compare the game left on keeps the quad
    mi_gbi_geometry(&out, MI_G_STATE | MI_G_SHADE | MI_G_ZBUFFER, MI_G_ZBUFFER); // G_ZBUFFER only (no culling, lighting or shade)
    mi_gbi_force_matrix(&out, MI_CLEAR - 0x80000000u);
    mi_gbi_vertex(&out, MI_CLEAR + 64 - 0x80000000u, 4, 0);
    mi_gbi_tri2(&out, 0, 1, 2, 0, 2, 3);    // the two triangles
    mi_gbi_put(&out, 0xE7000000u, 0);
    mi_gbi_othermode_l(&out, MI_OM_L_ZSRCSEL, 0); // Z source: the pixel's again
    mi_gbi_enddl(&out);
    out = (u32*)MI_CLEAR_DL;
    mi_gbi_dl(&out, MI_CLEAR_BODY - 0x80000000u);
    mi_gbi_segment(&out, 0xC, MI_CLEAR_NOOP - 0x80000000u); // segment 0xC: the empty list, for the rest of this frame
    mi_gbi_enddl(&out);
    out = (u32*)MI_CLEAR_NOOP;
    mi_gbi_enddl(&out);
    // After the parts (mi_aki_nodes_emit_done): depth back to 1.0, so the imports' depth never outlives the wrestler
    // chunk that drew it (the game's own draws before the next frame's parts, and after the imports stop, test against
    // it: the far ropes), then the state the lists leave (mi_list_close) and texturing on, as the stock parts have it.
    out = (u32*)MI_CLEAR_AFTER;
    mi_gbi_dl(&out, MI_CLEAR_BODY - 0x80000000u);
    mi_gbi_put(&out, 0xE7000000u, 0);
    mi_gbi_othermode_h(&out, MI_OM_H_TEXTLUT, 0);
    mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7008u);
    mi_gbi_geometry(&out, MI_G_ZBUFFER, MI_G_LIGHTING | MI_G_CULL_BACK);
    mi_gbi_texture(&out, 1, 0xFFFFFFFFu);
    mi_gbi_enddl(&out);
}

void mi_arm_depth_clear(u32 gfx) {
    u32 head = RAM32(gfx);
    if (!mi_is_ram(head)) return;
    write_clear();
    RAM32(head) = 0xFE000000u;              // gDPSetDepthImage, the same every frame (see MI_DEPTH)
    RAM32(head + 4) = MI_DEPTH - 0x80000000u;
    RAM32(head + 8) = mi_gbi_segment_w0(0xC); // gSPSegment(0xC, the clear) in the game's list, before the parts
    RAM32(head + 12) = MI_CLEAR_DL - 0x80000000u;
    RAM32(gfx) = head + 16;
}

u32* mi_list_open(u32* out) {
    mi_gbi_put(&out, 0xE7000000u, 0);       // G_RDPPIPESYNC
    mi_gbi_dl(&out, 0x0C000000u);           // the frame's depth clear, the first time (segment 0xC, see above)
    mi_gbi_put(&out, 0xE7000000u, 0);       // (a sync between the clear's triangles and the state below)
    mi_gbi_texture(&out, 1, 0xFFFFFFFFu);   // gSPTexture on, full scale, tile 0 (as the stock parts)
    // othermode H bits 8-23 (dither as the game has it): 1-cycle, G_TP_PERSP, G_TD_CLAMP, G_TL_TILE, G_TT_NONE,
    // G_TF_BILERP, G_TC_FILT, G_CK_NONE. A pack's material sets its own TLUT mode and combiner.
    mi_gbi_othermode_h(&out, MI_OM_H_PIPELINE_TO_CYCLE, 0x00082C00u);
    mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7038u); // the stock parts' render mode plus Z_CMP | Z_UPD
    mi_gbi_put(&out, 0xFCFFFFFFu, 0xFFFE7D3Eu); // combiner SHADE (colour), 1 (alpha): untextured materials
    // Geometry as the game's preamble leaves it, whichever list drew the clear: lighting, fog, texgen, LOD and culling
    // off (pack vertices carry colours); G_ZBUFFER, shade and smooth shading on.
    mi_gbi_geometry(&out, MI_G_STATE, MI_G_ZBUFFER | MI_G_SHADE | MI_G_SHADING_SMOOTH);
    return out;
}

u32* mi_list_close(u32* out) {
    mi_gbi_put(&out, 0xE7000000u, 0);
    mi_gbi_othermode_h(&out, MI_OM_H_TEXTLUT, 0); // TLUT off again (CI4 materials turn it on)
    mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7008u); // the stock render mode (no depth)
    // G_ZBUFFER off; lighting and back-face culling back on, as the stock parts leave them
    mi_gbi_geometry(&out, MI_G_ZBUFFER, MI_G_LIGHTING | MI_G_CULL_BACK);
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

#ifdef AN_DEFER_HUNG_LISTS
// AN_DEFER_HUNG_LISTS (VPW64, aki-roadmap#163): the emitter loads every node's vertices of a group first and calls
// their lists after, so a pack list drawn among them reloads the vertex cache under the stock lists that come after it
// (the other wrestler lost its body whenever it drew later). The host node gets a marker instead: an empty list, one
// per slot list (MI_SCRATCH + k x MI_LIST_BYTES -> marker k), and the return hook appends the pack lists whose markers
// this call drew, after everything the call emitted.
#define MI_MARKERS (MI_CLEAR + 0x200u)
#define MI_MARKER_COUNT (2 * MI_MAX_SLOTS)
static u32 marker_of(u32 dl) { return mi_marker_of(dl, MI_SCRATCH, MI_LIST_BYTES, MI_MARKERS, MI_MARKER_COUNT); }
// The slot list a node's list word stands for: a marker's list, else the word itself.
static u32 list_of(u32 word) {
    int k = mi_marker_index(word, MI_MARKERS, MI_MARKER_COUNT);
    return k < 0 ? word : MI_SCRATCH + (u32)k * MI_LIST_BYTES;
}
#endif

void mi_hang_list(u32 node, u32 dl) {
    RAM32(node + 0x30) = 0;
    RAM16(node + 0x2C) = 0;
#ifdef AN_DEFER_HUNG_LISTS
    u32 m = marker_of(dl);
    if (m) {
        RAM32(m) = MI_GBI_ENDDL << 24;
        RAM32(m + 4) = 0;
        dl = m;
    }
#endif
    RAM32(node + 0x34) = dl;
}

void mi_restore_part(const MiSavedPart* s, u32 dl0, u32 dl1, u32 current_node) {
    // A rebuilt wrestler has new nodes: the old one may be anyone's now, even when its list is 0 (aki-roadmap#167).
    u32 node_dl = RAM32(s->node + 0x34);
#ifdef AN_DEFER_HUNG_LISTS
    node_dl = list_of(node_dl);
#endif
    if (!mi_restore_ok(current_node, s->node, node_dl, dl0, dl1)) return;
    RAM32(s->node + 0x30) = s->w0;
    RAM16(s->node + 0x2C) = (s16)s->w1;
    RAM32(s->node + 0x34) = s->w2;
}

// The emitter draws every slot's visible parts; its parity argument says which matrix half it uses this frame.
// The game's head pointer this call armed the clear for (the caller reloads it after the emitter returns).
static u32 armed_gfx;
static u32 armed_start;     // the head after the arming: where this call's own commands start

RECOMP_HOOK(AN_EMITTER) void mi_aki_nodes_emit(u32 gfx, s32 parity, s32 list) {
    mi_core_emit(parity & 1);
    armed_gfx = 0;
    if (mi_core_replacing()) {
        mi_arm_depth_clear(gfx);
        armed_gfx = gfx;
        armed_start = RAM32(gfx);
    }
}

RECOMP_HOOK_RETURN(AN_EMITTER) void mi_aki_nodes_emit_done(void) {
    if (!armed_gfx) return;
    u32 head = RAM32(armed_gfx);
#ifdef AN_DEFER_HUNG_LISTS
    // The pack lists whose markers this call drew, once each, after the call's own lists.
    if (mi_is_ram(head) && mi_is_ram(armed_start) && armed_start <= head && head - armed_start < 0x10000u) {
        u32 drawn = 0;
        for (u32 p = armed_start; p < head; p += 8) {
            if (RAM32(p) != (MI_GBI_DL << 24)) continue;
            int k = mi_marker_index(RAM32(p + 4), MI_MARKERS, MI_MARKER_COUNT);
            if (k >= 0) drawn |= 1u << k;
        }
        for (u32 k = 0; k < MI_MARKER_COUNT; k++) {
            if (!(drawn & (1u << k))) continue;
            RAM32(head) = MI_GBI_DL << 24;
            RAM32(head + 4) = MI_SCRATCH + k * MI_LIST_BYTES - 0x80000000u;
            head += 8;
        }
    }
#endif
    if (mi_is_ram(head)) {
        RAM32(head) = MI_GBI_DL << 24;      // the after-clear, behind the parts this call drew
        RAM32(head + 4) = MI_CLEAR_AFTER - 0x80000000u;
        RAM32(armed_gfx) = head + 8;
    }
    armed_gfx = 0;
}

AKI_ON_FRAME void mi_aki_nodes_frame(void) { mi_core_frame(); }
