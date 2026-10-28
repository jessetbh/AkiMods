// aki_modelimport: the shared core (aki-roadmap#130, runtime contract v1).
//
// Model packs are folders <mods>/models/<id>.akimodel/ made by AkiTools aki-model.py. The core reads only their
// model.bin: an AKP1 header (layout below) followed by geometry, display lists and textures laid out for a "built
// base" address, with a table of the address words to move when the pack loads elsewhere. <mods>/models/models.txt
// says which pack replaces which wrestler:
//
//     snake      slot 1                 # a slot (1-4)
//     banjo      wrestler 0x0404        # a wrestler id as the game stores it (costume N optional, any if left out)
//     off banjo                         # turns a pack off, whatever the other lines say
//
// The Mods menu's Slot N pack options beat models.txt (quick tests); then the first matching "slot" line, then the
// first matching "wrestler" line. models.txt is read again at the start of every match (and on Reload).
//
// Per replaced slot the core builds two display lists (frame parity 0 and 1) and hangs them on the first part node
// that draws anything; every other part of the slot draws nothing. A list sets the adapter's render state, then per
// pack entry forces that part's matrix and calls the pack's list (or, for a welded pack, points segment 0xD at the
// slot's part matrices around the one model list).
#include "adapter.h"
#include "recomp_api.h"

RECOMP_IMPORT("*", void aki_log(const char* str, u32 length));
RECOMP_IMPORT("*", s32 aki_models_read(const char* path, void* dest, u32 max));

#define MI_PACKS 8
#define MI_PACK_ENTRIES 64
#define MI_RULES 32
#define MI_ID_LEN 32
#define MI_LIST_BYTES 0x800                        // per slot and parity: render state + 3 commands per entry
#define MI_TEXT (MI_SCRATCH + 2 * MI_MAX_SLOTS * MI_LIST_BYTES)
#define MI_TEXT_MAX 0x4000

// AKP1 header (big-endian, at offset 0 of model.bin; all offsets from the start of the file).
#define AKP1_MAGIC 0x414B5031u // 'AKP1'
#define AKP1_VERSION 2      // version 2: the skin table (header bytes 56..63)
#define MI_RUNTIME 2        // the highest required_runtime this core reads (2: smooth skinning)
#define AKP1_SIZE 64
#define AKP1_WELDED 0xFFFFFFFFu // entry part: the one model list of a welded pack (matrices through segment 0xD)
#define AKP1_FLAG_WELDED 1u
#define AKP1_FLAG_CULL 4u   // the converter made the winding consistent (aki-model --winding normals): cull back faces
#define AKP1_FLAG_SKIN 8u   // smooth skinning: blended vertices load from segment 0xE (aki-model --skin)
#define AKP1_SKIN_ENTRY 48  // Vtx template (16) + 4 x (u8 part, u8 weight, s16 x, y, z)
#define AKP1_SKIN_MAX 4096  // skinned vertices per pack

// --- log lines ---------------------------------------------------------------------------------------------------
static char line[256];
static int line_len;

void mi_log_str(const char* s) {
    while (*s && line_len < (int)sizeof(line) - 1) line[line_len++] = *s++;
}

void mi_log_hex(u32 v, int digits) {
    for (int i = digits - 1; i >= 0; i--)
        if (line_len < (int)sizeof(line) - 1) line[line_len++] = "0123456789ABCDEF"[(v >> (i * 4)) & 0xF];
}

void mi_log_dec(s32 v) {
    char tmp[12];
    int n = 0;
    u32 u = (v < 0) ? (u32)(-v) : (u32)v;
    do {
        tmp[n++] = (char)('0' + (u % 10));
        u /= 10;
    } while (u && n < 11);
    if (v < 0) mi_log_str("-");
    while (n > 0 && line_len < (int)sizeof(line) - 1) line[line_len++] = tmp[--n];
}

void mi_log_end(void) {
    aki_log(line, (u32)line_len);
    line_len = 0;
}

// --- small string helpers (no libc) ------------------------------------------------------------------------------
static int str_eq(const char* a, const char* b) {
    while (*a && *a == *b) a++, b++;
    return *a == *b;
}

static void str_copy(char* dst, const char* src, int max) {
    int i = 0;
    for (; src[i] && i < max - 1; i++) dst[i] = src[i];
    dst[i] = 0;
}

static int valid_id(const char* id) {
    int n = 0;
    for (; id[n]; n++) {
        char c = id[n];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) return 0;
    }
    return n > 0 && n < MI_ID_LEN;
}

