// aki_modelimport: is the Expansion RAM window still free? (aki-roadmap#155)
//
// The core loads packs at MI_MEM_BASE and keeps its lists, models.txt and the adapter's clear list at MI_SCRATCH
// (through MI_SCRATCH + 0x9000): Expansion RAM the stock games
// never touch. A conversion can use it: the relinked No Mercy hacks (No Mercy Plus, ECW Born to be Wired) put code,
// data and a heap there from 0x80400000 up, and a pack loaded over them crashes the game. So before its first write the
// core checks that the window is still all zero; if it isn't, model imports stay off for the session.
//
// Pure, header-only (host tests: tests/window_test.c).
#ifndef MI_WINDOW_CHECK_H
#define MI_WINDOW_CHECK_H

// Index of the first nonzero word of words[0..count), or -1 when they're all zero.
static inline int mi_first_used_word(const volatile unsigned int* words, unsigned int count) {
    for (unsigned int i = 0; i < count; i++) {
        if (words[i] != 0) return (int)i;
    }
    return -1;
}

#endif
