// aki_modelimport: models.txt, the rule table (aki-roadmap#130; sizes aki-roadmap#247).
//
//     snake      slot 1                 # a slot (1-4)
//     banjo      wrestler 0x0404        # a wrestler id as the game stores it (costume N optional, any if left out)
//     off banjo                         # turns a pack off, whatever the other lines say
//
// A conversion's aki.json "slots" arrive the same way (the game serves them as models.txt, aki-roadmap#226), so a full
// replacement roster (every wrestler, its costumes and the created-wrestler slots: 100-300 lines) has to fit: MI_RULES
// 256 (~11 KB of mod memory, which lives above 8 MB with the mod's code, not in the game's RAM) and the 32 KB text
// buffer (scratch_layout.h).
//
// Pure, header-only, no libc (host tests: tests/rules_test.c).
#ifndef MI_RULES_H
#define MI_RULES_H

#define MI_RULES 256
#define MI_ID_LEN 32

enum { RULE_SLOT = 1, RULE_WRESTLER, RULE_OFF };

typedef struct {
    char pack[MI_ID_LEN];
    int kind;
    int a, b; // slot (0-based), or wrestler + costume (-1 = any)
} MiRule;

// Reports a skipped line: models.txt line number and why.
typedef void (*MiRuleError)(int line_no, const char* why);

static int mi_str_eq(const char* a, const char* b) {
    while (*a && *a == *b) a++, b++;
    return *a == *b;
}

static void mi_str_copy(char* dst, const char* src, int max) {
    int i = 0;
    for (; src[i] && i < max - 1; i++) dst[i] = src[i];
    dst[i] = 0;
}

static int mi_valid_id(const char* id) {
    int n = 0;
    for (; id[n]; n++) {
        char c = id[n];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) return 0;
    }
    return n > 0 && n < MI_ID_LEN;
}

static int mi_parse_int(const char* s, int* out) {
    int neg = 0;
    unsigned int v = 0;
    if (*s == '-') neg = 1, s++;
    if (!*s) return 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        if (!*s) return 0;
        for (; *s; s++) {
            char c = *s;
            unsigned int d = (c >= '0' && c <= '9') ? (unsigned int)(c - '0') : (c >= 'a' && c <= 'f') ? (unsigned int)(c - 'a' + 10)
                  : (c >= 'A' && c <= 'F') ? (unsigned int)(c - 'A' + 10) : 99;
            if (d > 15) return 0;
            v = v * 16 + d;
        }
    } else {
        for (; *s; s++) {
            if (*s < '0' || *s > '9') return 0;
            v = v * 10 + (unsigned int)(*s - '0');
        }
    }
    *out = neg ? -(int)v : (int)v;
    return 1;
}

// One line, split into at most 6 words (the buffer is modified in place); appends to rules[*count].
static void mi_parse_line(char* s, int line_no, MiRule* rules, int* count, int slots, MiRuleError err) {
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
    if (*count >= MI_RULES) { err(line_no, "too many lines (256 at most), ignored"); return; }
    MiRule* r = &rules[*count];
    if (mi_str_eq(w[0], "off")) {
        if (n != 2 || !mi_valid_id(w[1])) { err(line_no, "expected: off <pack>"); return; }
        mi_str_copy(r->pack, w[1], MI_ID_LEN);
        r->kind = RULE_OFF;
        (*count)++;
        return;
    }
    if (!mi_valid_id(w[0])) { err(line_no, "pack ids are a-z, 0-9, _ and -, at most 31"); return; }
    mi_str_copy(r->pack, w[0], MI_ID_LEN);
    int v;
    if (n == 3 && mi_str_eq(w[1], "slot") && mi_parse_int(w[2], &v) && v >= 1 && v <= slots) {
        r->kind = RULE_SLOT;
        r->a = v - 1;
        (*count)++;
    } else if ((n == 3 || n == 5) && mi_str_eq(w[1], "wrestler") && mi_parse_int(w[2], &v)) {
        r->kind = RULE_WRESTLER;
        r->a = v;
        r->b = -1;
        if (n == 5 && !(mi_str_eq(w[3], "costume") && mi_parse_int(w[4], &r->b) && r->b >= 0)) {
            err(line_no, "expected: <pack> wrestler <id> [costume <n>]");
            return;
        }
        (*count)++;
    } else {
        err(line_no, "expected: <pack> slot <1-4> | <pack> wrestler <id> [costume <n>] | off <pack>");
    }
}

// The whole text (text[size] must be writable: it becomes the terminator); \n, \r\n and \r-only line ends, a UTF-8 BOM
// skipped. Returns the number of rules.
static int mi_parse_rules(char* text, int size, MiRule* rules, int slots, MiRuleError err) {
    int count = 0, line_no = 1;
    text[size] = 0;
    int first = (size >= 3 && (unsigned char)text[0] == 0xEF && (unsigned char)text[1] == 0xBB && (unsigned char)text[2] == 0xBF) ? 3 : 0;
    char* start = text + first;
    for (int i = first; i <= size; i++) {
        if (text[i] == '\n' || text[i] == '\r' || text[i] == 0) {
            char end = text[i];
            char next = (i < size) ? text[i + 1] : 0;
            text[i] = 0;
            mi_parse_line(start, line_no, rules, &count, slots, err);
            if (end == '\n' || (end == '\r' && next != '\n')) line_no++;
            start = &text[i + 1];
        }
    }
    return count;
}

#endif
