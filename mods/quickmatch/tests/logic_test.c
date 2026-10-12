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

static void test_staged_venues(void) {
    CHECK_EQ(qm_start_venue(0, 10, 20, 10), 0);   // arena: as is
    CHECK_EQ(qm_start_venue(9, 10, 20, 10), 9);
    CHECK_EQ(qm_start_venue(10, 10, 20, 10), 0);  // stage: start in its arena
    CHECK_EQ(qm_start_venue(19, 10, 20, 10), 9);
    CHECK_EQ(qm_start_venue(20, 10, 20, 10), 20); // backstage: the game starts demos there itself
    CHECK_EQ(qm_start_venue(12, 0, 0, 0), 12);    // game without staged venues
    CHECK_EQ(qm_area_change_due(11, 45, 45, 0), 1);
    CHECK_EQ(qm_area_change_due(11, 44, 45, 0), 0);      // too early
    CHECK_EQ(qm_area_change_due(11, 0xFFFF, 45, 0), 0);  // counter not started (0xFFFF)
    CHECK_EQ(qm_area_change_due(11, 60, 45, 1), 0);      // fade-in running or another change pending
    CHECK_EQ(qm_area_change_due(-1, 60, 45, 0), 0);      // nothing pending
}

static void test_menu_steps(void) {
    static const qm_menu_step steps[] = {
        { 0, QM_CURSOR_KEEP, 0x1000 }, { 5, QM_CURSOR_MATCH, 0x8000 }, { 3, QM_CURSOR_VENUE, 0x8000 },
        { 4, QM_CURSOR_PER_MATCH, 0x8000 }, { 17, 0, 0x8000 },
    };
    CHECK_EQ(qm_menu_find(steps, 5, 3) == &steps[2], 1);
    CHECK_EQ(qm_menu_find(steps, 5, 0) == &steps[0], 1); // screen 0 is a real screen
    CHECK_EQ(qm_menu_find(steps, 5, 34) == 0, 1);        // unknown screen (the match): hands off
    CHECK_EQ(qm_menu_find(steps, 0, 3) == 0, 1);
    // Cursor values
    static const unsigned char watch[] = { 2, 5, 3, 2 }; // WM2000's "Watch" item per match type
    CHECK_EQ(qm_per_match(watch, 4, 0), 2);
    CHECK_EQ(qm_per_match(watch, 4, 1), 5);
    CHECK_EQ(qm_per_match(watch, 4, 3), 2);
    CHECK_EQ(qm_per_match(watch, 4, 4), -1);  // unknown match type
    CHECK_EQ(qm_per_match(watch, 4, -1), -1);
    CHECK_EQ(qm_menu_cursor(&steps[0], 4, 1, 2), -1);  // keep
    CHECK_EQ(qm_menu_cursor(&steps[1], 4, 3, 2), 3);   // match type
    CHECK_EQ(qm_menu_cursor(&steps[1], 4, -1, 2), -1); // "Game's rotation": keep
    CHECK_EQ(qm_menu_cursor(&steps[2], 6, 0, 2), 6);   // venue
    CHECK_EQ(qm_menu_cursor(&steps[2], 0, 0, 2), 0);   // venue 0 is real
    CHECK_EQ(qm_menu_cursor(&steps[2], -1, 0, 2), -1); // "Game's pick": keep
    CHECK_EQ(qm_menu_cursor(&steps[3], 0, 0, 5), 5);   // per match type (tag "Watch")
    CHECK_EQ(qm_menu_cursor(&steps[3], 0, 0, -1), -1); // unknown match type: keep
    CHECK_EQ(qm_menu_cursor(&steps[4], 3, 3, 2), 0);   // fixed value
    // Press timing: settle first, then repeat
    CHECK_EQ(qm_menu_press_due(19, 100, 20, 30), 0);
    CHECK_EQ(qm_menu_press_due(20, 100, 20, 30), 1);
    CHECK_EQ(qm_menu_press_due(50, 29, 20, 30), 0);
    CHECK_EQ(qm_menu_press_due(50, 30, 20, 30), 1);
}

static void test_sequences(void) {
    static const unsigned short seq[] = { 0x0400, 0x0100, 0x0400, 0x8000 };
    CHECK_EQ(qm_seq_buttons(seq, 4, 0), 0x0400);
    CHECK_EQ(qm_seq_buttons(seq, 4, 1), 0x0100);
    CHECK_EQ(qm_seq_buttons(seq, 4, 3), 0x8000);
    CHECK_EQ(qm_seq_buttons(seq, 4, 9), 0x8000); // used up: keeps pressing the last (A)
    CHECK_EQ(qm_seq_buttons(seq, 4, -1), 0x8000);
    CHECK_EQ(qm_seq_buttons(seq, 0, 0), 0);
}

int main(void) {
    test_idle_timer();
    test_attract_counter();
    test_venue_id();
    test_length_index();
    test_staged_venues();
    test_menu_steps();
    test_sequences();
    printf("[quickmatch tests] %d checks, %d failed\n", checks, failures);
    return failures != 0;
}
