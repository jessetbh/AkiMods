// The display-list commands the core and the aki_nodes adapter write, in the microcode the game runs (aki-roadmap#163).
// No Mercy, VPW2, WM2000 and Revenge run F3DEX2 (the default); VPW64 and World Tour run F3DEX 1.x: build with
// -DMI_F3DEX1 (mod.psd1 Defines). Pure encoding, no RAM access: host tests in tests/gbi_test.c.
//
// What differs in F3DEX 1.x: the opcodes; G_VTX packs v0 * 2, n and the byte length; the othermode commands take
// (shift, length) instead of (32 - shift - length, length - 1); gSPTexture's on flag is bit 0, not 1; geometry is
// a clear (G_CLEARGEOMETRYMODE) plus a set (G_SETGEOMETRYMODE) with different culling and smooth-shading bits; and
// a forced matrix is four G_MOVEMEMs of 16 bytes (G_MV_MATRIX_1..4, the order VPW64's emitter writes them; RT64
// loads all 64 bytes on the first and skips the other three, so they must stay together) with no G_MW_FORCEMTX.
// RDP commands (syncs, combiner, prim depth, othermode data words, texture loads) are the same in both.
#ifndef MI_GBI_H
#define MI_GBI_H

// Geometry mode bits (gbi.h G_*): pass these to mi_gbi_geometry.
#define MI_G_ZBUFFER 0x00000001u
#define MI_G_SHADE 0x00000004u
#define MI_G_FOG 0x00010000u
#define MI_G_LIGHTING 0x00020000u
#define MI_G_TEXTURE_GEN 0x00040000u
#define MI_G_TEXTURE_GEN_LINEAR 0x00080000u
#define MI_G_LOD 0x00100000u
#ifdef MI_F3DEX1
#define MI_G_SHADING_SMOOTH 0x00000200u
#define MI_G_CULL_FRONT 0x00001000u
#define MI_G_CULL_BACK 0x00002000u
#else
#define MI_G_SHADING_SMOOTH 0x00200000u
#define MI_G_CULL_FRONT 0x00000200u
#define MI_G_CULL_BACK 0x00000400u
#endif
// Everything the lists turn off before setting their own state (fog, lighting, texgen, LOD, culling, smooth).
#define MI_G_STATE (MI_G_FOG | MI_G_LIGHTING | MI_G_TEXTURE_GEN | MI_G_TEXTURE_GEN_LINEAR | MI_G_LOD | \
                    MI_G_CULL_FRONT | MI_G_CULL_BACK | MI_G_SHADING_SMOOTH)

// Othermode fields (G_MDSFT_*, shift and length).
#define MI_OM_H_PIPELINE_TO_CYCLE 8, 16  // othermode H bits 8-23: cycle type, persp, detail, LOD, TLUT, filter, convert, key
#define MI_OM_H_TEXTLUT 14, 2
#define MI_OM_L_RENDERMODE 3, 29
#define MI_OM_L_ZSRCSEL 2, 1

static inline void mi_gbi_put(unsigned int** out, unsigned int w0, unsigned int w1) {
    (*out)[0] = w0;
    (*out)[1] = w1;
    *out += 2;
}

#ifdef MI_F3DEX1
#define MI_GBI_DL 0x06u
#define MI_GBI_ENDDL 0xB8u
#define MI_GBI_MOVEWORD 0xBCu
#define MI_GBI_TEXTURE 0xBBu
#define MI_GBI_SETOTHERMODE_L 0xB9u
#define MI_GBI_SETOTHERMODE_H 0xBAu
#define MI_GBI_VTX 0x04u
#define MI_GBI_TRI2 0xB1u
#else
#define MI_GBI_DL 0xDEu
#define MI_GBI_ENDDL 0xDFu
#define MI_GBI_MOVEWORD 0xDBu
#define MI_GBI_TEXTURE 0xD7u
#define MI_GBI_SETOTHERMODE_L 0xE2u
#define MI_GBI_SETOTHERMODE_H 0xE3u
#define MI_GBI_VTX 0x01u
#define MI_GBI_TRI2 0x06u
#endif
#define MI_GBI_VTX_CACHE_F3DEX1 32u
#define MI_GBI_VTX_CACHE_F3DEX2 64u

// gSPDisplayList (a call: the list returns here).
static inline void mi_gbi_dl(unsigned int** out, unsigned int addr) { mi_gbi_put(out, MI_GBI_DL << 24, addr); }

