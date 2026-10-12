#ifndef __QUICKMATCH_LOGIC_H__
#define __QUICKMATCH_LOGIC_H__

// Pure decisions for the quick-match mod, shared by the mod and its host tests (tests/).
// No game memory access here: the mod reads and writes RDRAM, these functions decide.

// Title-screen idle timer: the game counts frames up from 0 and starts the attract
// sequence when the count reaches `limit`. Negative = not counting (not on the title).
// Returns the value to store so the attract sequence starts on the next tick.
static inline int qm_idle_timer(int timer, int limit) {
    if (timer < 0 || timer >= limit - 1) {
        return timer;
    }
    return limit - 1;
}

// Attract sequence counter: odd values play the intro movie, even values a demo match
// whose category is counter / 2 (0 .. categories - 1). Returns the counter to store
// before the game reads it: the configured category (option 1 = category 0; 0 = keep
// the game's own rotation, but still skip the intro).
static inline int qm_attract_counter(int counter, int option, int categories) {
    if (option > 0 && option <= categories) {
        return (option - 1) * 2;
    }
    if (counter & 1) {
        counter++;
    }
    if (counter >= categories * 2) {
        counter = 0;
    }
    return counter;
}

// Venue option (0 = the game's pick, n = the n-th entry of `ids`) -> the venue id to
// store, or -1 to leave the game's pick alone.
static inline int qm_venue_id(int option, const unsigned char* ids, int count) {
    if (option <= 0 || option > count) {
        return -1;
    }
    return ids[option - 1];
}

// Demo length option (n = the n-th entry of `ids`, no "game's pick" entry) -> the time limit
// index to store, or -1 for an out-of-range option (leave the game's limit alone).
static inline int qm_length_index(int option, const unsigned char* ids, int count) {
    if (option < 0 || option >= count) {
        return -1;
    }
    return ids[option];
}

// Venues the game only reaches mid-match, through an exit (No Mercy's entrance stages): starting a
// match there leaves the ring's collision in place, so wrestlers fight in the air above the stage.
// Instead the match starts in the venue's parent arena and the game's own area change moves it there.
// Returns the venue to start in for a chosen id (the id itself unless first_staged <= id < last_staged).
static inline int qm_start_venue(int id, int first_staged, int last_staged, int parent_offset) {
    if (id >= first_staged && id < last_staged) {
        return id - parent_offset;
    }
    return id;
}

// Whether to request the area change now: once, after the match has run `delay` frames, while no
// other area change is pending.
static inline int qm_area_change_due(int pending_id, int match_frames, int delay, int request_busy) {
    return pending_id >= 0 && match_frames >= delay && match_frames < 0x8000 && !request_busy;
}

// Menu-driven quick match, for games whose attract sequence has no demo match (WrestleMania 2000): the
// mod walks the game's own menus to a CPU-vs-CPU exhibition ("Watch"), one button press per known screen.
// A step says what to do on one menu screen: which value to put in the screen's cursor, then which
// buttons to press. Unknown screens are left alone.
#define QM_CURSOR_KEEP (-1)  // leave the cursor where the game has it
#define QM_CURSOR_VENUE (-2) // the configured venue (or keep, for "Game's pick")
#define QM_CURSOR_MATCH (-3) // the configured match type (or keep, for "Game's rotation")
#define QM_CURSOR_LAST (-4)  // the screen's last item (WM2000's "Watch": 2 for singles, 5 for tag)

typedef struct {
    short screen;
    short cursor;
    unsigned short buttons;
} qm_menu_step;

// The step for a screen, or 0 when the mod has nothing to do there.
static inline const qm_menu_step* qm_menu_find(const qm_menu_step* steps, int count, int screen) {
    for (int i = 0; i < count; i++) {
        if (steps[i].screen == screen) {
            return &steps[i];
        }
    }
    return 0;
}

// Cursor value to store for a step, or -1 to leave it. venue / match are the cursor values chosen in
// the options (-1 = the game's own choice); items = the number of items on the screen (0 = unknown).
static inline int qm_menu_cursor(const qm_menu_step* step, int venue, int match, int items) {
    if (step->cursor == QM_CURSOR_VENUE) {
        return venue;
    }
    if (step->cursor == QM_CURSOR_MATCH) {
        return match;
    }
    if (step->cursor == QM_CURSOR_LAST) {
        return items > 0 ? items - 1 : -1;
    }
    return step->cursor;
}

// Whether to press the step's buttons this frame. idle_frames: frames the screen has been up with no
// transition running; since_press: frames since the mod last pressed. The first press waits `settle`
// frames (screens ignore input while they slide in), later ones repeat every `repeat` frames in case
// the screen missed one.
static inline int qm_menu_press_due(int idle_frames, int since_press, int settle, int repeat) {
    return idle_frames >= settle && since_press >= repeat;
}

#endif
