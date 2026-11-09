// aki_modelimport: the scratch layout (MI_SCRATCH, the 256 KB below 8 MB, so RT64 reads the lists there).
//
//   + 0                     the per-slot lists: 2 parities x MI_MAX_SLOTS x MI_LIST_BYTES
//   + MI_CLEAR_OFFSET       the adapter's depth-clear lists (aki_nodes.c, 0x1000)
//   + MI_TEXT_OFFSET        models.txt (or a conversion's aki.json slots served as it), MI_TEXT_MAX bytes
//   + MI_SCRATCH_USED       the end of what the mod uses (the window check scans through here, aki-roadmap#155)
//
// models.txt used to sit between the lists and the clear (16 KB, ~250 lines). A full replacement roster needs ~300
// lines (aki-roadmap#247), so it moved past the clear: 32 KB, ~500 lines of a conversion's "<pack> wrestler 0x0000
// costume 0" (at most ~60 bytes each). F3DEX 1.x needs more room per list: a forced matrix is four commands, not two
// (aki-roadmap#163).
//
// Pure, header-only (host tests: tests/rules_test.c).
#ifndef MI_SCRATCH_LAYOUT_H
#define MI_SCRATCH_LAYOUT_H

#define MI_MAX_SLOTS 4

#ifdef MI_F3DEX1
#define MI_LIST_BYTES 0xC00                        // per slot and parity: render state + up to 5 commands per entry
#define MI_CLEAR_OFFSET 0xC000u
#else
#define MI_LIST_BYTES 0x800                        // per slot and parity: render state + 3 commands per entry
#define MI_CLEAR_OFFSET 0x8000u
#endif

#define MI_CLEAR_BYTES 0x1000u
#define MI_TEXT_OFFSET (MI_CLEAR_OFFSET + MI_CLEAR_BYTES)
#define MI_TEXT_MAX 0x8000u
#define MI_SCRATCH_USED (MI_TEXT_OFFSET + MI_TEXT_MAX) // 0x11000 (F3DEX2) or 0x15000 (F3DEX 1.x)
#define MI_SCRATCH_SIZE 0x40000u

typedef char mi_lists_fit[(2u * MI_MAX_SLOTS * MI_LIST_BYTES <= MI_CLEAR_OFFSET) ? 1 : -1];
typedef char mi_scratch_fits[(MI_SCRATCH_USED <= MI_SCRATCH_SIZE) ? 1 : -1];

#endif
