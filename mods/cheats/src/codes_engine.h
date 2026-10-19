// GameShark (N64 GameShark / Action Replay / GameShark Pro) code engine for the AKI cheats mod.
// Pure C with no runtime imports, so the host-side tests compile it unchanged (tests/).
//
// Two parsers fill one code list:
//   parse_codes()      the Codes box and presets: hex tokens, any separator ("8109940A FFFF 50000B02 0000 ...").
//   parse_code_file()  a conversion's codes file: one code per line, with comments, names and a "# rom:" tag.
//                      Grammar: mods/cheats/README.md, "Codes files".
// apply_codes() runs the list once; the mod calls it every frame.
//
// Code types:
//   80 / A0  XXXXXX 00YY   8-bit write
//   81 / A1  XXXXXX YYYY   16-bit write
//   D0 / D1  XXXXXX YYYY   run the next code only if the 8/16-bit value equals YY/YYYY
//   D2 / D3  XXXXXX YYYY   run the next code only if the 8/16-bit value differs
//   50       00NNSS IIII   repeat the next write NN times, address += SS, value += IIII
// Ignored: enabler/master codes (F0, F1, F6, FF, EE, DE, 2A, 3C; they patch the cartridge boot, which
// the recomp doesn't need), button activators (88/89: there is no GameShark button), anything else.
//
// "The next code" is a unit: a 50 repeater together with its write, or a conditional together with
// the unit it governs. So stacked conditionals (D0 ..., D0 ..., 81 ...) all have to hold.
//
// Memory: N64 virtual addresses (0x80xxxxxx; A0/A1 map to the same RAM), refused outside the 8 MB of
// RDRAM. Reads/writes go through plain pointers, which the mod recompiler turns into the runtime's
// byte-swapped RDRAM accesses (the same convention as every AKI mod). Tests redefine the macros.

#ifndef CODES_ENGINE_H
#define CODES_ENGINE_H

#ifndef MAX_CODES
#define MAX_CODES 4096
#endif

#define RDRAM_START 0x80000000u
#define RDRAM_END   0x80800000u // 8 MB (Expansion Pak)

#ifndef CODES_READ8
#define CODES_READ8(a)      (*(volatile u8*)(a))
#define CODES_READ16(a)     (*(volatile u16*)(a))
#define CODES_WRITE8(a, v)  (*(volatile u8*)(a) = (u8)(v))
#define CODES_WRITE16(a, v) (*(volatile u16*)(a) = (u16)(v))
#endif

typedef struct {
    u32 code;  // type in the top byte, address in the low 24 bits
    u16 value;
} Code;

// What a parse did, for the log line and the tests.
typedef struct {
    int added;          // codes kept (including 50 and conditional lines)
    int enablers;       // F0/F1/... lines ignored
    int buttons;        // 88/89 lines ignored
    int unknown;        // other code types ignored
    int malformed;      // lines that start like a code but aren't one (files only)
    int dropped;        // Rev 0 codes that couldn't be translated, with what they govern
    int truncated;      // codes lost because the list was full
    int rom_mismatch;   // the file's "# rom:" tag names another game or an unsupported revision
    int rom_rev;        // revision from the tag: -1 none, else the number
    int first_bad_line; // first malformed line (1-based), 0 = none
} CodesReport;

// Field by field: a "= { 0 }" initializer becomes a memset call, which mods can't import.
static void clear_report(CodesReport* r) {
    r->added = r->enablers = r->buttons = r->unknown = r->malformed = r->dropped = r->truncated = 0;
    r->rom_mismatch = r->first_bad_line = 0;
    r->rom_rev = -1;
}

static Code codes[MAX_CODES];
static int num_codes;

static int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int is_conditional(u32 type) {
    return type == 0xD0 || type == 0xD1 || type == 0xD2 || type == 0xD3;
}

static int is_write(u32 type) {
    return type == 0x80 || type == 0x81 || type == 0xA0 || type == 0xA1;
}

static int is_enabler(u32 type) {
    return type == 0xF0 || type == 0xF1 || type == 0xF6 || type == 0xFF || type == 0xEE || type == 0xDE ||
        type == 0x2A || type == 0x3C;
}

// Governs the next unit (a conditional or a repeater).
static int is_prefix(u32 type) {
    return type == 0x50 || is_conditional(type);
}

