// Host-side tests of the code engine: both parsers, Rev 0 translation, and the per-frame semantics
// (writes, conditionals, repeaters) against a fake big-endian RDRAM. No game needed.
// Build + run: .\mods\cheats\tests\run.ps1
#include <stdio.h>
#include <string.h>

// Fake 8 MB RDRAM in N64 (big-endian) byte order. The mod uses plain pointers instead, which the mod
// recompiler maps onto the runtime's byte-swapped RDRAM.
static unsigned char ram[0x800000];
#define CODES_READ8(a)      (ram[(a) - 0x80000000u])
#define CODES_READ16(a)     ((u16)((ram[(a) - 0x80000000u] << 8) | ram[(a) - 0x80000000u + 1]))
#define CODES_WRITE8(a, v)  (ram[(a) - 0x80000000u] = (u8)(v))
#define CODES_WRITE16(a, v) (ram[(a) - 0x80000000u] = (u8)((v) >> 8), ram[(a) - 0x80000000u + 1] = (u8)(v))

#define AKI_FRAME_HOOK func_test
#define AKI_GAME_NOMERCY 1
#define CHEAT_REV0_TRANSLATION
#include "../src/cheats.c" // cheat_presets.h comes from tests/ (run.ps1 puts it first on the path)

static int failures;

