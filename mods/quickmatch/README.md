# Quick Match (`aki_quickmatch`)

A **developer/testing** mod: with **Enabled** on, the game skips its intro and title wait and starts a CPU-vs-CPU
match within seconds, in the venue you pick. Off by default, so it does nothing for players until switched on.
Bundled with No Mercy, WrestleMania 2000, Virtual Pro Wrestling 2, WCW/nWo Revenge, WCW vs. nWo World Tour and Virtual Pro Wrestling 64 (`stage.ps1`), so it is in every install's Mods menu.

How it gets there depends on the game:

- **Attract demo** (No Mercy, VPW2): the game's own attract-mode demo match (no HUD, no controller input), started
  right away, with its venue and length replaced.
- **Menu-driven** (WrestleMania 2000, Revenge, World Tour and VPW64, whose attract sequences have no demo match): the mod walks the game's own menus,
  pressing one button per known screen, to an Exhibition match in **Watch** mode (CPU vs CPU). The match keeps the
  game's rules and entrances, with its HUD.

Options (Mods menu, or `mod_config\aki_quickmatch.json`):

| Option | Values |
|---|---|
| Enabled | off (default) / on |
| Venue | Game's pick, or one of the game's venues (list per game below) |
| Match | Game's rotation, or one of the game's demo match sets (menu-driven games: match types) |
| Demo length | No time limit (default), 1 minute (the game's demo length), 2 / 3 / 5 / 10 / 30 minutes (attract-demo games only) |
| Entrances | Skip (default) / Show (menu-driven games: WrestleMania 2000, Revenge) |

A pinfall or submission still ends the match; the next demo then starts at once, in the same venue.

To use it, turn on **Enabled** in the game's Mods menu (Settings → Mods → Quick Match), pick the options, and
restart the game. Turn it off again for normal play: while it's on, the menus press their own buttons. (The
maintainers drive it from a test-run script that sets these options for one run only.)

```powershell
..\..\build.ps1 quickmatch nomercy   # from the repo root: .\build.ps1 quickmatch nomercy
.\tests\run.ps1                      # host-side logic tests
```

Per-game addresses, venue lists and demo lengths live in `mod.psd1`; `generate.ps1` turns them into the
manifest's option lists and `quickmatch_game.h`. Pure decisions are in `src/quickmatch_logic.h` (tested);
`src/quickmatch.c` only reads and writes game memory.

## How it works (No Mercy)

