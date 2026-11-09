// Host tests for gbi.h (aki-roadmap#163), built twice by tests/run.ps1: as is (F3DEX2) and with -DMI_F3DEX1. The
// F3DEX2 words are the constants core.c and aki_nodes.c wrote before gbi.h, so the four F3DEX2 games keep their lists;
// the F3DEX 1.x words follow gbi.h (F3DEX_GBI) and what VPW64's emitter func_800029BC writes.
#include <stdio.h>
#include "../src/gbi.h"

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static unsigned int buf[64];
static unsigned int* out;
static int words(void) { return (int)(out - buf); }
#define BEGIN() (out = buf)
#define IS(i, w0, w1) CHECK(buf[(i) * 2] == (w0) && buf[(i) * 2 + 1] == (w1))

int main(void) {
#ifdef MI_F3DEX1
    const char* name = "f3dex1";
    BEGIN(); mi_gbi_dl(&out, 0x00123450u); IS(0, 0x06000000u, 0x00123450u);
    BEGIN(); mi_gbi_enddl(&out); IS(0, 0xB8000000u, 0);
    BEGIN(); mi_gbi_segment(&out, 0xD, 0x00200000u); IS(0, 0xBC003406u, 0x00200000u);
    CHECK(mi_gbi_segment_w0(0xC) == 0xBC003006u);
    CHECK(mi_gbi_segment_w0(0xE) == 0xBC003806u);
    // VPW64's emitter: 0x039E0010, 0x03980010, 0x039A0010, 0x039C0010 at +0, +16, +32, +48; no G_MW_FORCEMTX.
    BEGIN(); mi_gbi_force_matrix(&out, 0x00300038u);
    CHECK(words() == 8 && MI_GBI_FORCE_MATRIX_WORDS == 8);
    IS(0, 0x039E0010u, 0x00300038u); IS(1, 0x03980010u, 0x00300048u);
    IS(2, 0x039A0010u, 0x00300058u); IS(3, 0x039C0010u, 0x00300068u);
    BEGIN(); mi_gbi_texture(&out, 1, 0xFFFFFFFFu); IS(0, 0xBB000001u, 0xFFFFFFFFu);
    BEGIN(); mi_gbi_texture(&out, 0, 0xFFFFFFFFu); IS(0, 0xBB000000u, 0);
    BEGIN(); mi_gbi_othermode_h(&out, MI_OM_H_PIPELINE_TO_CYCLE, 0x00082C00u); IS(0, 0xBA000810u, 0x00082C00u);
    BEGIN(); mi_gbi_othermode_h(&out, MI_OM_H_TEXTLUT, 0); IS(0, 0xBA000E02u, 0);
    BEGIN(); mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7008u); IS(0, 0xB900031Du, 0x0F0A7008u);
    BEGIN(); mi_gbi_othermode_l(&out, MI_OM_L_ZSRCSEL, 4); IS(0, 0xB9000201u, 4);
    // VPW64's emitter loads 32 at a time: 0x04000000 | v0 * 2 << 16 | 0x81FF (n 32, 512 bytes).
    BEGIN(); mi_gbi_vertex(&out, 0x00400000u, 32, 0); IS(0, 0x040081FFu, 0x00400000u);
    BEGIN(); mi_gbi_vertex(&out, 0x00400000u, 4, 0); IS(0, 0x0400103Fu, 0x00400000u);
    BEGIN(); mi_gbi_vertex(&out, 0x00400000u, 2, 30); IS(0, 0x043C081Fu, 0x00400000u);
    BEGIN(); mi_gbi_tri2(&out, 0, 1, 2, 0, 2, 3); IS(0, 0xB1000204u, 0x00000406u);
    // Geometry: a clear and a set, each only if needed; F3DEX 1.x culling and smooth bits.
    CHECK(MI_G_CULL_BACK == 0x2000u && MI_G_CULL_FRONT == 0x1000u && MI_G_SHADING_SMOOTH == 0x200u);
    BEGIN(); mi_gbi_geometry(&out, 0, MI_G_CULL_BACK); CHECK(words() == 2); IS(0, 0xB7000000u, 0x00002000u);
    BEGIN(); mi_gbi_geometry(&out, MI_G_ZBUFFER, 0); CHECK(words() == 2); IS(0, 0xB6000000u, 1);
    BEGIN(); mi_gbi_geometry(&out, MI_G_ZBUFFER, MI_G_LIGHTING | MI_G_CULL_BACK);
    IS(0, 0xB6000000u, 1); IS(1, 0xB7000000u, 0x00022000u);
    BEGIN(); mi_gbi_geometry(&out, MI_G_STATE, MI_G_ZBUFFER | MI_G_SHADE | MI_G_SHADING_SMOOTH);
    IS(0, 0xB6000000u, 0x001F3200u); IS(1, 0xB7000000u, 0x00000205u);
    CHECK(MI_GBI_VTX_CACHE_F3DEX1 == 32);
