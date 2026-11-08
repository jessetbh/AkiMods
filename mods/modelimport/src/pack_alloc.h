// aki_modelimport: where packs and skin buffers go in the Expansion RAM window, and which pack to unload for room
// (aki-roadmap#170).
//
// A match used to load its packs one after another and free them only between matches (8 at most, ~3.5 MB), too few
// for a Rumble with an import for most of its ~27 wrestlers. Now a pack loads when its wrestler comes in, and when the
// window is full the least recently drawn pack that no slot shows is unloaded. Memory a frame may still draw is never
// reused: the frames already submitted can draw a list up to two frames after the core last hung it (the same delay
// as a Reload), so a block is free only once frame - last_use >= MI_IN_FLIGHT.
//
// Pure, header-only (host tests: tests/alloc_test.c).
#ifndef MI_PACK_ALLOC_H
#define MI_PACK_ALLOC_H

#define MI_IN_FLIGHT 2u

typedef struct { unsigned int base, size; } MiRange; // a used block of the window (size 0: none)

// Is a block last drawn at frame last_use free to reuse at frame now?
static inline int mi_drawn_out(unsigned int now, unsigned int last_use) {
    return now - last_use >= MI_IN_FLIGHT;
}

// Start of the gap of [lo, hi) that begins at c (16-aligned), or 0 when c lies inside a used block or past hi.
// *end: where the gap ends (the next used block, or hi).
static inline unsigned int mi_gap_at(const MiRange* used, int n, unsigned int lo, unsigned int hi, unsigned int c,
    unsigned int* end) {
    c = (c + 15u) & ~15u;
    if (c < lo || c >= hi) return 0;
    unsigned int e = hi;
    for (int i = 0; i < n; i++) {
        if (!used[i].size) continue;
        unsigned int b = used[i].base, t = used[i].base + used[i].size;
        if (c >= b && c < t) return 0;
        if (b >= c && b < e) e = b;
    }
    *end = e;
    return c;
}

// The largest gap of [lo, hi) between the used blocks (any order, may overlap): its start, or 0 when the window is
// full. *size: its length.
static inline unsigned int mi_largest_gap(const MiRange* used, int n, unsigned int lo, unsigned int hi,
    unsigned int* size) {
    unsigned int best = 0, best_size = 0, end;
    for (int i = -1; i < n; i++) {
        if (i >= 0 && !used[i].size) continue;
        unsigned int c = mi_gap_at(used, n, lo, hi, i < 0 ? lo : used[i].base + used[i].size, &end);
        if (c && end - c > best_size) best = c, best_size = end - c;
    }
    *size = best_size;
    return best;
}

// The lowest gap of [lo, hi) that holds bytes (first fit): its start, or 0.
static inline unsigned int mi_first_fit(const MiRange* used, int n, unsigned int lo, unsigned int hi,
    unsigned int bytes) {
    unsigned int best = 0, end;
    for (int i = -1; i < n; i++) {
        if (i >= 0 && !used[i].size) continue;
        unsigned int c = mi_gap_at(used, n, lo, hi, i < 0 ? lo : used[i].base + used[i].size, &end);
        if (c && end - c >= bytes && (!best || c < best)) best = c;
    }
    return best;
}

// The pack to unload for room: loaded, shown by no slot, not keep, drawn out, the least recently drawn; -1 if none.
static inline int mi_pick_evict(const int* loaded, const int* shown, const unsigned int* last_use, int n, int keep,
    unsigned int now) {
    int pick = -1;
    for (int i = 0; i < n; i++) {
        if (!loaded[i] || shown[i] || i == keep || !mi_drawn_out(now, last_use[i])) continue;
        if (pick < 0 || now - last_use[i] > now - last_use[pick]) pick = i;
    }
    return pick;
}

#endif