// --- packs -------------------------------------------------------------------------------------------------------
typedef struct {
    char id[MI_ID_LEN];
    int state;           // 1 loaded, -1 failed (not retried until the next match)
    u32 base, size, flags;
    u32 skin, skin_count;  // the skin table (virtual) and its vertices, or 0
    int count;
    u32 part[MI_PACK_ENTRIES], dl[MI_PACK_ENTRIES];
} MiPack;

static MiPack packs[MI_PACKS];
static int pack_count;
static u32 pack_top = MI_MEM_BASE; // the next free byte of the window packs load into

static void reset_slot_buffers(void);

static void reset_packs(void) {
    pack_count = 0;
    pack_top = MI_MEM_BASE;
    reset_slot_buffers();
}

static int pack_fail(MiPack* p, const char* why) {
    mi_log_str("model pack "); mi_log_str(p->id); mi_log_str(": "); mi_log_str(why); mi_log_end();
    p->state = -1;
    return -1;
}

// Reads and checks <id>.akimodel/model.bin at the top of the window, moves its address words there.
static int load_pack(MiPack* p) {
    char path[MI_ID_LEN + 24];
    int n = 0;
    for (const char* s = p->id; *s; s++) path[n++] = *s;
    for (const char* s = ".akimodel/model.bin"; *s; s++) path[n++] = *s;
    path[n] = 0;
    u32 base = (pack_top + 15) & ~15u, room = MI_MEM_BASE + MI_MEM_SIZE - base;
    s32 size = aki_models_read(path, (void*)base, room);
    if (size == -1) return pack_fail(p, "no model.bin (is the folder in mods/models?)");
    if (size == -2) return pack_fail(p, "too big for the memory left (other packs are loaded)");
    if (size < AKP1_SIZE) return pack_fail(p, "not readable");
    u32 end = base + (u32)size;
    if (RAM32(base) != AKP1_MAGIC) return pack_fail(p, "no AKP1 header (convert it again with a current aki-model)");
    if ((u32)RAM16(base + 4) > AKP1_VERSION || (u16)RAM16(base + 6) < AKP1_SIZE) return pack_fail(p, "made for a newer runtime");
    if (RAM32(base + 52) > MI_RUNTIME) return pack_fail(p, "needs a newer aki_modelimport");
    for (int i = 0; i < 8; i++)
        if (RAM8(base + 8 + i) != (u8)mi_profile_id[i]) return pack_fail(p, "built for another game");
    if (RAM32(base + 20) != (u32)size) return pack_fail(p, "file size doesn't match its header");
    u32 built = RAM32(base + 16), flags = RAM32(base + 24);
    u32 eoff = RAM32(base + 28), ecount = RAM32(base + 32), roff = RAM32(base + 36), rcount = RAM32(base + 40);
    // Every check as "x > size - y" (size >= AKP1_SIZE here), so no sum can wrap around.
    if (ecount == 0 || ecount > MI_PACK_ENTRIES || (eoff & 3) || eoff < AKP1_SIZE || eoff > (u32)size ||
        ecount * 8 > (u32)size - eoff)
        return pack_fail(p, "bad entry table");
    if ((roff & 3) || rcount > (u32)size / 4 ||
        (rcount && (roff < AKP1_SIZE || roff > (u32)size || rcount * 4 > (u32)size - roff)))
        return pack_fail(p, "bad relocation table");
    // All offsets are checked before any is applied, and none may point into the table itself (a relocation there
    // could change a later, already checked entry).
    u32 delta = base - built, rend = roff + rcount * 4;
    for (u32 r = 0; r < rcount; r++) {
        u32 off = RAM32(base + roff + r * 4);
        if ((off & 3) || off < AKP1_SIZE || off > (u32)size - 4 || (off >= roff && off < rend))
            return pack_fail(p, "relocation outside the file");
    }
    u32 soff = 0, scount = 0;
    if ((u16)RAM16(base + 4) >= 2 && (flags & AKP1_FLAG_SKIN)) {
        soff = RAM32(base + 56);
        scount = RAM32(base + 60);
        if (scount == 0 || scount > AKP1_SKIN_MAX || (soff & 3) || soff < AKP1_SIZE || soff > (u32)size ||
            scount * AKP1_SKIN_ENTRY > (u32)size - soff)
            return pack_fail(p, "bad skin table");
        for (u32 v = 0; v < scount; v++)
            for (int k = 0; k < 4; k++)
                if (RAM8(base + soff + v * AKP1_SKIN_ENTRY + 16 + k * 8) >= (u32)mi_parts())
                    return pack_fail(p, "skin table names a part this game hasn't");
    }
    for (u32 r = 0; r < rcount; r++) RAM32(base + RAM32(base + roff + r * 4)) += delta;
    p->count = 0;
    for (u32 i = 0; i < ecount; i++) {
        u32 part = RAM32(base + eoff + i * 8), dl = RAM32(base + eoff + i * 8 + 4);
        if ((part >= (u32)mi_parts() && part != AKP1_WELDED) || dl < AKP1_SIZE || dl > (u32)size - 8 || (dl & 7)) continue;
        if (part == AKP1_WELDED && !(flags & AKP1_FLAG_WELDED)) continue; // the layout check keys on the flag
        p->part[p->count] = part;
        p->dl[p->count] = base + dl;
        p->count++;
    }
    if (!p->count) return pack_fail(p, "no usable lists");
    p->base = base;
    p->size = (u32)size;
    p->flags = flags;
    p->skin = scount ? base + soff : 0;
    p->skin_count = scount;
    p->state = 1;
    pack_top = end;
    mi_log_str("model pack "); mi_log_str(p->id); mi_log_str(": "); mi_log_dec(size); mi_log_str(" bytes at ");
    mi_log_hex(base, 8); mi_log_str(", "); mi_log_dec(p->count); mi_log_str((flags & AKP1_FLAG_WELDED) ? " list (welded)" : " part lists");
    if (scount) { mi_log_str(", "); mi_log_dec((s32)scount); mi_log_str(" skinned vertices"); }
    mi_log_end();
    return 0;
}

