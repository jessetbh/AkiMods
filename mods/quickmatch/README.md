# Quick Match (`aki_quickmatch`)

A **developer/testing** mod: with **Enabled** on, the game skips its intro and title wait and starts its own
attract-mode demo match (CPU vs CPU, no HUD, no controller input) within seconds, in the venue you pick. Off
by default, so it does nothing for players until switched on. Bundled with No Mercy (`stage.ps1`), so it is in
every No Mercy install's Mods menu.

Options (Mods menu, or `mod_config\aki_quickmatch.json`):

| Option | Values |
|---|---|
| Enabled | off (default) / on |
| Venue | Game's pick, or one of the game's venues (list per game below) |
| Match | Game's rotation, or one of the game's demo match sets |
| Demo length | No time limit (default), 1 minute (the game's demo length), 2 / 3 / 5 / 10 / 30 minutes |

A pinfall or submission still ends the match; the next demo then starts at once, in the same venue.

The quickest way to use it is AkiTools: `aki-run.ps1 -Game nomercy -Worktree <wt> -Venue Raw -Ring` copies the
built mod into the checkout, enables it for that run only, and puts it back afterwards (AkiTools README).

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

## Option 2: a real exhibition match

Not built. Notes in this section once investigated.
