# Build settings for this mod, read by ..\..\build.ps1 (and generate.ps1).
#   Id        mod id; also the .nrm file name
#   Sources   C files, relative to this folder
#   Games     'all', or a list of game keys from common\games.psd1
#   Values    {{KEY}} values for mod.template.toml (any key left unset renders empty)
#   PerGame   per-game overrides: Defines (extra compiler flags), Values, and Presets
#
# Presets become on/off options in the Mods menu (all default off). Each one must be:
#   - published for the exact ROM revision the recomp uses (or translated and verified),
#   - checked against the recompiled code (the address is actually read/written there),
#   - broadly useful: unlocks, money/points, P1 helpers, match clock. No per-player
#     variants beyond P1, no button activators, no graphics-glitch novelty codes.
# Sources and evidence: README.md.
@{
    Id      = 'aki_cheats'
    Sources = @('src\cheats.c')
    Games   = 'all'
    Values  = @{}
    PerGame = @{
        # WCW vs. nWo World Tour (USA, v1.0)
        wt      = @{ Presets = @(
                @{ Id = 'unlock_wrestlers'; Name = 'Unlock hidden wrestlers'; Description = 'All six hidden wrestlers.'; Codes = '80060665 00FF' }
                @{ Id = 'unlock_belts'; Name = 'Unlock all belts + WWW League'; Description = 'All title belts and the WWW League Challenge.'; Codes = '80060669 00FF 8106066A FFFF' }
                @{ Id = 'p1_max_spirit'; Name = 'P1 max spirit'; Description = 'Player 1 always has full spirit.'; Codes = '800F0801 0064' }
                @{ Id = 'freeze_clock'; Name = 'Freeze match clock'; Description = 'The match clock stays at 00:00, so timed matches never run out.'; Codes = '800F16EF 0000' }
            )
        }
        # WCW/nWo Revenge (USA)
        revenge = @{ Presets = @(
                @{ Id = 'unlock_extras'; Name = 'Unlock extra characters + modes'; Description = 'All hidden wrestlers (including Wrath) and bonus modes.'; Codes = '8107F07C FFFF' }
                @{ Id = 'title_in_one'; Name = 'Win a title in one match'; Description = 'Title runs end after a single win.'; Codes = '80038AAB 0008' }
                @{ Id = 'p1_max_spirit'; Name = 'P1 max spirit'; Description = 'Player 1 always has full spirit.'; Codes = '800F9D25 0064' }
                @{ Id = 'p1_always_special'; Name = 'P1 always has a special'; Description = 'Player 1 can always do a special move.'; Codes = '800F9D35 0008' }
                @{ Id = 'freeze_clock'; Name = 'Freeze match clock'; Description = 'The match clock stays at 00:00, so timed matches never run out.'; Codes = '800FAF87 0000' }
            )
        }
        # WWF WrestleMania 2000 (USA)
        wm2k    = @{ Presets = @(
                @{ Id = 'unlock_characters'; Name = 'Unlock all characters'; Description = 'Every hidden wrestler.'; Codes = '8109ED5A FFFF' }
                @{ Id = 'caw_points'; Name = 'Infinite Create-a-Wrestler points'; Description = 'Attribute points never run out in Create-a-Wrestler.'; Codes = '8011A81B 001E' }
                @{ Id = 'p1_max_spirit'; Name = 'P1 max spirit'; Description = 'Player 1 always has full spirit.'; Codes = '801671F5 00FF' }
                @{ Id = 'p1_always_special'; Name = 'P1 always has a special'; Description = 'Player 1 can always do a special move.'; Codes = '80167235 0004' }
                @{ Id = 'freeze_clock'; Name = 'Freeze match clock'; Description = 'The match clock stays at 00:00, so timed matches never run out.'; Codes = '81166F8A 0000' }
            )
        }
        # Virtual Pro Wrestling 64 (Japan). No published unlock code exists.
        vpw64   = @{ Presets = @(
                @{ Id = 'p1_max_spirit'; Name = 'P1 max spirit'; Description = 'Player 1 always has full spirit.'; Codes = '800F2D49 0064' }
                @{ Id = 'freeze_clock'; Name = 'Freeze match clock'; Description = 'The match clock stays at 00:00, so timed matches never run out.'; Codes = '800F3CEF 0000' }
            )
        }
        # WWF No Mercy (USA, Rev 1). Presets use Rev 1 addresses; the Codes box can still
        # translate pasted Rev 0 codes.
        nomercy = @{
            # AKI_HOST_CONVERSION: the game exports aki_conversion_codes/aki_conversion_log (conversion codes files).
            # Only for games whose main.cpp has the export: importing a missing one stops the mod loading.
            Defines = @('-DCHEAT_REV0_TRANSLATION', '-DAKI_HOST_CONVERSION')
            Presets = @(
                @{ Id = 'unlock_everything'; Name = 'Unlock everything'; Description = 'All characters, costumes and moves.'; Codes = '50000B02 0000 810BD910 FFFF' }
                @{ Id = 'max_championship_money'; Name = 'Max Championship money'; Description = 'Championship mode money stays maxed out.'; Codes = '81097EDA FFFF' }
            )
            Values  = @{
                EXTRA_OPTIONS           = @'

[[manifest.config_options]]
id = "code_version"
name = "Code Version"
description = "Which cartridge revision the codes in the Codes box were written for. Published No Mercy codes are almost all Rev 0. (The toggles above are already Rev 1.)"
type = "Enum"
options = [ "Rev 1 (this ROM)", "Rev 0 (published codes)" ]
default = "Rev 0 (published codes)"
'@
                DESCRIPTION_EXTRA       = '\n\nThis recomp runs the Rev 1 cartridge. Most published No Mercy codes are for Rev 0: set Code Version to \"Rev 0 (published codes)\" and codes in the Codes box are translated automatically. Codes outside the main game data area can''t be translated safely and are skipped.'
            }
        }
        # Virtual Pro Wrestling 2 (Japan): no published codes found; Codes box only.
        vpw2    = @{}
    }
}