// The pack with this id, loaded on first use; -1 if it can't be.
static int find_pack(const char* id) {
    for (int i = 0; i < pack_count; i++)
        if (str_eq(packs[i].id, id)) return packs[i].state == 1 ? i : -1;
    if (pack_count >= MI_PACKS) {
        mi_log_str("model pack "); mi_log_str(id); mi_log_str(": not loaded, 8 packs per match at most"); mi_log_end();
        return -1;
    }
    MiPack* p = &packs[pack_count++];
    str_copy(p->id, id, MI_ID_LEN);
    return load_pack(p) == 0 ? pack_count - 1 : -1;
}

// --- models.txt and the Mods menu --------------------------------------------------------------------------------
enum { RULE_SLOT = 1, RULE_WRESTLER, RULE_OFF };
typedef struct {
    char pack[MI_ID_LEN];
    int kind;
    s32 a, b; // slot (0-based), or wrestler + costume (-1 = any)
} MiRule;

static MiRule rules[MI_RULES];
static int rule_count;
static char override_pack[MI_MAX_SLOTS][MI_ID_LEN];

static int parse_int(const char* s, s32* out) {
    int neg = 0;
    u32 v = 0;
    if (*s == '-') neg = 1, s++;
    if (!*s) return 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        if (!*s) return 0;
        for (; *s; s++) {
            char c = *s;
            u32 d = (c >= '0' && c <= '9') ? (u32)(c - '0') : (c >= 'a' && c <= 'f') ? (u32)(c - 'a' + 10)
                  : (c >= 'A' && c <= 'F') ? (u32)(c - 'A' + 10) : 99;
            if (d > 15) return 0;
            v = v * 16 + d;
        }
    } else {
        for (; *s; s++) {
            if (*s < '0' || *s > '9') return 0;
            v = v * 10 + (u32)(*s - '0');
        }
    }
    *out = neg ? -(s32)v : (s32)v;
    return 1;
}

static void rule_error(int line_no, const char* why) {
    mi_log_str("models.txt line "); mi_log_dec(line_no); mi_log_str(": "); mi_log_str(why); mi_log_end();
}