#ifdef CHEAT_REV0_TRANSLATION
// Revision translation hook (No Mercy: published codes target Rev 0, the recomp runs Rev 1).
// rev_translate() maps one Rev 0 address to Rev 1, or returns 0 for "no mapping", in which case the
// code is dropped along with the conditional/repeater governing it and the unit it governs.
// The map is a sorted list of non-overlapping Rev 0 ranges, each moved by a constant delta. The default
// is the one range verified so far: the main data segment sits 0x1530 lower in Rev 1 (unlock table,
// Championship money). A fuller map (Track A's Rev0<->Rev1 address map) drops in as a generated header:
// build with -DREV_MAP_HEADER=\"rev0_to_rev1_nomercy.h\" defining rev0_to_rev1[] and REV0_MAP_COUNT.
typedef struct {
    u32 start; // first Rev 0 address
    u32 end;   // one past the last
    s32 delta; // Rev 1 address = Rev 0 address + delta
} RevRange;
#ifdef REV_MAP_HEADER
#include REV_MAP_HEADER
#else
static const RevRange rev0_to_rev1[] = {
    { 0x80040000u, 0x800D9960u, -0x1530 },
};
#define REV0_MAP_COUNT 1
#endif

static int rev_translate(u32 addr, u32* out) {
    for (int i = 0; i < REV0_MAP_COUNT; i++) {
        if (addr >= rev0_to_rev1[i].start && addr < rev0_to_rev1[i].end) {
            *out = addr + (u32)rev0_to_rev1[i].delta;
            return 1;
        }
    }
    return 0;
}

static int translate_rev0(u32* code) {
    u32 addr = RDRAM_START | (*code & 0x00FFFFFF);
    u32 mapped;
    if (!rev_translate(addr, &mapped)) {
        return 0;
    }
    *code = (*code & 0xFF000000) | (mapped & 0x00FFFFFF);
    return 1;
}
#endif

// Parser state shared by both front ends.
typedef struct {
    int start;     // num_codes when this parse began: never remove below it
    int drop_next; // units still to drop after an untranslatable conditional
    int translate;
    CodesReport* report;
} ParseState;

static void drop_dangling_prefixes(ParseState* s) {
    while (num_codes > s->start && is_prefix(codes[num_codes - 1].code >> 24)) {
        num_codes--;
        s->report->dropped++;
    }
}

static void push_code(ParseState* s, u32 code, u16 value) {
    u32 type = code >> 24;
    if (s->drop_next > 0) {
        // Dropping a whole unit: a prefix extends it by one more code.
        s->drop_next--;
        if (is_prefix(type)) s->drop_next++;
        s->report->dropped++;
        return;
    }
    if (is_enabler(type)) { s->report->enablers++; return; }
    if (type == 0x88 || type == 0x89) { s->report->buttons++; return; }
    if (!is_write(type) && !is_prefix(type)) { s->report->unknown++; return; }
#ifdef CHEAT_REV0_TRANSLATION
    if (s->translate && type != 0x50 && !translate_rev0(&code)) {
        // Untranslatable: drop what governs it, and what it governs.
        s->report->dropped++;
        drop_dangling_prefixes(s);
        if (is_conditional(type)) s->drop_next = 1;
        return;
    }
#endif
    if (num_codes >= MAX_CODES) { s->report->truncated++; return; }
    codes[num_codes].code = code;
    codes[num_codes].value = value;
    num_codes++;
}

static void finish_parse(ParseState* s) {
    // A trailing conditional or repeater would govern the next list's first code.
    drop_dangling_prefixes(s);
    s->report->added += num_codes - s->start;
}

// Codes box / presets: hex tokens, appended to codes[]. An 8-digit code word followed by a value of
// up to 4 digits, or both run together as one 12-digit token. Any non-hex character separates tokens.
static void parse_codes(const char* text, int translate, CodesReport* report) {
    CodesReport scratch;
    clear_report(&scratch);
    ParseState s;
    s.start = num_codes;
    s.drop_next = 0;
    s.translate = translate;
    s.report = report ? report : &scratch;
    const char* p = text;

    while (*p != '\0') {
        u32 parts[2] = { 0, 0 };
        int lengths[2] = { 0, 0 };
        int n = 0;
        while (n < 2 && *p != '\0') {
            while (*p != '\0' && hex_value(*p) < 0) p++;
            if (*p == '\0') break;
            u32 v = 0;
            int len = 0;
            while (hex_value(*p) >= 0) {
                if (len == 8 && n == 0) {
                    // 12-digit token: the first 8 digits are the code word.
                    parts[0] = v;
                    lengths[0] = 8;
                    n = 1;
                    v = 0;
                    len = 0;
                }
                v = (v << 4) | (u32)hex_value(*p);
                len++;
                p++;
            }
            parts[n] = v;
            lengths[n] = len;
            n++;
        }
        if (n < 2) break;
        if (lengths[0] != 8 || lengths[1] > 4) continue; // malformed pair: skip it
        push_code(&s, parts[0], (u16)parts[1]);
    }
    finish_parse(&s);
}

