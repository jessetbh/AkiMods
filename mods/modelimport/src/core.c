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
#define AKP1_VERSION 1
#define AKP1_SIZE 64
#define AKP1_WELDED 0xFFFFFFFFu // entry part: the one model list of a welded pack (matrices through segment 0xD)
#define AKP1_FLAG_WELDED 1u

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
    int count;
    u32 part[MI_PACK_ENTRIES], dl[MI_PACK_ENTRIES];
} MiPack;

static MiPack packs[MI_PACKS];
static int pack_count;
static u32 pack_top = MI_MEM_BASE; // the next free byte of the window packs load into

static void reset_packs(void) {
    pack_count = 0;
    pack_top = MI_MEM_BASE;
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
    if (RAM32(base + 52) > AKP1_VERSION) return pack_fail(p, "needs a newer aki_modelimport");
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
    p->state = 1;
    pack_top = end;
    mi_log_str("model pack "); mi_log_str(p->id); mi_log_str(": "); mi_log_dec(size); mi_log_str(" bytes at ");
    mi_log_hex(base, 8); mi_log_str(", "); mi_log_dec(p->count); mi_log_str((flags & AKP1_FLAG_WELDED) ? " list (welded)" : " part lists");
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
    MiSavedPart saved[MI_MAX_PARTS];
} MiSlot;

static MiSlot slots[MI_MAX_SLOTS];
static int in_match;
static u32 reload_seen;
static u32 frame_no;          // counted by mi_core_frame
static s32 reload_frame = -1; // a Reload's frame while the slots sit out the frames still in flight

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

static void build_list(int slot, const MiPack* p, u32 dl, int parity) {
    u32* out = mi_list_open((u32*)dl);
    for (int e = 0; e < p->count; e++) {
        if (p->part[e] == AKP1_WELDED) {
            put(&out, 0xDB060034u, mi_matrix_segment(slot, parity) - 0x80000000u); // gSPSegment(0xD, part matrices)
            put(&out, 0xDE000000u, p->dl[e] - 0x80000000u);
            put(&out, 0xDB060034u, 0);
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
    s->pack = pi;
    s->dl[0] = MI_SCRATCH + (u32)(slot * 2) * MI_LIST_BYTES;
    s->dl[1] = s->dl[0] + MI_LIST_BYTES;
    build_list(slot, p, s->dl[0], 0);
    build_list(slot, p, s->dl[1], 1);
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

void mi_core_frame(void) {
    frame_no++;
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
