// The interface between the shared core (core.c: packs, models.txt, the per-slot lists) and one game's adapter
// (adapters/<game>.c, picked by adapter.c). The core knows the pack format and F3DEX2; the adapter knows how the game
// stores a wrestler's body parts and when it draws them. Contract: aki-roadmap#130, "runtime contract v1".
#ifndef MI_ADAPTER_H
#define MI_ADAPTER_H

#include "aki_mod.h"

#define MI_MAX_SLOTS 4
#define MI_MAX_PARTS 32

#define RAM8(a) (*(volatile u8*)(a))
#define RAM16(a) (*(volatile s16*)(a))
#define RAM32(a) (*(volatile u32*)(a))

static inline int mi_is_ram(u32 p) { return p >= 0x80000000u && p < 0x80800000u; }

// What a part's node held before the core changed it, so it can be put back.
typedef struct {
    u32 node;
    u32 w0, w1, w2;
} MiSavedPart;

// --- Provided by the adapter -----------------------------------------------------------------------------------
extern const char mi_profile_id[8];       // the profile a pack must be built for (pack header bytes 8..15)
int mi_slots(void);                       // wrestler slots the game draws (at most MI_MAX_SLOTS)
int mi_parts(void);                       // body parts per slot (at most MI_MAX_PARTS)
int mi_slot_active(int slot);             // a wrestler with render nodes is in the slot now
s32 mi_slot_wrestler(int slot);           // the game's wrestler id (what models.txt "wrestler" matches)
s32 mi_slot_costume(int slot);            // the game's costume, or -1 when the adapter can't tell
u32 mi_part_node(int slot, int part);     // the part's render node, 0 if it has none
u32 mi_part_matrix(u32 node, int parity); // the matrix the game wrote into the node for this frame parity
int mi_matrices_contiguous(int slot);     // welded packs: every part's matrix where mi_matrix_segment says
u32 mi_matrix_segment(int slot, int parity); // what segment 0xD points at for a welded pack (virtual)
u32* mi_list_open(u32* out);              // render state before a pack's lists
u32* mi_list_close(u32* out);             // what the game's own parts expect after them
void mi_save_part(u32 node, MiSavedPart* s);
int mi_part_draws(const MiSavedPart* s);  // the part had geometry of its own (the list is hung on the first such)
void mi_hide_part(u32 node);              // draws nothing
void mi_hang_list(u32 node, u32 dl);      // draws dl instead of its own geometry
void mi_restore_part(const MiSavedPart* s, u32 dl0, u32 dl1); // puts it back if it still shows what the core left

// --- Provided by the core, called from the adapter's hooks ----------------------------------------------------
void mi_core_emit(int parity);            // before the game draws its wrestlers' parts (may run several times a frame)
int mi_core_replacing(void);              // after mi_core_emit: some slot draws an imported model
void mi_core_frame(void);                 // once per frame

void mi_log_str(const char* s);
void mi_log_hex(u32 v, int digits);
void mi_log_dec(s32 v);
void mi_log_end(void);

#endif