static char lower(char c) {
    return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
}

static int is_space(char c) {
    return c == ' ' || c == '\t' || c == '\r';
}

// "rom:" tag value: "<game>" or "<game>-rev<N>" ('-', '_' or ' ' before "rev").
// Sets report->rom_rev; returns 0 when the game differs from game_key.
static int parse_rom_tag(const char* p, const char* end, const char* game_key, CodesReport* r) {
    while (p < end && is_space(*p)) p++;
    const char* k = game_key;
    while (*k != '\0' && p < end && lower(*p) == *k) { p++; k++; }
    if (*k != '\0') return 0;
    if (p < end && (*p == '-' || *p == '_' || *p == ' ')) {
        p++;
        if (p + 3 <= end && lower(p[0]) == 'r' && lower(p[1]) == 'e' && lower(p[2]) == 'v') {
            p += 3;
            while (p < end && is_space(*p)) p++;
            if (p < end && *p >= '0' && *p <= '9') {
                r->rom_rev = *p - '0';
                p++;
            }
        }
    }
    while (p < end && is_space(*p)) p++;
    return p == end; // nothing else allowed: "nomercy2" is not "nomercy"
}

// A conversion's codes file (UTF-8, optional BOM, LF or CRLF), appended to codes[].
//   game_key    the game this build runs ("nomercy"); a "# rom:" tag naming another game rejects the file
//   native_rev  the revision this port runs (No Mercy 1, others 0)
// Per line, after trimming:
//   "# rom: <game>[-rev<N>]"           the tag (first one counts), optional
//   "#..." or "//..." or ";..."        comment
//   "[...]"                            section header (a cheat's name)
//   "XXXXXXXX YYYY [note]"             a code: 8 hex digits, then ' ', tab, ':' or '-' separators, then
//   "XXXXXXXXYYYY [note]"              exactly 4 hex digits, then the end, whitespace, or a comment
//   anything else                      a cheat's name, ignored; but a line that starts with 8 hex digits
//                                      and isn't a valid code counts as malformed
// A trailing "# ..." or "// ..." comment after a code is allowed.
__attribute__((unused)) // only games built with AKI_HOST_CONVERSION read codes files
static void parse_code_file(const char* text, int length, const char* game_key, int native_rev, CodesReport* r) {
    ParseState s;
    s.start = num_codes;
    s.drop_next = 0;
    s.translate = 0;
    s.report = r;
    r->rom_rev = -1;
    const char* p = text;
    const char* text_end = text + length;
    int tag_seen = 0;
    int line_no = 0;

    if (length >= 3 && (u8)p[0] == 0xEF && (u8)p[1] == 0xBB && (u8)p[2] == 0xBF) p += 3;

    // First pass: find the tag, since it decides translation for every code.
    for (const char* q = p; q < text_end;) {
        const char* eol = q;
        while (eol < text_end && *eol != '\n') eol++;
        const char* a = q;
        while (a < eol && is_space(*a)) a++;
        const char* b = eol;
        while (b > a && is_space(b[-1])) b--;
        if (!tag_seen && a < b && *a == '#') {
            const char* t = a + 1;
            while (t < b && is_space(*t)) t++;
            if (b - t >= 4 && lower(t[0]) == 'r' && lower(t[1]) == 'o' && lower(t[2]) == 'm' && t[3] == ':') {
                tag_seen = 1;
                if (!parse_rom_tag(t + 4, b, game_key, r)) r->rom_mismatch = 1;
            }
        }
        q = eol < text_end ? eol + 1 : eol;
    }
    if (r->rom_rev >= 0 && r->rom_rev != native_rev) {
#ifdef CHEAT_REV0_TRANSLATION
        if (native_rev == 1 && r->rom_rev == 0) s.translate = 1;
        else r->rom_mismatch = 1;
#else
        r->rom_mismatch = 1;
#endif
    }
    if (r->rom_mismatch) return; // wrong game or revision: apply nothing rather than corrupt RAM

    while (p < text_end) {
        const char* eol = p;
        while (eol < text_end && *eol != '\n') eol++;
        line_no++;
        const char* a = p;
        while (a < eol && is_space(*a)) a++;
        p = eol < text_end ? eol + 1 : eol;

        // Leading hex run: the code word (8) or code word + value (12).
        u32 word = 0;
        int len = 0;
        const char* c = a;
        while (c < eol && hex_value(*c) >= 0 && len < 13) { word = (word << 4) | (u32)hex_value(*c); c++; len++; }
        if (len < 8) continue; // comment, header, name, blank
        int ok = 0;
        u32 code = 0, value = 0;
        if (len == 12) {
            code = 0;
            value = 0;
            const char* d = a;
            for (int i = 0; i < 8; i++) code = (code << 4) | (u32)hex_value(*d++);
            for (int i = 0; i < 4; i++) value = (value << 4) | (u32)hex_value(*d++);
            ok = 1;
        }
        else if (len == 8) {
            code = word;
            const char* d = c;
            int seps = 0;
            while (d < eol && (is_space(*d) || *d == ':' || *d == '-')) { d++; seps++; }
            int vlen = 0;
            while (d < eol && hex_value(*d) >= 0 && vlen < 5) { value = (value << 4) | (u32)hex_value(*d); d++; vlen++; }
            ok = seps > 0 && vlen == 4;
            c = d;
        }
        // After the code: end of line, whitespace (a note), or a comment.
        if (ok && c < eol && !is_space(*c) && *c != '#' && !(*c == '/' && c + 1 < eol && c[1] == '/')) ok = 0;
        if (!ok) {
            // "DEADBEEF Mode" is a name; only count it if it really looks like a code attempt.
            int looks_like_code = len >= 8 && (c >= eol || is_space(*c) || *c == ':' || *c == '-');
            const char* d = c;
            while (d < eol && (is_space(*d) || *d == ':' || *d == '-')) d++;
            if (looks_like_code && (d >= eol || hex_value(*d) >= 0)) {
                r->malformed++;
                if (r->first_bad_line == 0) r->first_bad_line = line_no;
            }
            continue;
        }
        push_code(&s, code, (u16)value);
    }
    finish_parse(&s);
}

