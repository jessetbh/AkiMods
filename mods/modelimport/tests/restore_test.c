// Host-side tests for restore_rules.h (aki-roadmap#167). Run: tests\run.ps1
#include <stdio.h>
#include "../src/restore_rules.h"

static int failures, checks;

#define CHECK_EQ(got, want) do { \
    int g_ = (got), w_ = (want); checks++; \
    if (g_ != w_) { failures++; printf("FAIL %s:%d: %s = %d, want %d\n", __FILE__, __LINE__, #got, g_, w_); } \
} while (0)

#define NODE 0x80371830u
#define OTHER 0x80372DC0u
#define DL0 0x807C0000u
#define DL1 0x807C0800u
#define GAME_DL 0x8032F1B0u

static void test_same_node(void) {
    CHECK_EQ(mi_restore_ok(NODE, NODE, DL0, DL0, DL1), 1);     // shows the slot's list (parity 0)
    CHECK_EQ(mi_restore_ok(NODE, NODE, DL1, DL0, DL1), 1);     // ... or parity 1
    CHECK_EQ(mi_restore_ok(NODE, NODE, 0, DL0, DL1), 1);       // a part the core hid
    CHECK_EQ(mi_restore_ok(NODE, NODE, GAME_DL, DL0, DL1), 0); // the game drew something else there since
}

static void test_rebuilt_wrestler(void) {
    // The VPW2 crash: the actor got a new node; the old one now belongs to another model and shows nothing.
    CHECK_EQ(mi_restore_ok(OTHER, NODE, 0, DL0, DL1), 0);
    CHECK_EQ(mi_restore_ok(OTHER, NODE, DL0, DL0, DL1), 0);
    CHECK_EQ(mi_restore_ok(0, NODE, 0, DL0, DL1), 0);          // the actor has no node any more (scene gone)
    CHECK_EQ(mi_restore_ok(0, 0, 0, DL0, DL1), 0);             // nothing was saved
}

int main(void) {
    test_same_node();
    test_rebuilt_wrestler();
    printf("[modelimport restore tests] %d checks, %d failed\n", checks, failures);
    return failures ? 1 : 0;
}
