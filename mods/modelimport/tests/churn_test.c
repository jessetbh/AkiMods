// Churn test for pack_alloc.h (aki-roadmap#247): the No Mercy intro and a Rumble swap wrestlers in and out of the 4
// slots for minutes, with more packs than the window holds. This replays the core's policy (core.c find_pack /
// load_pack / evict_pack / wait_slot: the file is read into the largest gap, unloading the least recently shown pack
// no slot shows until it fits, then moved to the smallest gap that holds it; a pack drawn in the last MI_IN_FLIGHT
// frames is never touched; a slot with no room yet tries again after MI_RETRY frames) over thousands of scene cuts:
//   - no pack is placed over a loaded pack (so never over one the frames in flight may still draw);
//   - loads - unloads == the packs resident, every frame (no leak), and they never hold more than the window;
//   - after a cut, a slot shows its pack within MI_IN_FLIGHT + MI_RETRY frames, except when the packs on screen split
//     the window so that no gap holds a big pack (rare: counted, and best fit must keep it rarer than largest-gap
//     placement did).
// Pack sizes: a 32-pack conversion's model.bin sizes (bytes only), 25 KB to 854 KB, 7.1 MB together against the
// 3.5 MB window. Skin buffers aren't modelled (they share the window the same way, first fit). Build and run:
// tests/run.ps1
#include <stdio.h>
#include "../src/pack_alloc.h"

#define MI_RETRY (MI_IN_FLIGHT + 1)   // as core.c
#define BOUND (MI_IN_FLIGHT + MI_RETRY)
#define SLOTS 4
#define NPACKS 32

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static const unsigned int lo = 0x80400000u, hi = 0x80400000u + 0x387C00u;   // No Mercy's window (mod.psd1)
static const unsigned int sizes[NPACKS] = {
    112888, 405188, 129356, 63792, 120636, 136880, 121200, 71696, 493160, 854060, 756092, 69848, 45300,
    341532, 53152, 25568, 33504, 62268, 93040, 185296, 138752, 210324, 73276, 92276, 55216, 489968,
    285908, 449868, 605892, 137664, 159636, 133648,
};

static int loaded[NPACKS], shown[NPACKS];
static unsigned int base[NPACKS], last_use[NPACKS];
static unsigned int now;
static int best_fit_on = 1;

typedef struct { long appearances, loads, unloads, waits, long_waits, stock_frames; unsigned int worst_short; } Stats;
static Stats st;

static unsigned int rng = 1;
static unsigned int rnd(unsigned int n) { rng = rng * 1103515245u + 12345u; return (rng >> 16) % n; }

static int used(MiRange* out) {
    int n = 0;
    for (int i = 0; i < NPACKS; i++)
        if (loaded[i]) out[n].base = base[i], out[n++].size = sizes[i];
    return n;
}

// core.c load_pack: 1 loaded (or already), 0 no room yet.
static int load(int p) {
    if (loaded[p]) return 1;
    MiRange u[NPACKS];
    for (;;) {
        int n = used(u);
        unsigned int room, b = mi_largest_gap(u, n, lo, hi, &room);
        if (b && sizes[p] <= room) {
            unsigned int fit = best_fit_on ? mi_best_fit(u, n, lo, hi, sizes[p]) : b;
            CHECK(fit != 0);
            b = fit;
            for (int i = 0; i < NPACKS; i++)
                if (loaded[i] && !(b + sizes[p] <= base[i] || base[i] + sizes[i] <= b)) { CHECK(0); return 0; }
            if (b < lo || b + sizes[p] > hi) { CHECK(0); return 0; }
            loaded[p] = 1, base[p] = b, last_use[p] = now, st.loads++;
            return 1;
        }
        int e = mi_pick_evict(loaded, shown, last_use, NPACKS, -1, now);   // core.c evict_pack
        if (e < 0) return 0;
        loaded[e] = 0, st.unloads++;
    }
}

typedef struct { int want, show; unsigned int wait_from, retry; } Slot;

static void waited(unsigned int w) {
    st.stock_frames += w;
    if (w > BOUND) st.long_waits++;
    else if (w > st.worst_short) st.worst_short = w;
}

