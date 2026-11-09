// Host tests for pack_alloc.h (aki-roadmap#170). Build and run: tests/run.ps1
#include <stdio.h>
#include "../src/pack_alloc.h"

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

int main(void) {
    const unsigned int lo = 0x80400000u, hi = 0x80780000u;
    unsigned int size;

    // Empty window: all of it.
    CHECK(mi_largest_gap(0, 0, lo, hi, &size) == lo && size == hi - lo);
    CHECK(mi_first_fit(0, 0, lo, hi, 0x1000) == lo);

    // A hole between two packs is found; the end gap is bigger, so largest picks it, first fit the hole.
    MiRange u[4] = { { lo, 0x100000 }, { lo + 0x180000, 0x100000 }, { 0, 0 }, { 0, 0 } };
    CHECK(mi_largest_gap(u, 4, lo, hi, &size) == lo + 0x280000 && size == hi - lo - 0x280000);
    CHECK(mi_first_fit(u, 4, lo, hi, 0x80000) == lo + 0x100000);
    CHECK(mi_first_fit(u, 4, lo, hi, 0x80001) == lo + 0x280000);

    // Best fit (aki-roadmap#247): the hole when it holds the pack (the big end gap stays whole), else the end gap.
    CHECK(mi_best_fit(u, 4, lo, hi, 0x80000) == lo + 0x100000);
    CHECK(mi_best_fit(u, 4, lo, hi, 0x80001) == lo + 0x280000);
    CHECK(mi_best_fit(u, 4, lo, hi, hi - lo) == 0);
    CHECK(mi_best_fit(0, 0, lo, hi, 0x1000) == lo);
    MiRange two[2] = { { lo + 0x10000, 0x10000 }, { lo + 0x30000, hi - lo - 0x30000 } };   // equal holes: the lower
    CHECK(mi_best_fit(two, 2, lo, hi, 0x8000) == lo);

    // Gaps start 16-aligned after an odd-sized block, and an aligned start inside a block is not a gap.
    MiRange odd[1] = { { lo, 0x1001 } };
    CHECK(mi_first_fit(odd, 1, lo, hi, 16) == lo + 0x1010);

    // Full window: nothing.
    MiRange full[2] = { { lo, 0x200000 }, { lo + 0x200000, hi - lo - 0x200000 } };
    CHECK(mi_largest_gap(full, 2, lo, hi, &size) == 0 && size == 0);
    CHECK(mi_first_fit(full, 2, lo, hi, 16) == 0);

    // Overlapping used blocks (a retired skin buffer inside a newer pack's range can't happen, but must not break it).
    MiRange ov[2] = { { lo, 0x2000 }, { lo + 0x1000, 0x2000 } };
    CHECK(mi_first_fit(ov, 2, lo, hi, 16) == lo + 0x3000);

    // In flight: a block drawn this frame or the last isn't free; two frames on it is; frame counter wrap.
    CHECK(!mi_drawn_out(10, 10) && !mi_drawn_out(11, 10) && mi_drawn_out(12, 10));
    CHECK(!mi_drawn_out(0, 0xFFFFFFFFu) && mi_drawn_out(1, 0xFFFFFFFFu));

    // Eviction: least recently drawn of the unshown, drawn-out packs; never the kept one, a shown one, or one in flight.
    int loaded[5] = { 1, 1, 1, 0, 1 }, shown[5] = { 0, 1, 0, 0, 0 };
    unsigned int last[5] = { 50, 10, 40, 0, 99 };
    CHECK(mi_pick_evict(loaded, shown, last, 5, -1, 100) == 2);  // 1 shown, 3 empty, 4 in flight: 2 (older than 0)
    CHECK(mi_pick_evict(loaded, shown, last, 5, 2, 100) == 0);
    last[0] = 99;
    CHECK(mi_pick_evict(loaded, shown, last, 5, 2, 100) == -1);  // 0 and 4 drawn at 99 are still in flight at 100
    last[0] = 50;
    shown[0] = 1;
    CHECK(mi_pick_evict(loaded, shown, last, 5, 2, 100) == -1);

    printf("[modelimport alloc tests] %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