| Step | Hook | What the mod does |
|---|---|---|
| Intro (ovl_b) | `RECOMP_HOOK func_80106AB0` (intro main, every frame) | What Start does: once the intro accepts Start (`0x8015C8E4` != 0, no transition pending at `0x800A872C`), set mode `0x800457A0` = 1; the intro fades out to the title |
| Title (ovl_c) | `RECOMP_HOOK func_80111158` (title/menu state machine) | Idle timer `0x80158150` (900 frames to the attract sequence) set to 899; attract counter `0x800457AA` kept even (demo, not the intro movie), or set to the chosen match set x 2 |
| Demo setup (ovl_a) | `RECOMP_HOOK_RETURN func_800EF478` | Overwrites the venue byte `0x800A10F1` it just picked (for an entrance stage: the stage's arena, see below) |
| Every frame | `AKI_ON_FRAME` | Entrance stages only: once the match's opening fade-in is over (`0x80151ED0` == 0, 45 match frames in), requests the game's area change to the stage the way an exit does: `0x80159670` = stage, `0x80151EF4` = stage, `0x80151EF8` = 1 |
| Match init (ovl_e) | `RECOMP_HOOK_RETURN func_80113E30` | For demos only (`0x80097EC0` & `0x02000000`): replaces the time limit index `0x8009FE6C` (demos get 7 = 1 minute; 0 = none) |

None of these functions has a hand-edit (`tools/readd_hand_edits.py`) or a post-recompile repair
(`RecompiledFuncs/repaired_functions.txt`) in NoMercyRecomp, so recompiling them from the ROM for the hooks is
safe. Rev 1 addresses (the recomp's revision).

### No Mercy venues

Venue ids from `func_800EE3C4`: 0-9 are ring arenas, 10-19 are the same arenas' entrance stages (arena id +
10), 20-24 are backstage areas. Every id 0-24 was forced and loads a working demo match (2026-10-11).

The game only reaches the entrance stages mid-match, through an exit (`func_800EDCF4`). Forced as the starting
venue, a stage loads with the ring's collision still in place, so the wrestlers fight in mid-air at ring height.
So the mod starts the match in the stage's arena and, once the opening fade-in is over, requests the game's own
area change (an area change requested during the fade-in leaves the screen black). All ten stages verified on the
floor afterwards. Backstage areas (20-24) need none of this: the game's own demo table starts matches there.

| Id | Option | Evidence |
|---|---|---|
| 0 | Raw | RT log "venue: Raw" |
| 1 | No Mercy | RT log; the stock attract demo's own pick |
| 2 | SmackDown | RT log "venue: SmackDown" (blue ropes) |
| 3 | Arena 3 | name not confirmed |
| 4 | Arena 4 | name not confirmed |
| 5 | Survivor Series | RT log "venue: Survivor Series" |
| 6 | Arena 6 | name not confirmed |
| 7 | WrestleMania | "WRESTLEMANIA" apron; stage 17 shows the WrestleMania logo |
| 8 | Backlash | stage 18 shows the Backlash logo |
| 9 | Armageddon | RT log "venue: Armageddon"; stage 19 shows the Armageddon logo |
| 10-19 | `<arena> stage` | entrance stage of arena id - 10 (starts in the arena, moves to the stage ~1.5 s in) |
| 20 | Backstage hallway | RT log "venue: Backstage hallway" |
| 21-24 | Backstage bar, Parking garage, Boiler room, Locker room | screenshots |

### No Mercy match sets

The demo table (`0x80102998`, 10-byte records: four wrestler ids + venue/flags) is grouped into 9 sets by
`0x80102AE4`; the game plays set `counter / 2` and picks a random record within it.

| Set | Option | Records | Seen (2026-10-11 screenshots) |
|---|---|---|---|
| 0 | Tag team | 0-2 | 4 wrestlers, tag |
| 1 | Singles A | 3-10 | 2 wrestlers |
| 2 | Singles B | 11-15 | 2 wrestlers |
| 3 | Singles C | 16 | 2 wrestlers |
| 4 | Three wrestlers A | 17 | 3 wrestlers, one in a referee shirt |
| 5 | Royal Rumble | 18 | entrant counter on screen (starts at 036) |
| 6 | Four wrestlers | 19-26 | 4 wrestlers in the ring at once |
| 7 | Triple threat | 27-29 | 3 wrestlers |
| 8 | Three wrestlers B | 30-32 | 3 wrestlers |

The exact rules per set (handicap, special referee, ...) aren't decoded; the names only describe what is seen.

### Verified (No Mercy, NoMercyRecomp-qm, aki-run `-Preset capture`)

- Demo match in the ring ~4 s after launch with the mod (stock: intro, title and idle wait, ~172 s).
- Forced venues load and are recognised by the renderer's venue log (Raw, SmackDown, Survivor Series,
  Armageddon, Backstage hallway).
- Demo length: no time limit keeps the demo running past the stock 1-minute end.

## How it works (WrestleMania 2000)

WM2000's attract sequence is an in-engine intro movie (randomized cuts through the arenas) and the title screen, then
the movie again: no CPU match anywhere. So the mod drives the menus, the way a player would, to
Exhibition > *match type* > *venue* > **Watch** > Non Title Match > (wrestlers) > rules > VS screen.