static inline void mi_gbi_enddl(unsigned int** out) { mi_gbi_put(out, MI_GBI_ENDDL << 24, 0); }

// The gSPSegment command words (also written straight into the game's list by the adapter).
static inline unsigned int mi_gbi_segment_w0(unsigned int seg) {
#ifdef MI_F3DEX1
    return (MI_GBI_MOVEWORD << 24) | ((seg * 4) << 8) | 0x06u;   // G_MW_SEGMENT, offset seg * 4
#else
    return (MI_GBI_MOVEWORD << 24) | (0x06u << 16) | (seg * 4);
#endif
}

static inline void mi_gbi_segment(unsigned int** out, unsigned int seg, unsigned int addr) {
    mi_gbi_put(out, mi_gbi_segment_w0(seg), addr);
}

// gSPForceMatrix (addr: 64 bytes of N64 fixed-point matrix).
static inline void mi_gbi_force_matrix(unsigned int** out, unsigned int addr) {
#ifdef MI_F3DEX1
    static const unsigned int index[4] = { 0x9Eu, 0x98u, 0x9Au, 0x9Cu };   // G_MV_MATRIX_1..4
    for (int i = 0; i < 4; i++) mi_gbi_put(out, (0x03u << 24) | (index[i] << 16) | 16u, addr + (unsigned int)i * 16u);
#else
    mi_gbi_put(out, 0xDC38000Eu, addr);                 // G_MOVEMEM 64 bytes to G_MV_MATRIX
    mi_gbi_put(out, (MI_GBI_MOVEWORD << 24) | (0x0Cu << 16), 0x00010000u);   // G_MW_FORCEMTX
#endif
}

// gSPTexture(s, t, level 0, tile 0, on).
static inline void mi_gbi_texture(unsigned int** out, int on, unsigned int st) {
#ifdef MI_F3DEX1
    mi_gbi_put(out, (MI_GBI_TEXTURE << 24) | (on ? 1u : 0u), on ? st : 0);
#else
    mi_gbi_put(out, (MI_GBI_TEXTURE << 24) | (on ? 2u : 0u), on ? st : 0);
#endif
}

// gSPGeometryMode(clear, set): one command in F3DEX2, a clear and a set (each only if needed) in F3DEX 1.x.
static inline void mi_gbi_geometry(unsigned int** out, unsigned int clear, unsigned int set) {
#ifdef MI_F3DEX1
    if (clear) mi_gbi_put(out, 0xB6000000u, clear);
    if (set) mi_gbi_put(out, 0xB7000000u, set);
#else
    mi_gbi_put(out, 0xD9000000u | (~clear & 0x00FFFFFFu), set);
#endif
}

static inline unsigned int mi_gbi_othermode_w0(unsigned int op, unsigned int shift, unsigned int len) {
#ifdef MI_F3DEX1
    return (op << 24) | (shift << 8) | len;
#else
    return (op << 24) | ((32u - shift - len) << 8) | (len - 1u);
#endif
}

static inline void mi_gbi_othermode_h(unsigned int** out, unsigned int shift, unsigned int len, unsigned int data) {
    mi_gbi_put(out, mi_gbi_othermode_w0(MI_GBI_SETOTHERMODE_H, shift, len), data);
}

static inline void mi_gbi_othermode_l(unsigned int** out, unsigned int shift, unsigned int len, unsigned int data) {
    mi_gbi_put(out, mi_gbi_othermode_w0(MI_GBI_SETOTHERMODE_L, shift, len), data);
}

// gSPVertex(addr, n, v0).
static inline void mi_gbi_vertex(unsigned int** out, unsigned int addr, unsigned int n, unsigned int v0) {
#ifdef MI_F3DEX1
    mi_gbi_put(out, (MI_GBI_VTX << 24) | ((v0 * 2u) << 16) | (n << 10) | (n * 16u - 1u), addr);
#else
    mi_gbi_put(out, (MI_GBI_VTX << 24) | (n << 12) | ((v0 + n) << 1), addr);
#endif
}

// gSP2Triangles (flag 0): both microcodes take vertex index * 2.
static inline void mi_gbi_tri2(unsigned int** out, unsigned int a0, unsigned int b0, unsigned int c0,
                               unsigned int a1, unsigned int b1, unsigned int c1) {
    mi_gbi_put(out, (MI_GBI_TRI2 << 24) | (a0 * 2u) << 16 | (b0 * 2u) << 8 | (c0 * 2u),
               (a1 * 2u) << 16 | (b1 * 2u) << 8 | (c1 * 2u));
}

#endif
