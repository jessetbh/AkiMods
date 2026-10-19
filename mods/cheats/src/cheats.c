// AKI Cheats: a generic GameShark-style cheat engine for the AKI recomp family.
//
// Three sources feed one code list (codes_engine.h), applied at least once per frame (AKI_ON_FRAME):
//   1. the active conversion's codes file (AKI_CONVERSION; games built with AKI_HOST_CONVERSION only),
//      read once at the first frame and always on while that conversion runs;
//   2. the per-game presets turned on in the Mods menu (cheat_presets.h, generated from mod.psd1);
//   3. the "Codes" box, e.g. "50000B02 0000 810BD910 FFFF" (any non-hex character separates tokens).
// 2 and 3 are re-read about twice a second.

#include "aki_mod.h"
#include "recomp_api.h"
#include "codes_engine.h"

#define MAX_TEXT 4096
#define RELOAD_INTERVAL 30 // frames between re-reading the config

// The game key a codes file's "# rom:" tag must name, and the cartridge revision this port runs.
#if defined(AKI_GAME_NOMERCY)
#define CODES_GAME_KEY "nomercy"
#define CODES_NATIVE_REV 1
#elif defined(AKI_GAME_WT)
#define CODES_GAME_KEY "wt"
#elif defined(AKI_GAME_REVENGE)
#define CODES_GAME_KEY "revenge"
#elif defined(AKI_GAME_WM2K)
#define CODES_GAME_KEY "wm2k"
#elif defined(AKI_GAME_VPW64)
#define CODES_GAME_KEY "vpw64"
#elif defined(AKI_GAME_VPW2)
#define CODES_GAME_KEY "vpw2"
#else
#define CODES_GAME_KEY "unknown"
#endif
#ifndef CODES_NATIVE_REV
#define CODES_NATIVE_REV 0
#endif

// Named, per-game presets (Bool options in the Mods menu), generated into cheat_presets.h
// by generate.ps1 from mod.psd1. Their codes are written for this recomp's ROM revision.
typedef struct {
    const char* id;    // config option id
    const char* codes;
} Preset;
#include "cheat_presets.h"

static int num_conversion_codes; // codes[0 .. num_conversion_codes) come from the conversion
static u32 frames_until_reload;
static u32 last_state_hash;
static int loaded;

static u32 hash_text(const char* s) {
    u32 h = 2166136261u; // FNV-1a
    for (; *s != '\0'; s++) {
        h = (h ^ (u8)*s) * 16777619u;
    }
    return h;
}

#ifdef AKI_HOST_CONVERSION
// Exported by the game (src/main/main.cpp, register_conversion_exports). Importing a function the
// game doesn't export stops the mod loading, so only games that have it build with
// AKI_HOST_CONVERSION (mod.psd1, PerGame.<game>.Defines).
//   aki_conversion_codes  the active conversion's codes file as a NUL-terminated string in the recomp
//                         heap (free it with recomp_free), or NULL when no conversion or no codes
//   aki_conversion_log    one line to the game's log ("[conversion] ...")
RECOMP_IMPORT("*", char* aki_conversion_codes(void));
RECOMP_IMPORT("*", void aki_conversion_log(const char* msg));
RECOMP_IMPORT("*", void recomp_free(void* ptr)); // runtime heap (librecomp heap.cpp)

static char* append_text(char* out, const char* s) {
    while (*s != '\0') *out++ = *s++;
    return out;
}

static char* append_int(char* out, int v) {
    char digits[12];
    int n = 0;
    if (v < 0) { *out++ = '-'; v = -v; }
    do { digits[n++] = (char)('0' + v % 10); v /= 10; } while (v > 0);
    while (n > 0) *out++ = digits[--n];
    return out;
}

static char* append_count(char* out, const char* label, int v) {
    if (v == 0) return out;
    out = append_text(out, ", ");
    out = append_int(out, v);
    return append_text(out, label);
}

static void load_conversion_codes(void) {
    char* text = aki_conversion_codes();
    if (text == 0) return;
    int length = 0;
    while (text[length] != '\0') length++;

    static CodesReport r;
    clear_report(&r);
    num_codes = 0;
    parse_code_file(text, length, CODES_GAME_KEY, CODES_NATIVE_REV, &r);
    recomp_free(text);
    num_conversion_codes = num_codes;

    static char msg[256];
    char* m = msg;
    if (r.rom_mismatch) {
        m = append_text(m, "codes: the file's \"# rom:\" tag doesn't match this game (" CODES_GAME_KEY " rev ");
        m = append_int(m, CODES_NATIVE_REV);
        m = append_text(m, "); no codes applied");
    }
    else {
        m = append_text(m, "codes: ");
        m = append_int(m, r.added);
        m = append_text(m, " active");
        if (r.rom_rev == 0 && CODES_NATIVE_REV == 1) m = append_text(m, " (translated from Rev 0)");
        m = append_count(m, " enabler codes ignored (not needed)", r.enablers);
        m = append_count(m, " button codes (88/89) ignored", r.buttons);
        m = append_count(m, " unknown code types ignored", r.unknown);
        m = append_count(m, " dropped (no Rev 1 address)", r.dropped);
        m = append_count(m, " over the limit", r.truncated);
        if (r.malformed > 0) {
            m = append_count(m, " malformed lines, first at line ", r.malformed);
            m = append_int(m, r.first_bad_line);
        }
    }
    *m = '\0';
    aki_conversion_log(msg);
}
#endif

// Rebuilds the preset + Codes box part of codes[] when any of them changed since the last reload.
static void reload_codes(void) {
    int translate = 0;
#ifdef CHEAT_REV0_TRANSLATION
    translate = recomp_get_config_u32("code_version") == 1;
#endif
    u32 enabled = 0;
    for (int i = 0; i < NUM_PRESETS; i++) {
        if (recomp_get_config_u32(cheat_presets[i].id)) enabled |= 1u << i;
    }

    // Copy into a bounded buffer so an over-long option can't run parsing past MAX_TEXT.
    static char buffer[MAX_TEXT + 1];
    buffer[0] = '\0';
    char* text = recomp_get_config_string("codes");
    if (text != 0) {
        int i = 0;
        for (; i < MAX_TEXT && text[i] != '\0'; i++) {
            buffer[i] = text[i];
        }
        buffer[i] = '\0';
        recomp_free_config_string(text);
    }

    u32 h = hash_text(buffer) ^ (enabled * 2654435761u) ^ (u32)translate;
    if (loaded && h == last_state_hash) return;
    loaded = 1;
    last_state_hash = h;

    num_codes = num_conversion_codes;
    for (int i = 0; i < NUM_PRESETS; i++) {
        if (enabled & (1u << i)) parse_codes(cheat_presets[i].codes, 0, 0);
    }
    parse_codes(buffer, translate, 0);
}

AKI_ON_FRAME void aki_cheats_on_frame(void) {
#ifdef AKI_HOST_CONVERSION
    static int conversion_checked;
    if (!conversion_checked) {
        conversion_checked = 1;
        load_conversion_codes();
    }
#endif
    if (frames_until_reload == 0) {
        reload_codes();
        frames_until_reload = RELOAD_INTERVAL;
    }
    frames_until_reload--;
    apply_codes();
}
