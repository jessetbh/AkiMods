// AKI Starter Kit (No Mercy): the conversion's code change, as real mod source.
//
// What it does, so you can see it work without touching the controller:
//   1. The intro movie is skipped (as if you pressed Start as soon as it allows).
//   2. On the title screen the attract demo starts after about 5 seconds instead of 30, and it is always the
//      tag team demo (four wrestlers on screen), never the intro movie.
//   3. Every attract demo match takes place in the Raw arena (venue 0), where this kit's ring mat, logo,
//      turnbuckle pads and RT light rig are.
//
// How it hooks the game (the same technique every AKI mod uses; see ..\..\..\mods\quickmatch for a bigger one):
//   RECOMP_HOOK("func_X")         runs our function at the START of the game's func_X, every time it is called.
//   RECOMP_HOOK_RETURN("func_X")  runs our function right AFTER func_X returns.
// The names are the recompiler's function names (the game repo's syms\dump.toml). Pointers to 0x80xxxxxx are the
// game's RAM: the mod recompiler turns these plain loads and stores into the runtime's RAM accesses.
//
// Every address below is for WWF No Mercy (USA, Rev 1), the ROM the port runs. The evidence for each one is in
// mods\quickmatch\mod.psd1, where the same addresses were found and checked (aki-roadmap#227 reuses them).
//
// This file is CC0 1.0 (public domain): copy it into your own conversion and change it freely.

#include "aki_mod.h"
#include "recomp_api.h"

#define RAM_U8(addr)  (*(volatile u8*)(addr))
#define RAM_S32(addr) (*(volatile s32*)(addr))

// Intro (ovl_b), called once per frame while the logos and the intro movie play.
#define INTRO_FUNC       "func_80106AB0"
#define INTRO_SCENE      0x800A872Cu  // s32: a scene transition is pending (0 = none)
#define INTRO_SKIPPABLE  0x8015C8E4u  // s32: nonzero once Start may skip the intro
#define GAME_MODE        0x800457A0u  // s32: writing 1 leaves the intro (what Start does)

// Title screen (ovl_c), called once per frame while the title / main menu is up.
#define TITLE_FUNC   "func_80111158"
// s32: frames the title has been idle. The game counts it up from 0 and starts the attract demo when it reaches
// TITLE_IDLE_LIMIT; -1 = not counting (a menu is open).
#define TITLE_IDLE   0x80158150u
#define TITLE_IDLE_LIMIT 900
// Wait this many frames instead (30 frames = 1 second).
#define STARTER_IDLE_FRAMES 150
// u8: what the attract sequence plays next: odd = the intro movie, even = a demo match of set counter / 2
// (0 = tag team, 2 = singles, ...; the game steps it after each one).
#define ATTRACT_COUNTER  0x800457AAu
#define STARTER_DEMO_SET 0

// Demo setup (ovl_a): picks the demo match's wrestlers and venue, then returns.
#define DEMO_SETUP_FUNC "func_800EF478"
// u8: venue id the demo match loads (0 = Raw, 1 = No Mercy, 2 = SmackDown, ...; mods\quickmatch\mod.psd1).
#define DEMO_VENUE   0x800A10F1u
#define STARTER_VENUE 0

RECOMP_HOOK(INTRO_FUNC) void starter_on_intro(void) {
    if (RAM_S32(GAME_MODE) == 0 && RAM_S32(INTRO_SCENE) == 0 && RAM_S32(INTRO_SKIPPABLE) != 0) {
        RAM_S32(GAME_MODE) = 1;
    }
}

RECOMP_HOOK(TITLE_FUNC) void starter_on_title(void) {
    RAM_U8(ATTRACT_COUNTER) = STARTER_DEMO_SET * 2;
    s32 idle = RAM_S32(TITLE_IDLE);
    // Only move a running count forward, and never past the game's own limit: the game still starts the demo.
    if (idle >= 0 && idle < TITLE_IDLE_LIMIT - STARTER_IDLE_FRAMES) {
        RAM_S32(TITLE_IDLE) = TITLE_IDLE_LIMIT - STARTER_IDLE_FRAMES;
    }
}

RECOMP_HOOK_RETURN(DEMO_SETUP_FUNC) void starter_after_demo_setup(void) {
    RAM_U8(DEMO_VENUE) = STARTER_VENUE;
}