// One line, split into at most 6 words (the buffer is modified in place).
static void parse_line(char* s, int line_no) {
    char* w[6];
    int n = 0;
    while (*s && n < 6) {
        while (*s == ' ' || *s == '\t') s++;
        if (!*s || *s == '#') break;
        w[n++] = s;
        while (*s && *s != ' ' && *s != '\t' && *s != '#') s++;
        if (*s == '#') { *s = 0; break; }
        if (*s) *s++ = 0;
    }
    if (n == 0) return;
    if (rule_count >= MI_RULES) { rule_error(line_no, "too many lines (32 at most), ignored"); return; }
    MiRule* r = &rules[rule_count];
    if (str_eq(w[0], "off")) {
        if (n != 2 || !valid_id(w[1])) { rule_error(line_no, "expected: off <pack>"); return; }
        str_copy(r->pack, w[1], MI_ID_LEN);
        r->kind = RULE_OFF;
        rule_count++;
        return;
    }
    if (!valid_id(w[0])) { rule_error(line_no, "pack ids are a-z, 0-9, _ and -, at most 31"); return; }
    str_copy(r->pack, w[0], MI_ID_LEN);
    s32 v;
    if (n == 3 && str_eq(w[1], "slot") && parse_int(w[2], &v) && v >= 1 && v <= mi_slots()) {
        r->kind = RULE_SLOT;
        r->a = v - 1;
        rule_count++;
    } else if ((n == 3 || n == 5) && str_eq(w[1], "wrestler") && parse_int(w[2], &v)) {
        r->kind = RULE_WRESTLER;
        r->a = v;
        r->b = -1;
        if (n == 5 && !(str_eq(w[3], "costume") && parse_int(w[4], &r->b) && r->b >= 0)) {
            rule_error(line_no, "expected: <pack> wrestler <id> [costume <n>]");
            return;
        }
        rule_count++;
    } else {
        rule_error(line_no, "expected: <pack> slot <1-4> | <pack> wrestler <id> [costume <n>] | off <pack>");
    }
}

static void read_overrides(void) {
    static const char* keys[MI_MAX_SLOTS] = { "slot1_pack", "slot2_pack", "slot3_pack", "slot4_pack" };
    for (int i = 0; i < MI_MAX_SLOTS; i++) {
        char* v = recomp_get_config_string(keys[i]);
        str_copy(override_pack[i], v ? v : "", MI_ID_LEN);
        if (v) recomp_free_config_string(v);
        if (override_pack[i][0] && !valid_id(override_pack[i])) {
            mi_log_str("Slot "); mi_log_dec(i + 1); mi_log_str(" pack: not a pack id: "); mi_log_str(override_pack[i]);
            mi_log_end();
            override_pack[i][0] = 0;
        }
    }
}

static void read_rules(void) {
    rule_count = 0;
    read_overrides();
    s32 size = aki_models_read("models.txt", (void*)MI_TEXT, MI_TEXT_MAX - 1);
    if (size < 0) {
        mi_log_str(size == -2 ? "models.txt: too big (16 KB at most)" : "models.txt: none (only the Mods menu's slot packs apply)");
        mi_log_end();
        return;
    }
    char* text = (char*)MI_TEXT;
    text[size] = 0;
    int line_no = 1;
    s32 first = (size >= 3 && (u8)text[0] == 0xEF && (u8)text[1] == 0xBB && (u8)text[2] == 0xBF) ? 3 : 0; // UTF-8 BOM
    char* start = text + first;
    for (s32 i = first; i <= size; i++) {
        if (text[i] == '\n' || text[i] == '\r' || text[i] == 0) {
            char end = text[i];
            char next = (i < size) ? text[i + 1] : 0;
            text[i] = 0;
            parse_line(start, line_no);
            if (end == '\n' || (end == '\r' && next != '\n')) line_no++; // \n, \r\n and \r-only files
            start = &text[i + 1];
        }
    }
    mi_log_str("models.txt: "); mi_log_dec(rule_count); mi_log_str(" lines"); mi_log_end();
}

static int pack_is_off(const char* id) {
    for (int i = 0; i < rule_count; i++)
        if (rules[i].kind == RULE_OFF && str_eq(rules[i].pack, id)) return 1;
    return 0;
}

// The pack id that replaces this slot now, or 0.
static const char* pack_for_slot(int slot) {
    const char* id = 0;
    if (override_pack[slot][0]) id = override_pack[slot];
    for (int i = 0; !id && i < rule_count; i++)
        if (rules[i].kind == RULE_SLOT && rules[i].a == slot) id = rules[i].pack;
    s32 w = mi_slot_wrestler(slot), c = mi_slot_costume(slot);
    for (int i = 0; !id && i < rule_count; i++)
        if (rules[i].kind == RULE_WRESTLER && rules[i].a == w && (rules[i].b < 0 || rules[i].b == c)) id = rules[i].pack;
    return (id && !pack_is_off(id)) ? id : 0;
}

// --- slots -------------------------------------------------------------------------------------------------------
typedef struct {
    int state;           // 0 stock, 1 replaced, -1 can't be replaced until the slot changes
    u32 signature;       // the slot's nodes and wrestler when it was decided
    int pack;
    u32 host;
    u32 dl[2];
    u32 skin_buf[2];     // the blended vertices for each frame parity (segment 0xE), or 0
    u32 skin_cap;        // vertices they hold (allocated once per match, reused when the slot changes)
    u32 skin_frame[2];   // the frame each was last computed for
    MiSavedPart saved[MI_MAX_PARTS];
} MiSlot;