static void check(const char* name, int ok) {
    printf("%s %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) failures++;
}

static int same_codes(int n, const Code* want) {
    int ok = num_codes == n;
    for (int i = 0; ok && i < n; i++) ok = codes[i].code == want[i].code && codes[i].value == want[i].value;
    if (!ok) {
        printf("  got %d codes:", num_codes);
        for (int i = 0; i < num_codes; i++) printf(" %08X:%04X", codes[i].code, codes[i].value);
        printf("\n");
    }
    return ok;
}

static void expect(const char* name, const char* text, int translate, int n, const Code* want) {
    num_codes = 0;
    parse_codes(text, translate, 0);
    check(name, same_codes(n, want));
}

static CodesReport file_report;
static void parse_file(const char* text) {
    num_codes = 0;
    memset(&file_report, 0, sizeof(file_report));
    parse_code_file(text, (int)strlen(text), "nomercy", 1, &file_report);
}

static void expect_file(const char* name, const char* text, int n, const Code* want) {
    parse_file(text);
    check(name, same_codes(n, want));
}

static u16 peek16(u32 a) { return CODES_READ16(a); }
static u8 peek8(u32 a) { return CODES_READ8(a); }

static void run_file(const char* text) {
    parse_file(text);
    apply_codes();
}

static void box_tests(void) {
    const Code unlock_rev1[] = { { 0x50000B02, 0x0000 }, { 0x810BD910, 0xFFFF } };
    expect("rev1 codes pass through", "50000B02 0000 810BD910 FFFF", 0, 2, unlock_rev1);
    expect("rev0 unlock translates", "50000B02 0000 810BEE40 FFFF", 1, 2, unlock_rev1);

    const Code money[] = { { 0x81097EDA, 0xFFFF } };
    expect("rev0 championship money translates", "8109940A FFFF", 1, 1, money);
    expect("separators: colon, comma, newline", "8109940A:FFFF,\n", 1, 1, money);
    expect("12-digit token", "8109940AFFFF", 1, 1, money);

    expect("rev0 mall money (overlay area) dropped", "8114FAD6 FFFF", 1, 0, 0);
    expect("dropped code takes its repeater with it", "50000B02 0000 8114FAD6 FFFF", 1, 0, 0);
    const Code after_cond[] = { { 0x81097EDA, 0x0001 } };
    expect("dropped conditional takes its target", "D114FAD6 0001 8109940A 0002 8109940A 0001", 1, 1, after_cond);
    expect("dropped conditional takes a whole repeater unit", "D114FAD6 0001 50000B02 0000 810BEE40 FFFF 8109940A 0001", 1, 1, after_cond);

    const Code no_master[] = { { 0x800BD910, 0x00FF } };
    expect("master code ignored", "F103E2C0 2400 800BD910 00FF", 0, 1, no_master);
    expect("empty", "", 0, 0, 0);
    expect("dangling conditional at the end dropped", "800BD910 00FF D00BD910 0001", 0, 1, no_master);

    // Presets and custom codes stack: parse_codes appends.
    num_codes = 0;
    parse_codes(cheat_presets[0].codes, 0, 0);
    parse_codes("8109940A FFFF", 1, 0);
    check("presets + custom codes stack",
        num_codes == 3 && codes[0].code == 0x50000B02 && codes[1].code == 0x810BD910 && codes[2].code == 0x81097EDA);
    expect("junk", "hello world", 0, 0, 0);
}

static void file_tests(void) {
    const Code two[] = { { 0x810BD910, 0xFFFF }, { 0x800BD920, 0x0001 } };
    expect_file("file: comments, names, sections, CRLF, BOM",
        "\xEF\xBB\xBF# My conversion\r\n"
        "# rom: nomercy-rev1\r\n"
        "[Unlock everything]\r\n"
        "Unlock Everything\r\n"
        "810BD910 FFFF   // all of it\r\n"
        "; another comment\r\n"
        "\r\n"
        "  800BD920:0001 # indented, colon\r\n", 2, two);
    check("file: no malformed lines", file_report.malformed == 0 && file_report.rom_rev == 1 && file_report.added == 2);

    expect_file("file: no tag means this port's revision", "810BD910 FFFF\n800BD920 0001", 2, two);
    expect_file("file: 12-digit code and trailing note", "810BD910FFFF\n800BD920 0001 note words\n", 2, two);

    const Code money[] = { { 0x81097EDA, 0xFFFF } };
    expect_file("file: rev0 tag translates", "# rom: nomercy-rev0\n8109940A FFFF\n", 1, money);
    check("file: rev0 tag recorded", file_report.rom_rev == 0);
    expect_file("file: rev0 tag, untranslatable dropped", "#rom: NoMercy rev0\n8114FAD6 FFFF\n8109940A FFFF\n", 1, money);
    check("file: dropped counted", file_report.dropped == 1);

    expect_file("file: other game's tag rejects everything", "# rom: wm2k\n810BD910 FFFF\n", 0, 0);
    check("file: mismatch flagged", file_report.rom_mismatch == 1);
    expect_file("file: unknown revision rejects everything", "# rom: nomercy-rev2\n810BD910 FFFF\n", 0, 0);
    expect_file("file: 'nomercy2' is not 'nomercy'", "# rom: nomercy2\n810BD910 FFFF\n", 0, 0);
    expect_file("file: only the first tag counts", "# rom: nomercy-rev1\n# rom: wm2k\n810BD910 FFFF\n", 1, two);

    expect_file("file: enablers and button codes ignored, counted",
        "F10396B0 2400\nDE000400 0000\n2A000000 0000\n3C000000 0000\nF0000000 0000\n"
        "88000000 0001\n89000000 0001\n810BD910 FFFF\n", 1, two);
    check("file: ignore counts", file_report.enablers == 5 && file_report.buttons == 2 && file_report.unknown == 0);

    expect_file("file: malformed lines counted, names are not",
        "Deadbeef Mode\n8109940A FFF\n810BD910 FFFF\n8109940A FFFFF\n", 1, two);
    check("file: malformed count and first line", file_report.malformed == 2 && file_report.first_bad_line == 2);

    expect_file("file: cht2codes 'unknown' revision tag refuses", "# rom: nomercy-unknown\n810BD910 FFFF\n", 0, 0);
    check("file: unknown flagged", file_report.rom_mismatch == 1);
    expect_file("file: cht2codes '????' line stays a comment", "# 81097EDA ???? -- choose a value\n", 0, 0);
    check("file: '????' comment isn't malformed", file_report.malformed == 0);

    u32 mapped = 0;
    check("rev map: range start maps", rev_translate(0x80040000u, &mapped) && mapped == 0x8003EAD0u);
    check("rev map: range end is exclusive", !rev_translate(0x800D9960u, &mapped));
    check("rev map: below the range has no mapping", !rev_translate(0x8003FFFFu, &mapped));

    expect_file("file: commented-out code ignored", "// 810BD910 FFFF\n#810BD910 FFFF\n", 0, 0);
}

static void apply_tests(void) {
    memset(ram, 0, sizeof(ram));
    run_file("800BD910 00AB\n810BD912 1234\n");
    check("apply: 8-bit write", peek8(0x800BD910) == 0xAB);
    check("apply: 16-bit write is big-endian", peek8(0x800BD912) == 0x12 && peek8(0x800BD913) == 0x34);

    memset(ram, 0, sizeof(ram));
    run_file("A00BD910 0001\nA10BD912 BEEF\n");
    check("apply: A0/A1 write the same RAM", peek8(0x800BD910) == 1 && peek16(0x800BD912) == 0xBEEF);

    memset(ram, 0, sizeof(ram));
    run_file("810BD911 1234\n");
    check("apply: odd 16-bit address aligned down", peek16(0x800BD910) == 0x1234);

    memset(ram, 0, sizeof(ram));
    run_file("50000B02 0000\n810BD910 FFFF\n");
    int all = 1;
    for (u32 k = 0; k < 0x0B; k++) all &= peek16(0x800BD910 + k * 2) == 0xFFFF;
    check("apply: 50 repeater (No Mercy unlock shape)", all && peek16(0x800BD910 + 0x0B * 2) == 0);

    memset(ram, 0, sizeof(ram));
    run_file("50000304 0002\n80100000 0010\n");
    check("apply: repeater value increment, 8-bit",
        peek8(0x80100000) == 0x10 && peek8(0x80100004) == 0x12 && peek8(0x80100008) == 0x14 && peek8(0x8010000C) == 0);

    memset(ram, 0, sizeof(ram));
    ram[0x100000] = 0x05;
    run_file("D0100000 0005\n80100010 0001\nD0100000 0006\n80100011 0001\n");
    check("apply: D0 equal true/false", peek8(0x80100010) == 1 && peek8(0x80100011) == 0);

    memset(ram, 0, sizeof(ram));
    ram[0x100000] = 0x05;
    run_file("D2100000 0005\n80100010 0001\nD2100000 0006\n80100011 0001\n");
    check("apply: D2 not-equal true/false", peek8(0x80100010) == 0 && peek8(0x80100011) == 1);

    memset(ram, 0, sizeof(ram));
    ram[0x100000] = 0x12; ram[0x100001] = 0x34;
    run_file("D1100000 1234\n80100010 0001\nD3100000 1234\n80100011 0001\nD3100000 FFFF\n80100012 0001\n");
    check("apply: D1/D3 16-bit", peek8(0x80100010) == 1 && peek8(0x80100011) == 0 && peek8(0x80100012) == 1);

    memset(ram, 0, sizeof(ram));
    run_file("D0100000 0001\n50000202 0000\n81100010 FFFF\n80100020 0007\n");
    check("apply: false conditional skips the whole repeater unit",
        peek16(0x80100010) == 0 && peek16(0x80100012) == 0 && peek8(0x80100020) == 7);

    memset(ram, 0, sizeof(ram));
    ram[0x100000] = 1;
    run_file("D0100000 0001\nD0100001 0001\n80100010 0001\n80100020 0007\n");
    check("apply: stacked conditionals must all hold", peek8(0x80100010) == 0 && peek8(0x80100020) == 7);
    ram[0x100001] = 1;
    apply_codes();
    check("apply: stacked conditionals, all true", peek8(0x80100010) == 1);

    // Per frame: the engine re-applies every call, so the game can't undo a write for long.
    memset(ram, 0, sizeof(ram));
    run_file("81097EDA FFFF\n");
    ram[0x97EDA] = 0; ram[0x97EDB] = 0;
    apply_codes();
    check("apply: re-applied every frame", peek16(0x80097EDA) == 0xFFFF);

    // Out-of-range writes are refused (no crash, nothing written).
    memset(ram, 0, sizeof(ram));
    run_file("81900000 FFFF\n");
    check("apply: outside 8 MB refused", num_codes == 1);
}

int main(void) {
    box_tests();
    file_tests();
    apply_tests();
    printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures != 0;
}
