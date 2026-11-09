// Host tests for rules.h and scratch_layout.h (aki-roadmap#247: full replacement rosters). Build and run: tests/run.ps1
// (twice: F3DEX2 and -DMI_F3DEX1).
#include <stdio.h>
#include <string.h>
#include "../src/scratch_layout.h"
#include "../src/rules.h"

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static int errors, last_error_line;
static char last_error[128];
static void on_error(int line_no, const char* why) {
    errors++;
    last_error_line = line_no;
    snprintf(last_error, sizeof last_error, "%s", why);
}

static MiRule rules[MI_RULES];
static char text[MI_TEXT_MAX];

static int parse(const char* src) {
    errors = 0;
    last_error[0] = 0;
    int n = (int)strlen(src);
    memcpy(text, src, (size_t)n);
    return mi_parse_rules(text, n, rules, 4, on_error);
}

int main(void) {
    // Layout: the lists, the clear and models.txt don't overlap and stay inside the 256 KB scratch.
    CHECK(2u * MI_MAX_SLOTS * MI_LIST_BYTES <= MI_CLEAR_OFFSET);
    CHECK(MI_CLEAR_OFFSET + MI_CLEAR_BYTES <= MI_TEXT_OFFSET);
    CHECK(MI_TEXT_OFFSET + MI_TEXT_MAX == MI_SCRATCH_USED && MI_SCRATCH_USED <= MI_SCRATCH_SIZE);
#ifdef MI_F3DEX1
    CHECK(MI_SCRATCH_USED == 0x15000u);
#else
    CHECK(MI_SCRATCH_USED == 0x11000u);
#endif

    // Every line form, comments, CRLF, a BOM.
    int n = parse("\xEF\xBB\xBFsnake slot 1\r\nbanjo   wrestler 0x0404 # Banjo\r\n# a comment\r\n\r\n"
                  "cesiumman wrestler 0x0302 costume 2\roff banjo\n");
    CHECK(n == 4 && errors == 0);
    CHECK(rules[0].kind == RULE_SLOT && rules[0].a == 0 && !strcmp(rules[0].pack, "snake"));
    CHECK(rules[1].kind == RULE_WRESTLER && rules[1].a == 0x404 && rules[1].b == -1 && !strcmp(rules[1].pack, "banjo"));
    CHECK(rules[2].kind == RULE_WRESTLER && rules[2].a == 0x302 && rules[2].b == 2);
    CHECK(rules[3].kind == RULE_OFF && !strcmp(rules[3].pack, "banjo"));

    // Bad lines are skipped with their line number.
    n = parse("snake slot 5\nBad wrestler 1\nsnake wrestler 1 costume -1\nok wrestler 0x10\n");
    CHECK(n == 1 && errors == 3 && last_error_line == 3 && rules[0].a == 0x10);

    // A full replacement roster in the conversion's own format (the game's models_txt, aki-roadmap#226): 300 lines of
    // the longest kind (a 31-character pack id, a costume) fit the text buffer; the first 256 become rules and the
    // rest are each reported.
    static char big[MI_TEXT_MAX];
    int len = snprintf(big, sizeof big, "# from aki.json components.models.slots\n");
    for (int i = 0; i < 300; i++)
        len += snprintf(big + len, sizeof big - (size_t)len, "pack-%026d wrestler 0x%04X costume %d\n", i, 0x100 + i, i % 4);
    CHECK(len < (int)MI_TEXT_MAX - 1);   // the core reads at most MI_TEXT_MAX - 1 bytes (one for the terminator)
    errors = 0;
    memcpy(text, big, (size_t)len);
    n = mi_parse_rules(text, len, rules, 4, on_error);
    CHECK(n == MI_RULES && MI_RULES == 256);
    CHECK(errors == 300 - 256 && last_error_line == 301 && strstr(last_error, "256 at most"));
    CHECK(rules[255].kind == RULE_WRESTLER && rules[255].a == 0x100 + 255 && rules[255].b == 255 % 4);
    CHECK(strlen(rules[255].pack) == 31);

    // The old limits (aki-roadmap#247): 32 rules and 16 KB were too few for this roster.
    CHECK(len > 0x4000);

    printf("[modelimport rules tests] %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
