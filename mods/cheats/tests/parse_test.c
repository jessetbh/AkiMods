// Host-side test of the code parser and Rev 0 translation (no game needed).
// Build + run: .\mods\cheats\tests\run.ps1
#include <stdio.h>

#define AKI_FRAME_HOOK func_test
#define CHEAT_REV0_TRANSLATION
#include "../src/cheats.c" // cheat_presets.h comes from tests/ (run.ps1 puts it first on the path)

static int failures;

static void expect(const char* name, const char* text, int translate, int n, const Code* want) {
    num_codes = 0;
    parse_codes(text, translate);
    int ok = num_codes == n;
    for (int i = 0; ok && i < n; i++) {
        ok = codes[i].code == want[i].code && codes[i].value == want[i].value;
    }
    printf("%s %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) {
        failures++;
        printf("  got %d codes:", num_codes);
        for (int i = 0; i < num_codes; i++) printf(" %08X:%04X", codes[i].code, codes[i].value);
        printf("\n");
    }
}

int main(void) {
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

    const Code no_master[] = { { 0x800BD910, 0x00FF } };
    expect("master code ignored", "F103E2C0 2400 800BD910 00FF", 0, 1, no_master);
    expect("empty", "", 0, 0, 0);

    // Presets and custom codes stack: parse_codes appends.
    num_codes = 0;
    parse_codes(cheat_presets[0].codes, 0);
    parse_codes("8109940A FFFF", 1);
    const int stacked = num_codes == 3 && codes[0].code == 0x50000B02 && codes[1].code == 0x810BD910 && codes[2].code == 0x81097EDA;
    printf("%s presets + custom codes stack\n", stacked ? "PASS" : "FAIL");
    if (!stacked) failures++;
    expect("junk", "hello world", 0, 0, 0);

    printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures != 0;
}
