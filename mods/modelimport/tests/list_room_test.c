// Host tests for list_room.h (aki-roadmap#175), built twice by tests/run.ps1: F3DEX2 and -DMI_F3DEX1.
//
// Writes a slot list the way core.c build_list does (with aki_nodes.c's mi_list_open / mi_list_close command
// sequences, through the same gbi.h helpers) for a hand-made pack of 64 welded skinned entries (the most a pack may
// carry, MI_PACK_ENTRIES) and for 64 part entries, into a buffer with guard words after MI_LIST_BYTES. Without the
// bound, 64 welded entries ran 624 bytes past an F3DEX2 list into the next one.
#include <stdio.h>
#include "../src/list_room.h"

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

#define LIST_WORDS (MI_LIST_BYTES / 4)
#define GUARD 0xA5A5A5A5u
static unsigned int buf[LIST_WORDS + 4096];

// aki_nodes.c mi_list_open: 7 commands + geometry (+ the CULL_BACK geometry build_list adds).
static unsigned int* list_open(unsigned int* out) {
    mi_gbi_put(&out, 0xE7000000u, 0);
    mi_gbi_dl(&out, 0x0C000000u);
    mi_gbi_put(&out, 0xE7000000u, 0);
    mi_gbi_texture(&out, 1, 0xFFFFFFFFu);
    mi_gbi_othermode_h(&out, MI_OM_H_PIPELINE_TO_CYCLE, 0x00082C00u);
    mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7038u);
    mi_gbi_put(&out, 0xFCFFFFFFu, 0xFFFE7D3Eu);
    mi_gbi_geometry(&out, MI_G_STATE, MI_G_ZBUFFER | MI_G_SHADE | MI_G_SHADING_SMOOTH);
    mi_gbi_geometry(&out, 0, MI_G_CULL_BACK);
    return out;
}

// aki_nodes.c mi_list_close.
static unsigned int* list_close(unsigned int* out) {
    mi_gbi_put(&out, 0xE7000000u, 0);
    mi_gbi_othermode_h(&out, MI_OM_H_TEXTLUT, 0);
    mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7008u);
    mi_gbi_geometry(&out, MI_G_ZBUFFER, MI_G_LIGHTING | MI_G_CULL_BACK);
    return out;
}

static unsigned int* entry(unsigned int* out, int welded) {
    if (welded) {
        mi_gbi_segment(&out, 0xD, 0x00200000u);
        mi_gbi_segment(&out, 0xE, 0x00300000u);
        mi_gbi_dl(&out, 0x00400000u);
        mi_gbi_segment(&out, 0xD, 0);
        mi_gbi_segment(&out, 0xE, 0);
    } else {
        mi_gbi_force_matrix(&out, 0x00500000u);
        mi_gbi_dl(&out, 0x00400000u);
    }
    return out;
}

// build_list's shape: open, entries while they fit, close, end. Returns the entries written; *words = list length.
static int build(int count, int welded, int bounded, unsigned int* words) {
    for (unsigned int i = 0; i < sizeof buf / sizeof buf[0]; i++) buf[i] = GUARD;
    unsigned int tail[32];
    const unsigned int tail_words = (unsigned int)(list_close(tail) - tail) + 2;
    unsigned int* out = list_open(buf);
    int e = 0;
    for (; e < count; e++) {
        const unsigned int need = welded ? MI_ENTRY_WORDS_WELDED : MI_ENTRY_WORDS_PART;
        if (bounded && !mi_list_entry_fits((unsigned int)(out - buf), need, tail_words, MI_LIST_BYTES)) break;
        out = entry(out, welded);
    }
    out = list_close(out);
    mi_gbi_enddl(&out);
    *words = (unsigned int)(out - buf);
    return e;
}

static int guards_intact(void) {
    for (unsigned int i = LIST_WORDS; i < sizeof buf / sizeof buf[0]; i++)
        if (buf[i] != GUARD) return 0;
    return 1;
}

int main(void) {
#ifdef MI_F3DEX1
    const char* name = "f3dex1";
#else
    const char* name = "f3dex2";
#endif
    unsigned int w[8], *o;
    // The entry sizes build_list reserves are what the entries write.
    o = entry(w, 1); CHECK((unsigned int)(o - w) == MI_ENTRY_WORDS_WELDED);
    o = entry(w, 0); CHECK((unsigned int)(o - w) == MI_ENTRY_WORDS_PART);

    // mi_list_entry_fits edges.
    CHECK(mi_list_entry_fits(0, 10, 12, 88));
    CHECK(!mi_list_entry_fits(0, 11, 12, 88));
    CHECK(mi_list_entry_fits(12, 0, 10, 88));
    CHECK(!mi_list_entry_fits(23, 0, 0, 88));          // already past the end
    CHECK(!mi_list_entry_fits(10, 2, 0xFFFFFFFFu, 88)); // no wrap-around
    CHECK(!mi_list_entry_fits(0, 0xFFFFFFFFu, 4, 88));

    unsigned int words;
    int n;
    // 64 welded skinned entries: the old loop overran an F3DEX2 list (aki-roadmap#175); bounded, it stops short.
    n = build(64, 1, 0, &words);
#ifdef MI_F3DEX1
    CHECK(words <= LIST_WORDS && guards_intact());     // F3DEX 1.x lists were big enough anyway
#else
    CHECK(words > LIST_WORDS && !guards_intact());     // the regression: past the list
#endif
    n = build(64, 1, 1, &words);
    CHECK(words <= LIST_WORDS && guards_intact());
    CHECK(buf[words - 2] == (MI_GBI_ENDDL << 24));     // still closed and ended
#ifdef MI_F3DEX1
    CHECK(n == 64);
#else
    CHECK(n > 40 && n < 64);
    printf("[modelimport list_room tests %s] 64 welded entries -> %d fit, %u of %u words\n", name, n, words, LIST_WORDS);
#endif
    // 64 part entries always fit (what aki-model can write).
    n = build(64, 0, 1, &words);
    CHECK(n == 64 && words <= LIST_WORDS && guards_intact());
    // One welded entry (aki-model's welded packs).
    n = build(1, 1, 1, &words);
    CHECK(n == 1 && guards_intact());

    printf("[modelimport list_room tests %s] %d checks, %d failed\n", name, checks, fails);
    return fails ? 1 : 0;
}