static MiSlot slots[MI_MAX_SLOTS];
static int in_match;
static u32 reload_seen;
static u32 frame_no;          // counted by mi_core_frame
static s32 reload_frame = -1; // a Reload's frame while the slots sit out the frames still in flight

static void reset_slot_buffers(void) {
    for (int i = 0; i < MI_MAX_SLOTS; i++) {
        slots[i].skin_buf[0] = slots[i].skin_buf[1] = 0;
        slots[i].skin_cap = 0;
    }
}

// --- smooth skinning ------------------------------------------------------------------------------------------------
// A blended vertex: clip = sum of w_i * [p_i 1] * M_i over its influences (M_i: part i's node matrix for this parity,
// p_i: its rest position as placed for part i), then local = clip * M_dom^-1, loaded under the dominant part's matrix
// (the list forces M_dom before the run). The parts share the projection, so M_i * M_dom^-1 is affine (checked on
// No Mercy: aki-roadmap#130). Row vectors, N64 Mtx layout: 16 s16 integer parts, then 16 u16 fractions.
typedef struct { float m[4][4]; } MiMat;

static void read_mtx(u32 addr, MiMat* out) {
    for (int e = 0; e < 16; e++) {
        s32 v = ((s32)RAM16(addr + e * 2) << 16) | (u16)RAM16(addr + 32 + e * 2);
        out->m[e >> 2][e & 3] = (float)v * (1.0f / 65536.0f);
    }
}

// 4x4 inverse (cofactors); 0 when singular.
static int invert(const MiMat* a, MiMat* out) {
    const float* m = &a->m[0][0];
    float inv[16];
    inv[0] = m[5]*m[10]*m[15] - m[5]*m[11]*m[14] - m[9]*m[6]*m[15] + m[9]*m[7]*m[14] + m[13]*m[6]*m[11] - m[13]*m[7]*m[10];
    inv[4] = -m[4]*m[10]*m[15] + m[4]*m[11]*m[14] + m[8]*m[6]*m[15] - m[8]*m[7]*m[14] - m[12]*m[6]*m[11] + m[12]*m[7]*m[10];
    inv[8] = m[4]*m[9]*m[15] - m[4]*m[11]*m[13] - m[8]*m[5]*m[15] + m[8]*m[7]*m[13] + m[12]*m[5]*m[11] - m[12]*m[7]*m[9];
    inv[12] = -m[4]*m[9]*m[14] + m[4]*m[10]*m[13] + m[8]*m[5]*m[14] - m[8]*m[6]*m[13] - m[12]*m[5]*m[10] + m[12]*m[6]*m[9];
    inv[1] = -m[1]*m[10]*m[15] + m[1]*m[11]*m[14] + m[9]*m[2]*m[15] - m[9]*m[3]*m[14] - m[13]*m[2]*m[11] + m[13]*m[3]*m[10];
    inv[5] = m[0]*m[10]*m[15] - m[0]*m[11]*m[14] - m[8]*m[2]*m[15] + m[8]*m[3]*m[14] + m[12]*m[2]*m[11] - m[12]*m[3]*m[10];
    inv[9] = -m[0]*m[9]*m[15] + m[0]*m[11]*m[13] + m[8]*m[1]*m[15] - m[8]*m[3]*m[13] - m[12]*m[1]*m[11] + m[12]*m[3]*m[9];
    inv[13] = m[0]*m[9]*m[14] - m[0]*m[10]*m[13] - m[8]*m[1]*m[14] + m[8]*m[2]*m[13] + m[12]*m[1]*m[10] - m[12]*m[2]*m[9];
    inv[2] = m[1]*m[6]*m[15] - m[1]*m[7]*m[14] - m[5]*m[2]*m[15] + m[5]*m[3]*m[14] + m[13]*m[2]*m[7] - m[13]*m[3]*m[6];
    inv[6] = -m[0]*m[6]*m[15] + m[0]*m[7]*m[14] + m[4]*m[2]*m[15] - m[4]*m[3]*m[14] - m[12]*m[2]*m[7] + m[12]*m[3]*m[6];
    inv[10] = m[0]*m[5]*m[15] - m[0]*m[7]*m[13] - m[4]*m[1]*m[15] + m[4]*m[3]*m[13] + m[12]*m[1]*m[7] - m[12]*m[3]*m[5];
    inv[14] = -m[0]*m[5]*m[14] + m[0]*m[6]*m[13] + m[4]*m[1]*m[14] - m[4]*m[2]*m[13] - m[12]*m[1]*m[6] + m[12]*m[2]*m[5];
    inv[3] = -m[1]*m[6]*m[11] + m[1]*m[7]*m[10] + m[5]*m[2]*m[11] - m[5]*m[3]*m[10] - m[9]*m[2]*m[7] + m[9]*m[3]*m[6];
    inv[7] = m[0]*m[6]*m[11] - m[0]*m[7]*m[10] - m[4]*m[2]*m[11] + m[4]*m[3]*m[10] + m[8]*m[2]*m[7] - m[8]*m[3]*m[6];
    inv[11] = -m[0]*m[5]*m[11] + m[0]*m[7]*m[9] + m[4]*m[1]*m[11] - m[4]*m[3]*m[9] - m[8]*m[1]*m[7] + m[8]*m[3]*m[5];
    inv[15] = m[0]*m[5]*m[10] - m[0]*m[6]*m[9] - m[4]*m[1]*m[10] + m[4]*m[2]*m[9] + m[8]*m[1]*m[6] - m[8]*m[2]*m[5];
    float det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
    if (det > -1e-12f && det < 1e-12f) return 0;
    det = 1.0f / det;
    for (int i = 0; i < 16; i++) (&out->m[0][0])[i] = inv[i] * det;
    return 1;
}

