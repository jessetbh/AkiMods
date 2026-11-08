# Cheats (`aki_cheats`)

A GameShark-style cheat engine with curated, per-game cheats. In the Mods menu each game
gets:

- **toggles** for the cheats below (all off by default), and
- a **Codes** box for any other GameShark code, separated by spaces.

Both are re-read about twice a second and applied every frame. Supported code types:
`80`/`A0` (8-bit write), `81`/`A1` (16-bit write), `D0`–`D3` (conditionals), `50` (serial
repeater). Master/enabler codes (`F0`, `F1`, `F6`, `FF`, `EE`, `DE`, `2A`, `3C`) and button
activators (`88`/`89`) are ignored. A conditional governs the next code together with anything that
code governs (a `50` and its write, or another conditional), so stacked conditionals must all hold. Writes outside the 8 MB
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

## Codes files (full conversions)

A conversion package (AKI Launcher, `conversion.json`) can carry a codes file: `components.codes.file`. When the
game runs with `AKI_CONVERSION=<id>`, it reads `<app folder>/conversions/<id>/<file>` and hands the text to this mod
(host export `aki_conversion_codes`), which applies it every frame, on top of the toggles and the Codes box. Only
games built with `AKI_HOST_CONVERSION` (`mod.psd1` defines; No Mercy so far) import that export, because a mod
that imports a function the game lacks doesn't load. The result goes to the game's log as one
`[conversion] codes: N active, ...` line (`aki_conversion_log`).

Format: UTF-8 text (a BOM is fine), LF or CRLF, one code per line.

```
# rom: nomercy-rev0              the revision tag (optional; the first "# rom:" line counts)
# anything after # // or ;       comments
[Unlock everything]              a cheat's name (or any line that isn't a code)
50000B02 0000                    a code: 8 hex digits, a separator (space, tab, ':' or '-'), 4 hex digits
810BEE40:FFFF  # note            ... optionally followed by a note or comment
8109940AFFFF                     ... or 12 digits run together
```

- **Tag**: `<game>` or `<game>-rev<N>` with the AkiMods game key (`nomercy`, `wm2k`, ...). Another game, an
  unknown revision (`nomercy-unknown`, `nomercy-rev2`), or anything unparsable after the key: the whole file is
  refused (nothing applied, logged). No tag: the port's revision is assumed (No Mercy Rev 1, the rest Rev 0).
- **No Mercy Rev 0 lists** (`# rom: nomercy-rev0`) are translated to Rev 1 address by address through
  `rev_translate()` in `codes_engine.h`. The default map has the one verified range (main data,
  `0x80040000`-`0x800D9960`, -`0x1530`); a fuller map drops in as a generated header
  (`-DREV_MAP_HEADER=\"file.h\"` defining `rev0_to_rev1[]` and `REV0_MAP_COUNT`). A code with no mapping is
  dropped together with the conditional/repeater that governs it and the code it governs, and counted in the log.
- A line starting with 8 hex digits that isn't a valid code (`8109940A FFF`) is counted as malformed in the log;
  other text lines are names.
- Up to 4096 codes in all; the file may be up to 1 MB.

Project64 cheat files (`.cht`, both the 1.6/2.x `Cheat0="Name",...` layout and the 3.x `$Name` layout) convert with
`tools/cht2codes.py`: the section header's CRC pair becomes the `# rom:` tag (kept as a `# source:` comment too),
cheat names become `[Name]` headers, notes become comments, and `????` option codes are left commented out with
their choices listed (or `--first-option`). `--list`, `--select <regex>`, `--game`, `--rev` as needed.