| Step | Hook | What the mod does |
|---|---|---|
| Intro (ovl_b) | `RECOMP_HOOK func_8011F078` (intro main, every frame) | What Start does: once the intro accepts Start (`0x8012A2A4` != 0, no script running at `0x8011BC84`), set mode `0x8003DD00` = 1 |
| Menus (ovl_a + ovl_c) | `RECOMP_HOOK_RETURN func_800E24D4` (copies the controllers into the menus' button words `0x8011C37C/7E/80`, every frame, before the menus run) | On each known screen (`0x8003DD04`), once neither the screen nor its script (`0x8011BC84`) has changed for 20 frames: set the screen's cursor and press A (Start on the title) for one frame; again every 30 frames until the screen changes |

The menus only run while ovl_c is loaded: the mod checks the first word of ovl_c's state machine (`0x801255E4` =
`lui $v1, 0x8004`) before touching anything. Screen records are 0x138 bytes at `0x8016ECA0 + screen * 0x138`, with the
screen's cursor (u8) at +5.

| Screen | What it is | The mod |
|---|---|---|
| 0 | Title ("Rumble Pak supported" prompt = script 48) | Start |
| 1 | Main menu (cursor 0 = Exhibition) | A, cursor left alone (writing it while the menu slides in freezes its animation) |
| 5 | Exhibition: Single / Tag / 3 Way / Cage | the Match option's type, A |
| 3 | Venue | the Venue option's venue, A |
| 4 | Players | Watch (item 2 singles and cage, 5 tag, 3 three-way), A |
| 10 | Belt | A (Non Title Match) |
| 19, 20 | Wrestlers (the same two screens repeat for tag and three-way) | A (the highlighted wrestler) |
| 17 | Rules | A (Decision; the game's saved rules, 30 minutes by default) |
| 18 | VS | A |
| 34 | Entrances (still ovl_a + ovl_c; ovl_d loads at the bell) | Start, which skips both entrances, unless **Entrances** = Show |
| (match) | ovl_d's match loop `func_800E1BC0` (checked by its first word, `0x27BDFFC0`) | `RECOMP_HOOK_RETURN func_80004628` (reads the controllers into the per-pad records `0x80095180`): A on controller 1 every 2 s. In Watch mode A does nothing during play; after the finish it ends the replay loop |
| 22 | Results (Rematch / Quit) | A on Rematch (cursor 0): the same match again, so a test run keeps going |

**Entrances** option (WM2000): Skip (default; the bell rings ~20 s after launch) or Show (both entrances on the
stage and ramp first; the bell at ~115 s). Start during the match proper would open the pause menu, but by then ovl_c
is gone and the mod does nothing.

The match runs as screen 34. None of the three hooked functions (`func_8011F078`, `func_800E24D4`, `func_80004628`)
has a hand-edit or a post-recompile repair in Wm2kRecomp (checked by diffing a fresh N64Recomp output
against the fixed `RecompiledFuncs`: 28 functions differ, none of them hooked). WM2000 V1.2 (USA) addresses.

No match length option: the rules screen keeps the game's own time limit (its popup list has no stable byte the mod
could set), and a pinfall or submission ends the match as usual.

### WrestleMania 2000 venues

The venue screen's cursor is the venue id (`0x8016F04D`, stored by the game at `0x8009E99B`, read by ovl_a's match
setup). Each venue's arena includes its entrance stage (wrestlers walk down the ramp in the same scene), so there are
no separate stage ids. No backstage areas.

| Id | Option | Evidence |
|---|---|---|
| 0 | Raw is War | venue screen logo; match screenshots |
| 1 | Sunday Night Heat | venue screen logo; match screenshots |
| 2 | Royal Rumble | venue screen logo; match screenshots |
| 3 | King of the Ring | venue screen logo; match screenshots |
| 4 | Survivor Series | venue screen logo; match screenshots |
| 5 | WrestleMania | venue screen logo; match screenshots |
| 6 | SummerSlam | venue screen logo; match screenshots |

## How it works (Virtual Pro Wrestling 2)

VPW2 is No Mercy's engine: the same attract sequence (intro movie, title, 900-frame idle wait, attract counter with odd =
intro movie and even = demo match) and the same pieces, at its own addresses (Japanese ROM NA2J):

