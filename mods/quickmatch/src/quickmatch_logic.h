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

#endif
