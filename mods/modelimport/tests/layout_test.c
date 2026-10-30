// Host tests for node_layout.h (aki-roadmap#161). Build and run: tests/run.ps1
#include <stdio.h>
#include "../src/node_layout.h"

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

int main(void) {
    const unsigned int s = 0xB8;

    // No Mercy / VPW2: every part has a node, contiguous from part 0.
    unsigned int nm[4] = { 0x80200000u, 0x80200000u + s, 0x80200000u + 2 * s, 0x80200000u + 3 * s };
    CHECK(mi_first_node(nm, 4) == 0);
    CHECK(mi_nodes_contiguous(nm, 4, s));
    CHECK(mi_node_base(nm, 4, s) == 0x80200000u);

    // WM2000: the root (part 0) has no node; part 1's node is the first, and part 0's would be one stride before it.
    unsigned int wm[5] = { 0, 0x80300000u, 0x80300000u + s, 0x80300000u + 2 * s, 0 };   // and a trailing part without one
    CHECK(mi_first_node(wm, 5) == 1);
    CHECK(mi_nodes_contiguous(wm, 5, s));
    CHECK(mi_node_base(wm, 5, s) == 0x80300000u - s);

    // WM2000 slot 2 (run 20261030-064240_wm2k_i161-nodes): part 24 got a node right after part 20's. Out of place over
    // all 25 parts, but the weld only needs parts 0-20.
    unsigned int wm2[25] = { 0 };
    for (int i = 1; i <= 20; i++) wm2[i] = 0x803753D8u + (unsigned int)(i - 1) * s;
    wm2[24] = 0x80376238u;
    CHECK(!mi_nodes_contiguous(wm2, 25, s));
    CHECK(mi_nodes_contiguous(wm2, 21, s));
    CHECK(mi_node_base(wm2, 21, s) == 0x803753D8u - s);

    // A node out of place: not contiguous (welded packs refused).
    unsigned int bad[4] = { 0, 0x80300000u, 0x80300000u + 2 * s, 0 };
    CHECK(!mi_nodes_contiguous(bad, 4, s));

    // No nodes at all: no slot.
    unsigned int none[3] = { 0, 0, 0 };
    CHECK(mi_first_node(none, 3) == -1);
    CHECK(!mi_nodes_contiguous(none, 3, s));
    CHECK(mi_node_base(none, 3, s) == 0);

    printf("[modelimport layout tests] %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