| Step | Hook | What the mod does |
|---|---|---|
| Intro (ovl_b) | `RECOMP_HOOK func_8011B1F8` | What Start does: once `0x8012C504` != 0 and no transition is pending (`0x800A33B4` == 0), mode `0x8003ED30` = 1 |
| Title (ovl_c) | `RECOMP_HOOK func_80124810` | Idle timer `0x801763F0` to 899; attract counter `0x8003ED40` kept even (or the chosen demo set) |
| Demo setup (ovl_c) | `RECOMP_HOOK_RETURN func_8014C4A0` | Overwrites the demo record's venue `0x800B5D14` |

No hooked function has a post-recompile repair in Vpw2Recomp (fresh N64Recomp output vs the fixed `RecompiledFuncs`:
15 functions differ, none hooked). The demo's 1-minute limit was not located (VPW2 has no `0x02000000` demo-flag time
limit like No Mercy's), so there is no Demo length option: when a demo ends, the next one starts at once in the same
venue. Mode is -6 while a demo runs; demo set = the even attract counter passed straight to the demo setup (sets at
`0x80171084`).

### VPW2 venues

The exhibition venue screen lists 武道館, ドーム, AKIホール, 球場 (Budokan, Dome, AKI Hall, Ballpark); the venue ids are in
that order. Each id was forced and surveyed with the free camera (2026-10-12).

| Id | Option | Evidence |
|---|---|---|
| 0 | Budokan | octagonal hall, gold ceiling lamp grid (scene ~4300 wide) |
| 1 | Dome | huge hall under a fabric roof, "'99 ALL JAPAN WRESTLING" banners |
| 2 | AKI Hall | small hall, low ceiling (~690), wall lamps, "東" sign; also what invalid ids fall back to |
| 3 | Ballpark | open-air at night: stars, city skyline, scene ~11000 wide |
| 4 | Dome (entrance) | the Dome plus an entrance ramp and truss light towers at the ring corners; name not confirmed |

Ids 5 and 6 load a bare ring with no arena, 7 and up fall back to AKI Hall or never start: not offered.

## How it works (Revenge)

The same menu driver as WrestleMania 2000. Revenge keeps the intro, title and menus in one overlay (ovl_a) with one
screen variable, so there is no separate intro hook: screen 0 is both the intro and the title, and Start leaves either.

