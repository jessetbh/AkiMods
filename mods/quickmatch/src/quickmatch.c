// AKI Quick Match: a developer/testing mod that starts the attract demo match right away,
// in the configured venue, and keeps it running.
//
// Off unless "Enabled" is set in the Mods menu (or in mods.json by AkiTools\aki-run.ps1
// -Venue/-Match). Per-game addresses and the hooked functions are listed in README.md.

#include "aki_mod.h"
#include "recomp_api.h"
#include "quickmatch_logic.h"
#include "quickmatch_game.h" // generated: QM_VENUE_IDS, QM_CATEGORIES

#define RDRAM_U8(addr)  (*(volatile u8*)(addr))
#define RDRAM_S32(addr) (*(volatile s32*)(addr))
#define RDRAM_U32(addr) (*(volatile u32*)(addr))

// Per game (mod.psd1 PerGame.Defines):
//   QM_INTRO_FUNC        intro (logos + intro movie) main, per frame
//   QM_INTRO_SCENE       s32 pending transition step; the intro only takes Start when 0
//   QM_INTRO_SKIPPABLE   s32 nonzero once the intro may be skipped
//   QM_MODE              s32 the intro sets 1 on Start, then fades out to the title
//   QM_TITLE_FUNC       title/attract state machine (runs every frame on the title screen)
//   QM_IDLE_TIMER        s32 frame counter on the title; negative = not counting
//   QM_IDLE_LIMIT        count at which the attract sequence starts
//   QM_ATTRACT_COUNTER   u8: odd = intro movie next, even = demo match (category = value / 2)
//   QM_DEMO_SETUP_FUNC   picks the demo match (wrestlers, match type, venue)
//   QM_VENUE             u8 venue id the match loads
//   QM_STAGED_FIRST / QM_STAGED_END / QM_STAGED_PARENT   venue ids [first, end) are reached through
//                        the area change: start in id - PARENT, then move (optional; see quickmatch_logic.h)
//   QM_AREA_REQUEST      s16 area change request (the match driver handles it, then clears it)
//   QM_AREA_TARGET       s32 venue the area change loads
//   QM_AREA_NEXT         s32 venue the match init records as current after an area change
//   QM_MATCH_FRAMES      u16 match frame counter (0xFFFF before the match runs)
//   QM_MATCH_FADING      s8 nonzero while the match's opening fade-in runs
//   QM_DEMO_MODE         QM_MODE value while the demo match runs
//   QM_MATCH_INIT_FUNC   match init; gives demos their time limit
//   QM_TIME_LIMIT        s8 time limit index the match clock checks (0 = none)
//   QM_DEMO_FLAGS / QM_DEMO_FLAG   u32 match flags and the bit set for attract demos
//   (the title, demo setup and match init hooks are each optional: QM_TITLE_FUNC, QM_DEMO_SETUP_FUNC,
//   QM_MATCH_INIT_FUNC)
//
// Menu-driven games (no attract demo match; see qm_menu_step in quickmatch_logic.h):
//   QM_PAD_FUNC          reads the controllers into the menus' button words, once per frame
//   QM_PAD_HELD / QM_PAD_PRESSED / QM_PAD_REPEAT   u16 button words the menus read (N64 button bits)
//   QM_MENU_SIG / QM_MENU_SIG_WORD   u32 at QM_MENU_SIG equals QM_MENU_SIG_WORD while the menus are loaded
//   QM_MENU_SCREEN       s16 current menu screen
//   QM_MENU_SCRIPT       s32 screen transition/prompt script (some prompts wait for a press while it runs)
//   QM_MENU_SCREENS / QM_MENU_STRIDE   screen n's record at QM_MENU_SCREENS + n * stride: u32 +0 = its
//                        item list (u8 item count at +5), u8 +5 = its cursor
//   QM_MENU_STEPS        { screen, cursor, buttons } list

static const u8 venue_ids[] = { QM_VENUE_IDS };
#ifdef QM_LENGTH_IDS
static const u8 length_ids[] = { QM_LENGTH_IDS };
#endif

#ifndef QM_STAGED_FIRST
#define QM_STAGED_FIRST 0
#define QM_STAGED_END 0
#define QM_STAGED_PARENT 0
#endif
#define QM_AREA_DELAY 45 // match frames before the area change (1.5 s at 30 fps), after the fade-in

#ifdef QM_DEMO_SETUP_FUNC
static int pending_area = -1; // venue the area change still has to move the demo to
#endif

static int enabled(void) {
    return recomp_get_config_u32("enabled") != 0;
}

// Intro: do what pressing Start does, as soon as the intro accepts it.
RECOMP_HOOK(AKI_STRINGIFY(QM_INTRO_FUNC)) void qm_on_intro(void) {
    if (!enabled()) {
        return;
    }
    if (RDRAM_S32(QM_MODE) == 0 && RDRAM_S32(QM_INTRO_SCENE) == 0 && RDRAM_S32(QM_INTRO_SKIPPABLE) != 0) {
        RDRAM_S32(QM_MODE) = 1;
    }
}

#ifdef QM_TITLE_FUNC
RECOMP_HOOK(AKI_STRINGIFY(QM_TITLE_FUNC)) void qm_on_title(void) {
    if (!enabled()) {
        return;
    }
    s32 timer = RDRAM_S32(QM_IDLE_TIMER);
    if (timer < 0) {
        return;
    }
    RDRAM_S32(QM_IDLE_TIMER) = qm_idle_timer(timer, QM_IDLE_LIMIT);
    RDRAM_U8(QM_ATTRACT_COUNTER) = (u8)qm_attract_counter(RDRAM_U8(QM_ATTRACT_COUNTER),
        (int)recomp_get_config_u32("match"), QM_CATEGORIES);
}
#endif