// Scenes of 1-4 wrestlers, each held min_len..max_len frames; pick() gives each slot's pack (-1: empty).
static void run(int scenes, int min_len, int max_len, void (*pick)(int* want, int scene)) {
    Slot s[SLOTS];
    for (int i = 0; i < SLOTS; i++) s[i].want = s[i].show = -1;
    for (int i = 0; i < NPACKS; i++) loaded[i] = shown[i] = 0;   // a new match: the window starts empty
    const long loads0 = st.loads, unloads0 = st.unloads;
    now = 100;
    for (int scene = 0; scene < scenes; scene++) {
        int want[SLOTS];
        pick(want, scene);
        // The cut: each slot that changes lets go of its pack (last drawn this frame) and decides again, in slot order.
        for (int i = 0; i < SLOTS; i++) {
            if (want[i] == s[i].want) continue;
            if (s[i].want >= 0 && s[i].show < 0) waited(now - s[i].wait_from);   // still stock at the cut
            if (s[i].show >= 0) shown[s[i].show] = 0, last_use[s[i].show] = now;
            s[i].want = want[i], s[i].show = -1, s[i].wait_from = now, s[i].retry = now;
            if (want[i] >= 0) st.appearances++;
        }
        int len = min_len + (int)rnd((unsigned int)(max_len - min_len + 1));
        for (int f = 0; f < len; f++, now++) {
            for (int i = 0; i < SLOTS; i++) {
                if (s[i].want < 0 || s[i].show >= 0 || (int)(now - s[i].retry) < 0) continue;
                if (load(s[i].want)) {
                    waited(now - s[i].wait_from);
                    s[i].show = s[i].want, shown[s[i].want] = 1;
                } else {
                    if (now == s[i].wait_from) st.waits++;
                    s[i].retry = now + MI_RETRY;
                }
            }
            for (int i = 0; i < SLOTS; i++)
                if (s[i].show >= 0) last_use[s[i].show] = now;
            long resident = 0;
            unsigned int bytes = 0;
            for (int i = 0; i < NPACKS; i++) resident += loaded[i], bytes += loaded[i] ? sizes[i] : 0;
            if (resident != (st.loads - loads0) - (st.unloads - unloads0) || bytes > hi - lo) { CHECK(0); return; }
        }
    }
}

// The intro: 1-4 different wrestlers per scene, any of the roster.
static void pick_intro(int* want, int scene) {
    (void)scene;
    int n = 1 + (int)rnd(4);
    for (int i = 0; i < SLOTS; i++) {
        want[i] = -1;
        if (i >= n) continue;
        for (;;) {
            int p = (int)rnd(NPACKS), dup = 0;
            for (int k = 0; k < i; k++) dup |= want[k] == p;
            if (!dup) { want[i] = p; break; }
        }
    }
}

// The worst case: the four biggest packs on screen, then the next four, every cut (4 shown + 4 in flight: 4.4 MB,
// more than the window).
static void pick_big(int* want, int scene) {
    static const int big[8] = { 9, 10, 28, 8, 25, 27, 1, 13 };   // 854, 756, 606, 493, 490, 450, 405, 342 KB
    for (int i = 0; i < SLOTS; i++) want[i] = big[(scene * SLOTS + i) % 8];
}

// A Rumble: one wrestler swapped every cut, the other three stay.
static int cur[SLOTS], next_in;
static void pick_rumble(int* want, int scene) {
    if (scene == 0) { for (int i = 0; i < SLOTS; i++) cur[i] = i; next_in = SLOTS; }
    else cur[rnd(SLOTS)] = next_in, next_in = (next_in + 1) % NPACKS;
    for (int i = 0; i < SLOTS; i++) {
        int dup = 0;
        for (int k = 0; k < i; k++) dup |= cur[k] == cur[i];
        want[i] = dup ? -1 : cur[i];
    }
}

static void report(const char* name) {
    printf("[modelimport churn] %s: %ld appearances, %ld loads, %ld unloads, %ld waits, worst %u frames, %ld waits over %d"
           " frames, %ld stock frames\n", name, st.appearances, st.loads, st.unloads, st.waits, st.worst_short,
           st.long_waits, BOUND, st.stock_frames);
}

static void intro(int best) {
    st = (Stats){ 0 };
    best_fit_on = best;
    for (unsigned int seed = 1; seed <= 20; seed++) {
        rng = seed;
        run(3000, 60, 200, pick_intro);   // 2-7 s scenes at 30 game frames/s
    }
}

int main(void) {
    intro(0);
    report("intro, largest-gap placement (before #247)");
    long before = st.long_waits;
    intro(1);
    report("intro, best fit");
    CHECK(st.unloads > 0 && st.worst_short <= BOUND);
    CHECK(st.long_waits <= st.appearances / 50000);   // ~1 in 150000 appearances (seeds 1-20)
    CHECK(st.long_waits < before);

    st = (Stats){ 0 };
    rng = 7;
    run(2000, 3, 60, pick_big);
    report("biggest packs");
    CHECK(st.waits > 0);                 // the case that has to wait for the frames in flight
    CHECK(st.long_waits == 0 && st.worst_short <= BOUND);

    st = (Stats){ 0 };
    rng = 3;
    run(3000, 30, 300, pick_rumble);
    report("rumble");
    CHECK(st.unloads > 0 && st.worst_short <= BOUND);
    CHECK(st.long_waits <= st.appearances / 1000);   // 1 in 3003 (rng 3): a split window, waits for the next swap

    printf("[modelimport churn tests] %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
