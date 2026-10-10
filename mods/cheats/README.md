# Cheats (`aki_cheats`)

A GameShark-style cheat engine with curated, per-game cheats. In the Mods menu each game
gets:

- **toggles** for the cheats below (all off by default), and
- a **Codes** box for any other GameShark code, separated by spaces.

Both are re-read about twice a second and applied every frame. Supported code types:
`80`/`A0` (8-bit write), `81`/`A1` (16-bit write), `D0`–`D3` (conditionals), `50` (serial
repeater). Master/enabler codes and button activators are ignored. Writes outside the 8 MB
of game RAM are refused. Turning a cheat off doesn't undo it: the game keeps whatever was
written, and may save it.

```powershell
..\..\build.ps1 cheats          # from the repo root: .\build.ps1 cheats
.\tests\run.ps1                 # host-side parser/translation tests
```

Toggles live in `mod.psd1` (`PerGame.<game>.Presets`); `generate.ps1` turns them into
`cheat_presets.h` and Bool options at build time.

## Default cheats and why

A cheat is included only if it is (1) published for the exact ROM revision the recomp uses,
(2) confirmed against the recompiled code (game code reads/writes that address, or it sits
in a player struct the code builds right next to it), and (3) broadly useful. Left out:
per-player variants beyond P1, button activators (no GameShark button), novelty/glitch
codes (giant/invisible wrestlers etc.), weapon spawns, and anything unverified.

| Game (ROM) | Cheat | Code | Evidence |
|---|---|---|---|
| World Tour (USA v1.0) | Unlock hidden wrestlers | `80060665 00FF` | 3 direct refs |
| | Unlock all belts + WWW League | `80060669 00FF 8106066A FFFF` | 4 + 3 direct refs |
| | P1 max spirit | `800F0801 0064` | P1 struct at `0x800F07F0` |
| | Freeze match clock | `800F16EF 0000` | 8 direct refs |
| Revenge (USA) | Unlock extra characters + modes | `8107F07C FFFF` | 11 direct refs |
| | Win a title in one match | `80038AAB 0008` | 22 direct refs |
| | P1 max spirit | `800F9D25 0064` | P1 struct (special flag at `+0x10` has a direct ref) |
| | P1 always has a special | `800F9D35 0008` | direct ref |
| | Freeze match clock | `800FAF87 0000` | 7 direct refs |
| WrestleMania 2000 (USA) | Unlock all characters | `8109ED5A FFFF` | 23 direct refs |
| | Infinite Create-a-Wrestler points | `8011A81B 001E` | 5 direct refs |
| | P1 max spirit | `801671F5 00FF` | P1 struct at `0x801671E0` |
| | P1 always has a special | `80167235 0004` | direct ref |
| | Freeze match clock | `81166F8A 0000` | direct ref |
| VPW64 (Japan) | P1 max spirit | `800F2D49 0064` | P1 struct at `0x800F2D38` (same layout as WT) |
| | Freeze match clock | `800F3CEF 0000` | 6 direct refs |
| No Mercy (USA Rev 1) | Unlock everything | `50000B02 0000 810BD910 FFFF` | tested in-game; table read by `func_800D9A5C` |
| | Max Championship money | `81097EDA FFFF` | 11 direct refs; Rev 0 `8109940A` shifted `-0x1530` |
| VPW2 (Japan) | none | | no published codes found |

Not included, but worth revisiting:

- **VPW64 / VPW2 unlocks**: no published codes, and matching WT's / No Mercy's unlock code
  against them found no reliable equivalent. Best route: diff RAM before/after an unlock
  in-game.
- **No Mercy in-match codes** (spirit, specials; Rev 0 `0x8015xxxx`): the "in a match"
  byte they test (`0x8015A5D1`) has no refs as-is in Rev 1 but 4 refs at `-0x1530`, so the
  shift likely extends there too. Needs an in-game test before shipping.
- **WM2000 "Godfather's HOS"**: no references at all; skipped.

Sources: almarsguides.com GameShark lists (WT v1.0, Revenge, WM2000, No Mercy),
gamegenie.com (VPW64).

## No Mercy: Rev 0 codes in the Codes box

The recomp runs the Rev 1 cartridge; published codes are almost all Rev 0. With Code
Version set to Rev 0, codes in the main data area (`0x80040000`–`0x800D9960`) are shifted
by `-0x1530` (verified on the unlock table and Championship money). Codes outside it, such
as Smackdown Mall money (`0x8014FAD6`), are skipped, along with any repeater or conditional
that governs them.
