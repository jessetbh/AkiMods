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

    // Deferred pack lists (aki-roadmap#163): 8 slot lists of 0xC00 bytes at 0x807C0000, markers at 0x807CC200.
    const unsigned int L = 0x807C0000u, B = 0xC00u, M = 0x807CC200u;
    CHECK(mi_marker_of(L, L, B, M, 8) == M);
    CHECK(mi_marker_of(L + 3 * B, L, B, M, 8) == M + 24);
    CHECK(mi_marker_of(L + 7 * B, L, B, M, 8) == M + 56);
    CHECK(mi_marker_of(L + 8 * B, L, B, M, 8) == 0);     // past the slot lists (models.txt)
    CHECK(mi_marker_of(L + 4, L, B, M, 8) == 0);         // not a list start
    CHECK(mi_marker_of(L - B, L, B, M, 8) == 0);
    CHECK(mi_marker_of(0x80123450u, L, B, M, 8) == 0);   // a game list
    CHECK(mi_marker_index(M + 24, M, 8) == 3);
    CHECK(mi_marker_index(M + 24 - 0x80000000u, M, 8) == 3);   // as the emitter writes it (physical)
    CHECK(mi_marker_index(M + 64, M, 8) == -1);
    CHECK(mi_marker_index(M + 4, M, 8) == -1);
    CHECK(mi_marker_index(M - 8, M, 8) == -1);
    CHECK(mi_marker_index(0, M, 8) == -1);

    printf("[modelimport layout tests] %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
