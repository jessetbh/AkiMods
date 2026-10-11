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
    Games   = @('nomercy')
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
                '-DQM_DEMO_FLAG=0x02000000u'         #   bit set for attract demos
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
    }
}
