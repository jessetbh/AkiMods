// Host-side tests for window_check.h (aki-roadmap#155). Run: tests\run.ps1
#include <stdio.h>
#include "../src/window_check.h"

static int failures, checks;

#define CHECK_EQ(got, want) do { \
    int g_ = (got), w_ = (want); checks++; \
    if (g_ != w_) { failures++; printf("FAIL %s:%d: %s = %d, want %d\n", __FILE__, __LINE__, #got, g_, w_); } \
} while (0)

static void test_free_window(void) {
    unsigned int ram[64] = { 0 };
    CHECK_EQ(mi_first_used_word(ram, 64), -1);   // stock game: Expansion RAM untouched
    CHECK_EQ(mi_first_used_word(ram, 0), -1);
}

static void test_used_window(void) {
    unsigned int ram[64] = { 0 };
    ram[40] = 0x3C1A8040;                        // a relinked hack's preloaded code in the window
    CHECK_EQ(mi_first_used_word(ram, 64), 40);
    ram[3] = 1;
    CHECK_EQ(mi_first_used_word(ram, 64), 3);    // the first one
    CHECK_EQ(mi_first_used_word(ram, 3), -1);    // only the range asked for
    ram[63] = 0xFFFFFFFFu;
    CHECK_EQ(mi_first_used_word(ram + 41, 23), 22);
}

int main(void) {
    test_free_window();
    test_used_window();
    printf("[modelimport window tests] %d checks, %d failed\n", checks, failures);
    return failures ? 1 : 0;
}