| Piece | Revenge |
|---|---|
| Pad hook | `RECOMP_HOOK_RETURN func_800C5718` (button words `0x800EA0C0/C2/C4`; also controller 1's record `0x8006E17C/E`, which the VS screen reads) |
| Menus loaded | first word of `func_800AC144` = `0x27BDFFA0` (the match overlay ovl_b sits at the same vram) |
| Screen / script / mode | `0x80038A90` (s16) / `0x800E7C90` / `0x80038A8C` (-2 during the match) |
| Screen records | `0x800E8230 + screen * 0x138`, cursor at +5 (not on the rules screen 11) |

| Screen | What it is | The mod |
|---|---|---|
| 0 | Intro and title (Rumble Pak prompt = script 44) | Start |
| 1 | Main menu | cursor 1 (Exhibition), A |
| 5 | Exhibition: Single / Tag | the Match option's type, A |
| 2 | Venue | the Venue option's venue, A |
| 3 | Players (single: 1P vs 2P / 1P vs CPU / CPU vs CPU; tag: six, CPU vs CPU last) | CPU vs CPU (2 single, 5 tag), A |
| 13, 14 | Wrestlers | A |
| 12 | VS | Start |
| 11 | Rules | A (Decision) |
| 23 | Entrances (mode 0), then the match keeps screen 23 with mode -2 | Start skips the entrances unless **Entrances** = Show |

Neither hooked function has a repair (fresh N64Recomp output vs `RecompiledFuncs`: only `func_80001C28` and
`func_80002CC4` differ). USA addresses.

### Revenge venues

The venue screen's cursor, in its order; forced 2026-10-12 (ring aprons, set logos). Each arena has its own entrance
set, drawn in the entrance scene before the match (Entrances = Show).

| Id | Option | Evidence |
|---|---|---|
| 0 | Monday Nitro | NITRO apron, Nitro set |
| 1 | Souled Out | Souled Out banners |
| 2 | SuperBrawl | red carpet entrance |
| 3 | Bash at the Beach | beach hut set, BASH apron |
| 4 | Halloween Havoc | HALLOWEEN HAVOC mat |
| 5 | Starrcade | STARRCADE apron and set |

## How it works (World Tour)

The Revenge driver with two additions, both generic: a screen that takes a **sequence** of presses, and a venue
written straight into the rules.

| Piece | World Tour |
|---|---|
| Pad hook | `RECOMP_HOOK_RETURN func_800C383C` (button words `0x800E630C/0E/10`; controller 1's record `0x80047B24/26`) |
| Menus loaded | first word of `func_800A9910` = `0x3C04800E` (ovl_b has `0x3C04800F` there) |
| Screen / mode | `0x80034824` (s16) / `0x80034820` |
| Screen records | `0x800DD430 + screen * 0x138`, cursor at +5 |

| Screen | What it is | The mod |
|---|---|---|
| 0 | Intro and title (the intro ignores Start for ~30 s) | Start |
| 1 | Mode select (1 = Exhibition) | cursor 1, A |
| 9 | Exhibition: Single / Tag / Handicap / Battle Royal | Single only, A |
| 10 | Corners: player or CPU each | sequence: down (red to CPU), right, down (blue to CPU), A |
| 23, 24 | Wrestlers | A |
| 17 | Rules | the **Arena** setting at `0x8038311C` (+1, +2; fixed address), then A |
| 22 | Handicap | A |
| 35 | Match | (nothing) |

World Tour has **one arena in four federation themes** (the rules screen's Arena: WCW, NWO, DOA, IU; 4 = RANDOM, the
game's pick). The Venue option picks the theme. A fresh save without a Controller Pak note asks to create one before
the title; answer it once (the mod doesn't).

No repaired functions (fresh N64Recomp output = `RecompiledFuncs`); WT's own patches replace none of the hooks.

| Id | Option | Evidence |
|---|---|---|
| 0 | WCW | blue WCW mat, white light panels on the floor |
| 1 | NWO | black nWo mat, blue floor panels |
| 2 | DOA | teal Dead or Alive mat, floodlight banks |
| 3 | IU | blue Independent Union mat, wooden gym floor |

## How it works (Virtual Pro Wrestling 64)

World Tour's engine and menus (same screen ids and steps: corners as a press sequence, venue written on the rules
screen before A), at VPW64's addresses (Japanese ROM NVPJ):

| Piece | VPW64 |
|---|---|
| Pad hook | `RECOMP_HOOK_RETURN func_800C1F50` (button words `0x800EF6BC/BE/C0`; controller 1's record `0x800592F4/6`) |
| Menus loaded | first word of `func_800A432C` = `0x240EFFFF` (ovl_b has `0x3C07800F` there) |
| Screen / mode | `0x80040BA4` (s16) / `0x80040BA0` |
| Screen records | `0x800DF840 + screen * 0x138`, cursor at +5 |
| Venue | rules (会場) u8 at `0x803191DC` (+1, +2): 0 = random, 1-7 below |

Two prompts before the title (no Controller Pak note, Rumble Pak) take Start/A on screen 0. Unlike World Tour, VPW64
waits on its results after a finish: the mod presses A on screen 25 (results), then picks Rematch on screen 26 (cursor
10; 9 is End) and the same match starts again through screen 22. Only `func_8000209C` and
`func_80003358` differ between a fresh N64Recomp output and `RecompiledFuncs`; neither is hooked.

| Id | Option | Evidence |
|---|---|---|
| 1 | NSW | rules venue cycle + match screenshots |
| 2 | EWF | rules venue cycle + match screenshots |
| 3 | WOU | rules venue cycle + match screenshots |
| 4 | DAW | rules venue cycle + match screenshots |
| 5 | ILW | rules venue cycle + match screenshots |
| 6 | WCW | rules venue cycle + match screenshots |
| 7 | NWO | rules venue cycle + match screenshots |

## Option 2: a real exhibition match

Not built. Notes in this section once investigated.