#ifdef QM_DEMO_SETUP_FUNC
RECOMP_HOOK_RETURN(AKI_STRINGIFY(QM_DEMO_SETUP_FUNC)) void qm_after_demo_setup(void) {
    if (!enabled()) {
        return;
    }
    int id = qm_venue_id((int)recomp_get_config_u32("venue"), venue_ids, (int)sizeof(venue_ids));
    pending_area = -1;
    if (id >= 0) {
        int start = qm_start_venue(id, QM_STAGED_FIRST, QM_STAGED_END, QM_STAGED_PARENT);
        RDRAM_U8(QM_VENUE) = (u8)start;
        if (start != id) {
            pending_area = id;
        }
    }
}
#endif

#ifdef QM_AREA_REQUEST
// Moves a staged venue's demo from its parent arena, the way an exit does (func_800EDCF4 in No Mercy).
AKI_ON_FRAME void qm_on_frame(void) {
    if (pending_area < 0) {
        return;
    }
    if (!enabled()) {
        pending_area = -1;
        return;
    }
    if (RDRAM_S32(QM_MODE) != QM_DEMO_MODE) { // demo not running yet (each demo setup resets pending_area)
        return;
    }
    int frames = *(volatile u16*)QM_MATCH_FRAMES;
    // Busy while the match's opening fade-in runs: an area change requested during it never finishes
    // (stuck on a black screen).
    int busy = *(volatile s16*)QM_AREA_REQUEST != 0 || *(volatile s8*)QM_MATCH_FADING != 0;
    if (qm_area_change_due(pending_area, frames, QM_AREA_DELAY, busy)) {
        RDRAM_S32(QM_AREA_NEXT) = pending_area;
        RDRAM_S32(QM_AREA_TARGET) = pending_area;
        *(volatile s16*)QM_AREA_REQUEST = 1;
        pending_area = -1;
    }
}
#endif

#ifdef QM_MATCH_INIT_FUNC
// Demo length: the match init gives demos a short time limit; replace it (demos only).
RECOMP_HOOK_RETURN(AKI_STRINGIFY(QM_MATCH_INIT_FUNC)) void qm_after_match_init(void) {
    if (!enabled() || (*(volatile u32*)QM_DEMO_FLAGS & QM_DEMO_FLAG) == 0) {
        return;
    }
    int index = qm_length_index((int)recomp_get_config_u32("length"), length_ids, (int)sizeof(length_ids));
    if (index >= 0) {
        *(volatile s8*)QM_TIME_LIMIT = (s8)index;
    }
}
#endif

#ifdef QM_MENU_SCREEN
#define QM_MENU_SETTLE 20 // frames a screen must be up (no transition) before the first press
#define QM_MENU_REPEAT 30 // frames between presses on the same screen

static const qm_menu_step menu_steps[] = { QM_MENU_STEPS };
static int menu_screen = -1;
static int menu_script = -1;
static int menu_idle = 0;
static int menu_since_press = 0;

// After the game reads the controllers: on each known menu screen, set its cursor and press the
// step's buttons for one frame (the button words are rebuilt from the controllers every frame).
RECOMP_HOOK_RETURN(AKI_STRINGIFY(QM_PAD_FUNC)) void qm_after_pad(void) {
    if (!enabled() || *(volatile u32*)QM_MENU_SIG != QM_MENU_SIG_WORD) {
        menu_screen = -1;
        return;
    }
    int screen = *(volatile s16*)QM_MENU_SCREEN;
    int script = RDRAM_S32(QM_MENU_SCRIPT);
    int busy = RDRAM_S32(QM_MODE) != 0; // leaving the menus
    // A screen counts as settled once neither it nor its transition/prompt script has changed for a while.
    if (screen != menu_screen || script != menu_script || busy) {
        menu_screen = screen;
        menu_script = script;
        menu_idle = 0;
    } else {
        menu_idle++;
    }
    menu_since_press++;
    const qm_menu_step* step = qm_menu_find(menu_steps, (int)(sizeof(menu_steps) / sizeof(menu_steps[0])), screen);
    if (step == 0 || busy) {
        return;
    }
    if (qm_menu_press_due(menu_idle, menu_since_press, QM_MENU_SETTLE, QM_MENU_REPEAT)) {
        // The cursor is set only together with the press: writing it while a screen slides in breaks
        // the screen's own animation (WM2000's main menu lost its highlight).
        u32 record = QM_MENU_SCREENS + (u32)screen * QM_MENU_STRIDE;
        u32 list = RDRAM_U32(record);
        int items = (list >= 0x80000000u && list < 0x80800000u) ? RDRAM_U8(list + 5) : 0;
        int venue = qm_venue_id((int)recomp_get_config_u32("venue"), venue_ids, (int)sizeof(venue_ids));
        int option = (int)recomp_get_config_u32("match");
        int match = (option > 0 && option <= QM_CATEGORIES) ? option - 1 : -1;
        int cursor = qm_menu_cursor(step, venue, match, items);
        if (cursor >= 0) {
            RDRAM_U8(record + 5) = (u8)cursor;
        }
        *(volatile u16*)QM_PAD_HELD |= step->buttons;
        *(volatile u16*)QM_PAD_PRESSED |= step->buttons;
        *(volatile u16*)QM_PAD_REPEAT |= step->buttons;
        menu_since_press = 0;
    }
}
#endif