#else
    const char* name = "f3dex2";
    BEGIN(); mi_gbi_dl(&out, 0x00123450u); IS(0, 0xDE000000u, 0x00123450u);
    BEGIN(); mi_gbi_enddl(&out); IS(0, 0xDF000000u, 0);
    BEGIN(); mi_gbi_segment(&out, 0xD, 0x00200000u); IS(0, 0xDB060034u, 0x00200000u);
    CHECK(mi_gbi_segment_w0(0xC) == 0xDB060030u);
    CHECK(mi_gbi_segment_w0(0xE) == 0xDB060038u);
    BEGIN(); mi_gbi_force_matrix(&out, 0x00300038u);
    CHECK(words() == 4 && MI_GBI_FORCE_MATRIX_WORDS == 4);
    IS(0, 0xDC38000Eu, 0x00300038u); IS(1, 0xDB0C0000u, 0x00010000u);
    BEGIN(); mi_gbi_texture(&out, 1, 0xFFFFFFFFu); IS(0, 0xD7000002u, 0xFFFFFFFFu);
    BEGIN(); mi_gbi_texture(&out, 0, 0xFFFFFFFFu); IS(0, 0xD7000000u, 0);
    BEGIN(); mi_gbi_othermode_h(&out, MI_OM_H_PIPELINE_TO_CYCLE, 0x00082C00u); IS(0, 0xE300080Fu, 0x00082C00u);
    BEGIN(); mi_gbi_othermode_h(&out, MI_OM_H_TEXTLUT, 0); IS(0, 0xE3001001u, 0);
    BEGIN(); mi_gbi_othermode_l(&out, MI_OM_L_RENDERMODE, 0x0F0A7008u); IS(0, 0xE200001Cu, 0x0F0A7008u);
    BEGIN(); mi_gbi_othermode_l(&out, MI_OM_L_ZSRCSEL, 4); IS(0, 0xE2001D00u, 4);
    BEGIN(); mi_gbi_vertex(&out, 0x00400000u, 4, 0); IS(0, 0x01004008u, 0x00400000u);
    BEGIN(); mi_gbi_tri2(&out, 0, 1, 2, 0, 2, 3); IS(0, 0x06000204u, 0x00000406u);
    // The geometry words the lists wrote before gbi.h.
    BEGIN(); mi_gbi_geometry(&out, 0, MI_G_CULL_BACK); IS(0, 0xD9FFFFFFu, 0x00000400u);
    BEGIN(); mi_gbi_geometry(&out, MI_G_STATE | MI_G_SHADE | MI_G_ZBUFFER, MI_G_ZBUFFER); IS(0, 0xD9C0F9FAu, 0x00000001u);
    BEGIN(); mi_gbi_geometry(&out, MI_G_STATE, MI_G_ZBUFFER | MI_G_SHADE | MI_G_SHADING_SMOOTH);
    IS(0, 0xD9C0F9FFu, 0x00200005u);
    BEGIN(); mi_gbi_geometry(&out, MI_G_ZBUFFER, MI_G_LIGHTING | MI_G_CULL_BACK); IS(0, 0xD9FFFFFEu, 0x00020400u);
    CHECK(words() == 2);
#endif
    printf("[modelimport gbi tests %s] %d checks, %d failed\n", name, checks, fails);
    return fails ? 1 : 0;
}