static s16 clamp16(float f) {
    if (f > 32767.0f) return 32767;
    if (f < -32768.0f) return -32768;
    return (s16)(f < 0.0f ? f - 0.5f : f + 0.5f);
}

static MiMat skin_m[MI_MAX_PARTS], skin_inv[MI_MAX_PARTS];
static u8 skin_have[MI_MAX_PARTS];   // 1: skin_m read, 2: skin_inv too, 3: singular

// Recomputes the slot's blended vertices for this frame parity into its parity buffer.
static void skin_slot(int slot, MiSlot* s, const MiPack* p, int parity) {
    for (int i = 0; i < MI_MAX_PARTS; i++) skin_have[i] = 0;
    u32 out = s->skin_buf[parity];
    for (u32 v = 0; v < p->skin_count; v++, out += 16) {
        u32 e = p->skin + v * AKP1_SKIN_ENTRY;
        float c[4] = { 0, 0, 0, 0 };
        int dom = RAM8(e + 16);
        for (int k = 0; k < 4; k++) {
            u32 inf = e + 16 + k * 8;
            u32 w8 = RAM8(inf + 1);
            if (!w8) continue;
            int part = RAM8(inf);
            if (!skin_have[part]) {
                u32 node = mi_part_node(slot, part);
                if (!node) continue;
                read_mtx(mi_part_matrix(node, parity), &skin_m[part]);
                skin_have[part] = 1;
            }
            float x = RAM16(inf + 2), y = RAM16(inf + 4), z = RAM16(inf + 6), w = (float)w8 * (1.0f / 255.0f);
            const MiMat* m = &skin_m[part];
            for (int col = 0; col < 4; col++)
                c[col] += w * (x * m->m[0][col] + y * m->m[1][col] + z * m->m[2][col] + m->m[3][col]);
        }
        if (skin_have[dom] == 1) skin_have[dom] = invert(&skin_m[dom], &skin_inv[dom]) ? 2 : 3;
        if (skin_have[dom] != 2) continue;   // keep last frame's position
        const MiMat* inv = &skin_inv[dom];
        float l[4];
        for (int col = 0; col < 4; col++)
            l[col] = c[0] * inv->m[0][col] + c[1] * inv->m[1][col] + c[2] * inv->m[2][col] + c[3] * inv->m[3][col];
        float iw = (l[3] > 1e-6f || l[3] < -1e-6f) ? 1.0f / l[3] : 1.0f;
        RAM16(out + 0) = clamp16(l[0] * iw);
        RAM16(out + 2) = clamp16(l[1] * iw);
        RAM16(out + 4) = clamp16(l[2] * iw);
    }
    s->skin_frame[parity] = frame_no;
}

// The slot's two blended-vertex copies, from the window after the packs (once per match; reused when it changes).
static int alloc_skin(MiSlot* s, const MiPack* p) {
    if (!p->skin_count) return 1;
    if (s->skin_cap < p->skin_count) {
        u32 bytes = p->skin_count * 16, base = (pack_top + 15) & ~15u;
        if (base + 2 * bytes > MI_MEM_BASE + MI_MEM_SIZE) return 0;
        s->skin_buf[0] = base;
        s->skin_buf[1] = base + bytes;
        s->skin_cap = p->skin_count;
        pack_top = base + 2 * bytes;
    }
    // Both copies start as the templates (st, colours); positions are filled per frame.
    for (int b = 0; b < 2; b++)
        for (u32 v = 0; v < p->skin_count; v++)
            for (int w = 0; w < 4; w++)
                RAM32(s->skin_buf[b] + v * 16 + w * 4) = RAM32(p->skin + v * AKP1_SKIN_ENTRY + w * 4);
    s->skin_frame[0] = s->skin_frame[1] = 0xFFFFFFFFu;
    return 1;
}

