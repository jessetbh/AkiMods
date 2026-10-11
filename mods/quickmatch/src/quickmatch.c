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
//   QM_MATCH_INIT_FUNC   match init; gives demos their time limit
//   QM_TIME_LIMIT        s8 time limit index the match clock checks (0 = none)
//   QM_DEMO_FLAGS / QM_DEMO_FLAG   u32 match flags and the bit set for attract demos

static const u8 venue_ids[] = { QM_VENUE_IDS };
static const u8 length_ids[] = { QM_LENGTH_IDS };

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

RECOMP_HOOK_RETURN(AKI_STRINGIFY(QM_DEMO_SETUP_FUNC)) void qm_after_demo_setup(void) {
    if (!enabled()) {
        return;
    }
    int id = qm_venue_id((int)recomp_get_config_u32("venue"), venue_ids, (int)sizeof(venue_ids));
    if (id >= 0) {
        RDRAM_U8(QM_VENUE) = (u8)id;
    }
}

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
