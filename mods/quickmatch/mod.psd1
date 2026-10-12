# Build settings for the quickmatch mod, read by ..\..\build.ps1 (and generate.ps1). See
# ..\cheats\mod.psd1 for the common fields.
#
# PerGame:
#   Defines   addresses and hooked functions (README.md has the evidence for each)
#   Venues    the "Venue" option list: @{ Name; Id } with Id = the game's venue id
#   Matches   the "Match" option list: one name per demo category, in category order
@{
    Id      = 'aki_quickmatch'
    Sources = @('src\quickmatch.c')
    Games   = @('nomercy', 'wm2k')
    # Bundled with No Mercy (owner, 2026-10-11): off by default, so it does nothing until enabled.
    Values  = @{}
    PerGame = @{
        # WWF No Mercy (USA, Rev 1)
        nomercy = @{
            Defines = @(
                '-DQM_INTRO_FUNC=func_80106AB0',     # ovl_b: intro (logos + in-engine intro movie), per frame
                '-DQM_INTRO_SCENE=0x800A872Cu',      # s32: pending transition step (0 = none)
                '-DQM_INTRO_SKIPPABLE=0x8015C8E4u',  # s32: nonzero once Start may skip the intro
                '-DQM_MODE=0x800457A0u',             # s32: 1 = leave the intro (what Start does)
                '-DQM_TITLE_FUNC=func_80111158',     # ovl_c: title/menu state machine, per frame
                '-DQM_IDLE_TIMER=0x80158150u',       # s32: title idle frames; -1 = not counting
                '-DQM_IDLE_LIMIT=900',               # attract starts at 0x384 frames
                '-DQM_ATTRACT_COUNTER=0x800457AAu',  # u8: odd = intro next, even = demo (category = /2)
                '-DQM_DEMO_SETUP_FUNC=func_800EF478', # ovl_a: picks the demo match
                '-DQM_VENUE=0x800A10F1u',            # u8: venue id the match loads (func_800D9990)
                '-DQM_MATCH_INIT_FUNC=func_80113E30', # ovl_e: match init; sets the demo's time limit
                '-DQM_TIME_LIMIT=0x8009FE6Cu',       # s8: time limit index (0 = none), read by the clock
                '-DQM_DEMO_FLAGS=0x80097EC0u',       # u32: match flags; the clock ends demos on its limit
                '-DQM_DEMO_FLAG=0x02000000u',        #   bit set for attract demos
                '-DQM_DEMO_MODE=-6',                 # QM_MODE value while the demo match runs
                # Entrance stages (10-19) are only reached through an exit mid-match; started directly,
                # the ring's collision stays and wrestlers float above the stage. So: start in the
                # stage's arena (id - 10), then request the game's area change like the exit handler
                # func_800EDCF4 does (0x80159670 = target, func_800DA2F0: 0x80151EF4 = target,
                # 0x80151EF8 = 1), once the opening fade-in is over. The game's own demos start in
                # backstage areas (20-24) directly.
                '-DQM_STAGED_FIRST=10', '-DQM_STAGED_END=20', '-DQM_STAGED_PARENT=10',
                '-DQM_AREA_REQUEST=0x80151EF8u',     # s16, cleared by the match driver func_800D9990
                '-DQM_AREA_TARGET=0x80151EF4u',      # s32: venue the driver loads (func_800E1980)
                '-DQM_AREA_NEXT=0x80159670u',        # s32: match init(1) makes it the current area 0x80159628
                '-DQM_MATCH_FRAMES=0x801532C0u',     # u16: match frames (func_800DE58C), 0xFFFF before
                '-DQM_MATCH_FADING=0x80151ED0u'      # s8: opening fade-in running (func_800D9990)
            )
            # Time limit indexes (minutes table at 0x80146574 in ovl_e: 0 = none, 1=5, 2=10, 3=15,
            # 4=30, 5=60, 6=3, 7=1, 8=2); the game gives demos index 7.
            Lengths = @(
                @{ Name = 'No time limit'; Id = 0 }
                @{ Name = '1 minute (game)'; Id = 7 }
                @{ Name = '2 minutes'; Id = 8 }
                @{ Name = '3 minutes'; Id = 6 }
                @{ Name = '5 minutes'; Id = 1 }
                @{ Name = '10 minutes'; Id = 2 }
                @{ Name = '30 minutes'; Id = 4 }
            )
            # Venue ids (func_800EE3C4): 0-9 ring arenas, 10-19 the same arenas' entrance stages (id + 10),
            # 20-24 backstage. Identified 2026-10-11 by forcing each id (RT venue log + screenshots; stage
            # 17/18/19 show the WrestleMania / Backlash / Armageddon logos). Arenas 3, 4, 6: names unconfirmed.
            Venues  = @(
                @{ Name = 'Raw'; Id = 0 }
                @{ Name = 'No Mercy'; Id = 1 }
                @{ Name = 'SmackDown'; Id = 2 }
                @{ Name = 'Arena 3'; Id = 3 }
                @{ Name = 'Arena 4'; Id = 4 }
                @{ Name = 'Survivor Series'; Id = 5 }
                @{ Name = 'Arena 6'; Id = 6 }
                @{ Name = 'WrestleMania'; Id = 7 }
                @{ Name = 'Backlash'; Id = 8 }
                @{ Name = 'Armageddon'; Id = 9 }
                @{ Name = 'Raw stage'; Id = 10 }
                @{ Name = 'No Mercy stage'; Id = 11 }
                @{ Name = 'SmackDown stage'; Id = 12 }
                @{ Name = 'Arena 3 stage'; Id = 13 }
                @{ Name = 'Arena 4 stage'; Id = 14 }
                @{ Name = 'Survivor Series stage'; Id = 15 }
                @{ Name = 'Arena 6 stage'; Id = 16 }
                @{ Name = 'WrestleMania stage'; Id = 17 }
                @{ Name = 'Backlash stage'; Id = 18 }
                @{ Name = 'Armageddon stage'; Id = 19 }
                @{ Name = 'Backstage hallway'; Id = 20 }
                @{ Name = 'Backstage bar'; Id = 21 }
                @{ Name = 'Parking garage'; Id = 22 }
                @{ Name = 'Boiler room'; Id = 23 }
                @{ Name = 'Locker room'; Id = 24 }
            )
            # Demo sets in category order (func_800EF478: table 0x80102998, ranges 0x80102AE4; match flags
            # 0x800A957C per set: 0x42, 1, 0x11, 1, 0x48, 0x20200, 2, 4, 8). Named from screenshots.
            Matches = @('Tag team', 'Singles A', 'Singles B', 'Singles C', 'Three wrestlers A', 'Royal Rumble',
                'Four wrestlers', 'Triple threat', 'Three wrestlers B')
        }

        # WWF WrestleMania 2000 (USA, V1.2). Its attract sequence has no demo match (in-engine intro
        # movie, then the title), so the mod drives the menus to Exhibition > <match> > <venue> > Watch
        # (CPU vs CPU) itself: README.md, "How it works (WrestleMania 2000)".
        wm2k = @{
            Defines = @(
                '-DQM_INTRO_FUNC=func_8011F078',     # ovl_b: intro main, per frame (takes A/Start)
                '-DQM_INTRO_SCENE=0x8011BC84u',      # s32: transition script running (0 = none)
                '-DQM_INTRO_SKIPPABLE=0x8012A2A4u',  # s32: nonzero once Start may skip the intro
                '-DQM_MODE=0x8003DD00u',             # s32: 1 = leave (fades out; -1 at the end)
                '-DQM_PAD_FUNC=func_800E24D4',       # ovl_a: controllers -> menu button words, per frame
                '-DQM_PAD_HELD=0x8011C37Cu',         # u16 button words the menus read
                '-DQM_PAD_PRESSED=0x8011C37Eu',
                '-DQM_PAD_REPEAT=0x8011C380u',
                '-DQM_MENU_SIG=0x801255E4u',         # first word of ovl_c's menu state machine:
                '-DQM_MENU_SIG_WORD=0x3C038004u',    #   lui $v1, 0x8004 (menus loaded)
                '-DQM_MENU_SCREEN=0x8003DD04u',      # s16: menu screen id
                '-DQM_MENU_SCRIPT=0x8011BC84u',      # s32: transition/prompt script (48 = the title's Rumble Pak prompt)
                '-DQM_MENU_SCREENS=0x8016ECA0u',     # screen records, 0x138 bytes each (ovl_c func_8011EB10):
                '-DQM_MENU_STRIDE=0x138',            #   +5 cursor
                '-DQM_MATCH_SCREEN=5',               # Exhibition screen: cursor = match type
                '-DQM_PER_MATCH=2,5,3,2',            # "Watch" item per match type (single, tag, 3 way, cage)
                # Screens: 0 title, 1 main menu (0 = Exhibition), 5 Exhibition (match type), 3 venue,
                # 4 players (Watch, per match type), 10 belt, 19/20 wrestlers, 17 rules, 18 VS, 22 results (0 = Rematch). A = 0x8000, Start = 0x1000.
                # Entrances play on menu screen 34 (ovl_a + ovl_c still loaded; ovl_d loads at the bell); Start skips them.
                # After the finish the match code (ovl_d) loops a replay until a button: A ends it (in Watch mode A does
                # nothing during play; Start would pause). Then the results screen 22: Rematch.
                '-DQM_PAD_READ_FUNC=func_80004628',  # per-pad records at 0x80095180, 0xC each: +4 held, +6 pressed
                '-DQM_PAD0_PRESSED=0x80095186u',
                '-DQM_MATCH_SIG=0x800E1BC0u',        # ovl_d's match loop: addiu $sp, $sp, -0x40 (ovl_a has
                '-DQM_MATCH_SIG_WORD=0x27BDFFC0u',   #   0x00002821 there)
                '-DQM_MATCH_BUTTONS=0x8000',
                '-DQM_ENTRANCE_SCREEN=34',
                '-DQM_ENTRANCE_BUTTONS=0x1000',
                '-DQM_MENU_STEPS={0,-1,0x1000},{1,-1,0x8000},{5,-3,0x8000},{3,-2,0x8000},{4,-4,0x8000},{10,-1,0x8000},{19,-1,0x8000},{20,-1,0x8000},{17,-1,0x8000},{18,-1,0x8000},{22,0,0x8000}'
            )
            # No Lengths: the match keeps the game's own rules (time limit 30 minutes unless changed on
            # the rules screen; a pinfall or submission ends it). No "Demo length" option for this game.
            Text = @{
                SHORT_DESCRIPTION   = 'Developer tool: boot straight into a CPU-vs-CPU exhibition match in a chosen venue.'
                DESCRIPTION         = "A testing tool for {{GAME_TITLE}}: with Enabled on, the game skips its intro and walks its own menus to an Exhibition match in Watch mode (CPU vs CPU), in the venue you pick, within about 15 seconds of starting. The match keeps the game's rules (time limit, entrances); when it ends, the next one starts the same way.`n`nOff by default. Turn it off again for normal play: while it's on, the menus press their own buttons."
                ENABLED_DESCRIPTION = 'Skip the intro and menus, and start a CPU-vs-CPU match with the settings below.'
                VENUE_DESCRIPTION   = 'Where the match takes place.'
                MATCH_DESCRIPTION   = 'Exhibition match type.'
            }
            # Venue = the venue screen's cursor (0x8016F04D), stored by the game at 0x8009E99B.
            Venues  = @(
                @{ Name = 'Raw is War'; Id = 0 }
                @{ Name = 'Sunday Night Heat'; Id = 1 }
                @{ Name = 'Royal Rumble'; Id = 2 }
                @{ Name = 'King of the Ring'; Id = 3 }
                @{ Name = 'Survivor Series'; Id = 4 }
                @{ Name = 'WrestleMania'; Id = 5 }
                @{ Name = 'SummerSlam'; Id = 6 }
            )
            EntranceOption = $true
            # Exhibition match types, in the menu's order (cursor = option - 1).
            Matches = @('Single Match', 'Tag Match', '3 Way Match', 'Cage Match')
        }
    }
}