static u32 slot_signature(int slot) {
    u32 sig = 0x811C9DC5u;
    for (int i = 0; i < mi_parts(); i++) sig = (sig ^ mi_part_node(slot, i)) * 0x01000193u;
    return (sig ^ (u32)mi_slot_wrestler(slot)) * 0x01000193u;
}

static void put(u32** out, u32 w0, u32 w1) {
    (*out)[0] = w0;
    (*out)[1] = w1;
    *out += 2;
}

static void build_list(int slot, const MiSlot* s, const MiPack* p, u32 dl, int parity) {
    u32* out = mi_list_open((u32*)dl);
    // The game draws its wrestlers without depth, back faces culled. A pack with consistent winding gets the same; one
    // without it draws both sides (a rip's mixed winding would otherwise lose half its faces).
    if (p->flags & AKP1_FLAG_CULL) put(&out, 0xD9FFFFFFu, 0x00000400u); // G_CULL_BACK on
    for (int e = 0; e < p->count; e++) {
        if (p->part[e] == AKP1_WELDED) {
            put(&out, 0xDB060034u, mi_matrix_segment(slot, parity) - 0x80000000u); // gSPSegment(0xD, part matrices)
            // Segment 0xE only for a skinned pack (the slot keeps its buffers when it changes to another pack).
            const int skinned = p->skin_count && s->skin_buf[parity];
            if (skinned) put(&out, 0xDB060038u, s->skin_buf[parity] - 0x80000000u); // segment 0xE
            put(&out, 0xDE000000u, p->dl[e] - 0x80000000u);
            put(&out, 0xDB060034u, 0);
            if (skinned) put(&out, 0xDB060038u, 0);
            continue;
        }
        u32 node = mi_part_node(slot, (int)p->part[e]);
        if (!node) continue;
        put(&out, 0xDC38000Eu, mi_part_matrix(node, parity) - 0x80000000u); // gSPForceMatrix: that part's matrix
        put(&out, 0xDB0C0000u, 0x00010000u);
        put(&out, 0xDE000000u, p->dl[e] - 0x80000000u);
    }
    out = mi_list_close(out);
    put(&out, 0xDF000000u, 0);
}

static void restore_slot(MiSlot* s) {
    if (s->state == 1)
        for (int i = 0; i < mi_parts(); i++)
            if (s->saved[i].node) mi_restore_part(&s->saved[i], s->dl[0], s->dl[1]);
    for (int i = 0; i < MI_MAX_PARTS; i++) s->saved[i].node = 0;
    s->state = 0;
}

static void decide_slot(int slot, MiSlot* s) {
    restore_slot(s);
    s->signature = slot_signature(slot);
    s->state = -1;
    const char* id = pack_for_slot(slot);
    if (!id) return;
    int pi = find_pack(id);
    if (pi < 0) return;
    const MiPack* p = &packs[pi];
    if ((p->flags & AKP1_FLAG_WELDED) && !mi_matrices_contiguous(slot)) {
        mi_log_str("slot "); mi_log_dec(slot + 1); mi_log_str(": its part matrices aren't where welded pack ");
        mi_log_str(p->id); mi_log_str(" needs them; left stock"); mi_log_end();
        return;
    }
    s->host = 0;
    for (int i = 0; i < mi_parts(); i++) {
        u32 node = mi_part_node(slot, i);
        if (!node) continue;
        mi_save_part(node, &s->saved[i]);
        if (!s->host && mi_part_draws(&s->saved[i])) s->host = node;
    }
    if (!s->host) return;
    if (!alloc_skin(s, p)) {
        mi_log_str("slot "); mi_log_dec(slot + 1); mi_log_str(": no memory left for pack "); mi_log_str(p->id);
        mi_log_str("'s skinned vertices; left stock"); mi_log_end();
        return;
    }
    s->pack = pi;
    s->dl[0] = MI_SCRATCH + (u32)(slot * 2) * MI_LIST_BYTES;
    s->dl[1] = s->dl[0] + MI_LIST_BYTES;
    build_list(slot, s, p, s->dl[0], 0);
    build_list(slot, s, p, s->dl[1], 1);
    s->state = 1;
    mi_log_str("slot "); mi_log_dec(slot + 1); mi_log_str(" (wrestler "); mi_log_hex((u32)mi_slot_wrestler(slot), 4);
    mi_log_str("): "); mi_log_str(p->id); mi_log_end();
}

