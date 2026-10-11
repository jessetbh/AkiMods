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
            Venues  = @(
                @{ Name = 'Venue 0'; Id = 0 }
                @{ Name = 'Venue 1'; Id = 1 }
                @{ Name = 'Venue 2'; Id = 2 }
                @{ Name = 'Venue 3'; Id = 3 }
                @{ Name = 'Venue 4'; Id = 4 }
                @{ Name = 'Venue 5'; Id = 5 }
                @{ Name = 'Venue 6'; Id = 6 }
                @{ Name = 'Venue 7'; Id = 7 }
                @{ Name = 'Venue 8'; Id = 8 }
                @{ Name = 'Venue 9'; Id = 9 }
                @{ Name = 'Venue 10'; Id = 10 }
                @{ Name = 'Venue 11'; Id = 11 }
                @{ Name = 'Venue 12'; Id = 12 }
                @{ Name = 'Venue 13'; Id = 13 }
                @{ Name = 'Venue 14'; Id = 14 }
                @{ Name = 'Venue 15'; Id = 15 }
                @{ Name = 'Venue 16'; Id = 16 }
                @{ Name = 'Venue 17'; Id = 17 }
                @{ Name = 'Venue 18'; Id = 18 }
                @{ Name = 'Venue 19'; Id = 19 }
                @{ Name = 'Venue 20'; Id = 20 }
                @{ Name = 'Venue 21'; Id = 21 }
                @{ Name = 'Venue 22'; Id = 22 }
                @{ Name = 'Venue 23'; Id = 23 }
                @{ Name = 'Venue 24'; Id = 24 }
            )
            Matches = @('Set 1', 'Set 2', 'Set 3', 'Set 4', 'Set 5', 'Set 6', 'Set 7', 'Set 8', 'Set 9')
        }
    }
}
