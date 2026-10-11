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

#endif
