// AKI Cheats: a generic GameShark-style cheat engine for the AKI recomp family.
//
// Codes are typed into the mod's "Codes" option in the Mods menu, e.g.
//     50000B02 0000 810BD910 FFFF
// Any non-hex character separates tokens, so spaces, newlines, commas and
// "8109940A:FFFF" all work. Codes are applied at least once per frame (AKI_ON_FRAME).
//
// Supported code types (N64 GameShark / Action Replay):
//   80 / A0  XXXXXX 00YY   8-bit write
//   81 / A1  XXXXXX YYYY   16-bit write
//   D0 / D1  XXXXXX YYYY   run the next code only if the 8/16-bit value equals YY/YYYY
//   D2 / D3  XXXXXX YYYY   run the next code only if the 8/16-bit value differs
//   50       00NNSS IIII   repeat the next write NN times, address += SS, value += IIII
// Master/enabler codes (F0, F1, FF, EE, DE, 2A, 3C, ...) patch the cartridge boot and
// are not needed here; they and button activators (88/89) are ignored.

#include "aki_mod.h"
#include "recomp_api.h"

#define MAX_CODES 256
#define MAX_TEXT 4096
#define RELOAD_INTERVAL 30 // frames between re-reading the config

#define RDRAM_START 0x80000000u
#define RDRAM_END   0x80800000u // 8 MB (Expansion Pak)

typedef struct {
    u32 code;  // type in the top byte, address in the low 24 bits
    u16 value;
} Code;

// Named, per-game presets (Bool options in the Mods menu), generated into cheat_presets.h
// by generate.ps1 from mod.psd1. Their codes are written for this recomp's ROM revision.
typedef struct {
    const char* id;    // config option id
    const char* codes;
} Preset;
#include "cheat_presets.h"

static Code codes[MAX_CODES];
static int num_codes;
static u32 frames_until_reload;
static u32 last_state_hash;
static int loaded;

static int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static u32 hash_text(const char* s) {
    u32 h = 2166136261u; // FNV-1a
    for (; *s != '\0'; s++) {
        h = (h ^ (u8)*s) * 16777619u;
    }
    return h;
}

static int is_conditional(u32 type) {
    return type == 0xD0 || type == 0xD1 || type == 0xD2 || type == 0xD3;
}

static int is_write(u32 type) {
    return type == 0x80 || type == 0x81 || type == 0xA0 || type == 0xA1;
}

#ifdef CHEAT_REV0_TRANSLATION
// No Mercy: published codes target Rev 0, the recomp runs Rev 1. Rev 1's main data
// segment sits 0x1530 lower (verified on the unlock table and Championship money).
// Anything outside that range is unverified, so it's dropped rather than guessed.
static int translate_rev0(u32* code) {
    u32 addr = RDRAM_START | (*code & 0x00FFFFFF);
    if (addr < 0x80040000u || addr >= 0x800D9960u) {
        return 0;
    }
    *code = (*code & 0xFF000000) | ((addr - 0x1530u) & 0x00FFFFFF);
    return 1;
}
#endif

// Parses hex tokens into codes, appending to codes[]: an 8-digit code word followed by a
// 4-digit value, or both run together as one 12-digit token.
static void parse_codes(const char* text, int translate) {
    const int start = num_codes;
    int count = start;
    int drop_next = 0;
    const char* p = text;

    while (*p != '\0' && count < MAX_CODES) {
        // Collect the next two hex tokens (or one 12-digit token).
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

        u32 code = parts[0];
        u32 type = code >> 24;

        if (drop_next) {
            drop_next = 0;
            continue;
        }
        if (!is_write(type) && !is_conditional(type) && type != 0x50) {
            continue; // master codes, button activators, unknown types
        }
#ifdef CHEAT_REV0_TRANSLATION
        if (translate && type != 0x50 && !translate_rev0(&code)) {
            // Untranslatable: also drop the code a conditional or repeater would govern.
            if (is_conditional(type)) drop_next = 1;
            if (count > start && ((codes[count - 1].code >> 24) == 0x50 || is_conditional(codes[count - 1].code >> 24))) {
                count--;
            }
            continue;
        }
#endif
        codes[count].code = code;
        codes[count].value = (u16)parts[1];
        count++;
    }
    num_codes = count;
}

// Rebuilds codes[] from the enabled presets plus the custom Codes text, when any of them
// changed since the last reload.
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

    num_codes = 0;
    for (int i = 0; i < NUM_PRESETS; i++) {
        if (enabled & (1u << i)) parse_codes(cheat_presets[i].codes, 0);
    }
    parse_codes(buffer, translate);
}

static int in_rdram(u32 addr, u32 size) {
    return addr >= RDRAM_START && addr + size <= RDRAM_END;
}

static void write_value(u32 type, u32 addr, u32 value) {
    if (type == 0x80 || type == 0xA0) {
        if (in_rdram(addr, 1)) *(volatile u8*)addr = (u8)value;
    }
    else {
        addr &= ~1u;
        if (in_rdram(addr, 2)) *(volatile u16*)addr = (u16)value;
    }
}

static int condition_holds(u32 type, u32 addr, u32 value) {
    u32 current;
    if (type == 0xD0 || type == 0xD2) {
        if (!in_rdram(addr, 1)) return 0;
        current = *(volatile u8*)addr;
        value &= 0xFF;
    }
    else {
        addr &= ~1u;
        if (!in_rdram(addr, 2)) return 0;
        current = *(volatile u16*)addr;
    }
    return (type == 0xD0 || type == 0xD1) ? (current == value) : (current != value);
}

static void apply_codes(void) {
    for (int i = 0; i < num_codes; i++) {
        u32 type = codes[i].code >> 24;
        u32 addr = RDRAM_START | (codes[i].code & 0x00FFFFFF);
        u32 value = codes[i].value;

        if (is_conditional(type)) {
            if (!condition_holds(type, addr, value)) i++; // skip the governed code
        }
        else if (type == 0x50) {
            // Serial repeater: applies to the next code, which must be a write.
            if (i + 1 >= num_codes) break;
            u32 count = (codes[i].code >> 8) & 0xFF;
            u32 step = codes[i].code & 0xFF;
            u32 next_type = codes[i + 1].code >> 24;
            u32 next_addr = RDRAM_START | (codes[i + 1].code & 0x00FFFFFF);
            u32 next_value = codes[i + 1].value;
            if (is_write(next_type)) {
                for (u32 k = 0; k < count; k++) {
                    write_value(next_type, next_addr + k * step, next_value + k * value);
                }
            }
            i++;
        }
        else {
            write_value(type, addr, value);
        }
    }
}

AKI_ON_FRAME void aki_cheats_on_frame(void) {
    if (frames_until_reload == 0) {
        reload_codes();
        frames_until_reload = RELOAD_INTERVAL;
    }
    frames_until_reload--;
    apply_codes();
}