static int in_rdram(u32 addr, u32 size) {
    return addr >= RDRAM_START && addr + size <= RDRAM_END;
}

static void write_value(u32 type, u32 addr, u32 value) {
    if (type == 0x80 || type == 0xA0) {
        if (in_rdram(addr, 1)) CODES_WRITE8(addr, value);
    }
    else {
        addr &= ~1u;
        if (in_rdram(addr, 2)) CODES_WRITE16(addr, value);
    }
}

static u32 code_addr(const Code* c) {
    return RDRAM_START | (c->code & 0x00FFFFFF);
}

static int condition_holds(const Code* c) {
    u32 type = c->code >> 24;
    u32 addr = code_addr(c);
    u32 value = c->value;
    u32 current;
    if (type == 0xD0 || type == 0xD2) {
        if (!in_rdram(addr, 1)) return 0;
        current = CODES_READ8(addr);
        value &= 0xFF;
    }
    else {
        addr &= ~1u;
        if (!in_rdram(addr, 2)) return 0;
        current = CODES_READ16(addr);
    }
    return (type == 0xD0 || type == 0xD1) ? (current == value) : (current != value);
}

// Index just past the unit that starts at i.
static int unit_end(int i) {
    while (i < num_codes && is_prefix(codes[i].code >> 24)) i++;
    return i < num_codes ? i + 1 : num_codes;
}

static void apply_codes(void) {
    int i = 0;
    while (i < num_codes) {
        const Code* c = &codes[i];
        u32 type = c->code >> 24;
        if (is_conditional(type)) {
            i = condition_holds(c) ? i + 1 : unit_end(i + 1);
        }
        else if (type == 0x50) {
            // Serial repeater: applies to the next code, which must be a write.
            if (i + 1 >= num_codes) break;
            const Code* next = &codes[i + 1];
            u32 count = (c->code >> 8) & 0xFF;
            u32 step = c->code & 0xFF;
            u32 next_type = next->code >> 24;
            if (is_write(next_type)) {
                for (u32 k = 0; k < count; k++) {
                    write_value(next_type, code_addr(next) + k * step, next->value + k * c->value);
                }
                i += 2;
            }
            else {
                i = unit_end(i + 1); // 50 before a conditional: not a GameShark form; skip both
            }
        }
        else {
            write_value(type, code_addr(c), c->value);
            i++;
        }
    }
}

#endif