static void restore_all(void) {
    for (int i = 0; i < MI_MAX_SLOTS; i++) {
        restore_slot(&slots[i]);
        slots[i].signature = 0;
    }
}

void mi_core_emit(int parity) {
    int n = mi_slots();
    if (!recomp_get_config_u32("enabled")) {
        if (in_match) restore_all(), reset_packs();
        in_match = 0;
        return;
    }
    int any = 0;
    for (int i = 0; i < n; i++) any |= mi_slot_active(i);
    if (!any) {
        // Between matches: everything back to stock; packs and models.txt are read again for the next match.
        if (in_match) restore_all(), reset_packs();
        in_match = 0;
        return;
    }
    u32 reload = recomp_get_config_u32("reload");
    if (in_match && reload != reload_seen) {
        // The frames already submitted may still draw the old lists over the old pack memory: stock for two frames.
        restore_all();
        reload_seen = reload;
        reload_frame = (s32)frame_no;
    }
    if (reload_frame >= 0) {
        if (frame_no - (u32)reload_frame < 2) return;
        reload_frame = -1;
        reset_packs();
        read_rules();
    }
    if (!in_match) {
        restore_all();
        reset_packs();
        read_rules();
        in_match = 1;
        reload_seen = reload;
    }
    for (int i = 0; i < n; i++) {
        MiSlot* s = &slots[i];
        if (!mi_slot_active(i)) {
            if (s->state) restore_slot(s);
            s->signature = 0;
            continue;
        }
        if (s->signature != slot_signature(i)) decide_slot(i, s);
        if (s->state != 1) continue;
        // Smooth skinning: this parity's blended vertices, once per frame (the emitter runs per list).
        const MiPack* p = &packs[s->pack];
        if (p->skin_count && s->skin_frame[parity] != frame_no) skin_slot(i, s, p, parity);
        // The game can rewrite a node's fields between emitter calls: set them every time.
        for (int k = 0; k < mi_parts(); k++) {
            u32 node = s->saved[k].node;
            if (!node) continue;
            if (node == s->host) mi_hang_list(node, s->dl[parity]);
            else mi_hide_part(node);
        }
    }
}

// The Log option: each slot's wrestler id (and costume, where the adapter knows it) whenever it changes, so a player
// can write models.txt "wrestler" lines.
static s32 logged_wrestler[MI_MAX_SLOTS];

// The Log poses option: a replaced slot's part matrices (both parities, every 20 frames, for 1200 frames), for the
// offline skinning replay (tools/skinsim.py): "pose <frame> <slot> <part> M0|M1 <16 words>".
static void log_poses(void) {
    if (frame_no < 300 || frame_no > 1500 || frame_no % 20 != 0) return;
    for (int slot = 0; slot < mi_slots(); slot++) {
        if (slots[slot].state != 1) continue;
        for (int i = 0; i < mi_parts(); i++) {
            u32 node = mi_part_node(slot, i);
            if (!node) continue;
            for (int k = 0; k < 2; k++) {
                mi_log_str("pose "); mi_log_dec((s32)frame_no); mi_log_str(" "); mi_log_dec(slot + 1); mi_log_str(" ");
                mi_log_dec(i); mi_log_str(k ? " M1" : " M0");
                u32 base = mi_part_matrix(node, k);
                for (int w = 0; w < 16; w++) { mi_log_str(" "); mi_log_hex(RAM32(base + w * 4), 8); }
                mi_log_end();
            }
        }
    }
}

void mi_core_frame(void) {
    frame_no++;
    if (recomp_get_config_u32("log_poses")) log_poses();
    if (!recomp_get_config_u32("log")) return;
    for (int i = 0; i < mi_slots(); i++) {
        s32 w = mi_slot_active(i) ? mi_slot_wrestler(i) : 0;
        if (w == logged_wrestler[i]) continue;
        logged_wrestler[i] = w;
        if (!w) continue;
        mi_log_str("slot "); mi_log_dec(i + 1); mi_log_str(": wrestler "); mi_log_hex((u32)w, 4);
        s32 c = mi_slot_costume(i);
        if (c >= 0) { mi_log_str(" costume "); mi_log_dec(c); }
        mi_log_end();
    }
}
