// Host-side tests for quickmatch_logic.h. Run: tests\run.ps1
#include <stdio.h>
#include "../src/quickmatch_logic.h"

static int failures, checks;

#define CHECK_EQ(got, want) do { \
    int g_ = (got), w_ = (want); checks++; \
    if (g_ != w_) { failures++; printf("FAIL %s:%d: %s = %d, want %d\n", __FILE__, __LINE__, #got, g_, w_); } \
} while (0)

static void test_idle_timer(void) {
    CHECK_EQ(qm_idle_timer(-1, 900), -1);  // not on the title: untouched
    CHECK_EQ(qm_idle_timer(0, 900), 899);  // fires on the game's next increment
    CHECK_EQ(qm_idle_timer(500, 900), 899);
    CHECK_EQ(qm_idle_timer(899, 900), 899);
    CHECK_EQ(qm_idle_timer(900, 900), 900); // never moved backwards
    CHECK_EQ(qm_idle_timer(1200, 900), 1200);
}

static void test_attract_counter(void) {
    // Game's rotation: keep even (demo) counters, skip odd (intro) ones, wrap at the end.
    CHECK_EQ(qm_attract_counter(0, 0, 9), 0);
    CHECK_EQ(qm_attract_counter(1, 0, 9), 2);
    CHECK_EQ(qm_attract_counter(4, 0, 9), 4);
    CHECK_EQ(qm_attract_counter(17, 0, 9), 0);  // 18 = past the last category
    CHECK_EQ(qm_attract_counter(18, 0, 9), 0);
    CHECK_EQ(qm_attract_counter(200, 0, 9), 0);
    // A chosen category always wins.
    CHECK_EQ(qm_attract_counter(0, 1, 9), 0);
    CHECK_EQ(qm_attract_counter(5, 3, 9), 4);
    CHECK_EQ(qm_attract_counter(5, 9, 9), 16);
    // Out-of-range option: treated as the game's rotation.
    CHECK_EQ(qm_attract_counter(3, 10, 9), 4);
}

static void test_venue_id(void) {
    static const unsigned char ids[] = { 5, 0, 24 };
    CHECK_EQ(qm_venue_id(0, ids, 3), -1); // game's pick
    CHECK_EQ(qm_venue_id(1, ids, 3), 5);
    CHECK_EQ(qm_venue_id(2, ids, 3), 0);  // id 0 is a real venue, not "unset"
    CHECK_EQ(qm_venue_id(3, ids, 3), 24);
    CHECK_EQ(qm_venue_id(4, ids, 3), -1); // stale option past the list
    CHECK_EQ(qm_venue_id(-1, ids, 3), -1);
}

static void test_length_index(void) {
    static const unsigned char ids[] = { 0, 7, 8 };
    CHECK_EQ(qm_length_index(0, ids, 3), 0); // first entry is a real choice (no time limit)
    CHECK_EQ(qm_length_index(1, ids, 3), 7);
    CHECK_EQ(qm_length_index(2, ids, 3), 8);
    CHECK_EQ(qm_length_index(3, ids, 3), -1);
    CHECK_EQ(qm_length_index(-1, ids, 3), -1);
}

int main(void) {
    test_idle_timer();
    test_attract_counter();
    test_venue_id();
    test_length_index();
    printf("[quickmatch tests] %d checks, %d failed\n", checks, failures);
    return failures != 0;
}
