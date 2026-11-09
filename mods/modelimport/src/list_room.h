// aki_modelimport: the room in a slot list (aki-roadmap#175).
//
// Each slot has two lists of MI_LIST_BYTES (one per frame parity) at MI_SCRATCH, back to back. build_list (core.c)
// writes the render state (mi_list_open), one call per pack entry, then the closing state (mi_list_close) and an
// end. A pack may carry up to 64 entries, and 64 welded entries need more than an F3DEX2 list holds, so build_list
// checks every entry against the room left (keeping room for the close and the end) and leaves out the rest, with a
// log line. aki-model writes one welded entry or at most one entry per part, so real packs always fit.
//
// Pure, header-only (host tests: tests/list_room_test.c).
#ifndef MI_LIST_ROOM_H
#define MI_LIST_ROOM_H

#include "gbi.h"
#include "scratch_layout.h" // MI_LIST_BYTES (aki-roadmap#247)

// Words one entry writes: a welded entry sets segment 0xD (and 0xE when skinned), calls its list and clears them
// again (up to 5 commands); a part entry forces its part's matrix and calls its list.
#define MI_ENTRY_WORDS_WELDED 10u
#define MI_ENTRY_WORDS_PART (MI_GBI_FORCE_MATRIX_WORDS + 2u)

// Does an entry of entry_words still fit after used_words, with tail_words kept free for the close and the end?
static inline int mi_list_entry_fits(unsigned int used_words, unsigned int entry_words, unsigned int tail_words,
                                     unsigned int list_bytes) {
    unsigned int words = list_bytes / 4u;
    return used_words <= words && tail_words <= words - used_words && entry_words <= words - used_words - tail_words;
}

#endif
