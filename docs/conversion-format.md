# Conversion format (aki-conversion v1)

A **conversion** turns one of the AKI recomps into a different game: a ROM hack, a code pack, a texture pack, saves
and models, all run on top of the player's own ROM. This guide covers every file a conversion holds and every field
of its manifest, `aki.json`, so you can write one by hand or check what the tools made.

A conversion never contains ROM data. Patches are applied to the player's own cartridge when the game starts.

Each section ends with a **Source of truth** line that names the code that reads or writes it. Where this guide and
the code disagree, the code is right; the known gaps are listed at the end.

Paths below are in this repository (AkiMods) unless they start with `AkiLauncher/`.

## Contents

- [Folder layout](#folder-layout)
- [The package around it](#the-package-around-it)
- [aki.json, field by field](#akijson-field-by-field)
- [Components](#components)
- [codes.txt (aki-codes)](#codestxt-aki-codes)
- [Textures and naming](#textures-and-naming)
- [Model packs](#model-packs)
- [Tiers](#tiers)
- [How the launcher installs and runs a conversion](#how-the-launcher-installs-and-runs-a-conversion)
- [Testing a conversion](#testing-a-conversion)
- [Known gaps between code and docs](#known-gaps-between-code-and-docs)

## Folder layout

Installed, a conversion is one folder named after its id:

```
<app folder>\conversions\<id>\
    aki.json          the manifest (required)
    patch.nrm         the ROM hack, as a patch mod (optional)
    codes.txt         GameShark codes (optional)
    saves\            save files seeded into saves\<id>\ (optional)
        wwf.nomercy.us.bin
    models\           model packs (optional)
        roster.json
        <pack>.akimodel\
    icon.png          256x256 picture (optional; aki.json "thumb")
    README.md         what it is, credits, permission (optional)
    docs\             anything else the author shipped, e.g. move lists (optional)
```

- `<app folder>` is `%LOCALAPPDATA%\<Game>Recompiled`, or the folder next to the game's exe when it holds
  `portable.txt`.
- The folder name **is** the id: the game finds a conversion by its folder, and the launcher lists it under that
  name whatever `aki.json` says.
- Folders under `conversions\` whose names start with `_` or `.` are not conversions; the launcher skips them.
  `conversions\_packages\` is planned for shared texture packages; today texture packs install to `mods\`.
- A conversion's saves live in `<app folder>\saves\<id>\`, apart from the stock game's saves. Installing,
  upgrading or removing a conversion never touches them.

Source of truth: `AkiLauncher/src/packages.cpp` (`list_conversions`, `install_package`),
`tools/romhack/aki_save.py` (header, `install`).

## The package around it

You hand players a zip. The launcher installs a zip (or folder) that has an `aki-package.json` at its root and the
conversion folder inside it:

```
my-conversion-1.0.0.zip
    aki-package.json
    conversion\
        aki.json
        patch.nrm
        ...
```

`aki-package.json` for a conversion:

```json
{
  "format": 1,
  "kind": "conversion",
  "id": "my-conversion",
  "version": "1.0.0",
  "game_id": "wwfnomercy",
  "display_name": "My Conversion",
  "authors": ["you"],
  "short_description": "One line for the Mods page.",
  "license": "CC0-1.0",
  "base_rom": { "sha1": ["91cee3d096f4a76644d8b35b9aead6448909abd1"] },
  "requires": { "packages": ["my-conversion-textures"] },
  "payload": { "folder": "conversion/" }
}
```

| Field | Rule |
|---|---|
| `format` | The number `1`. |
| `kind` | `conversion`. (The other kinds are `addon`, `texture-pack` and `model-pack`.) |
| `id` | Lower case `a-z 0-9 _ . -`, at most 48 characters, starts with a letter or digit, doesn't end with `.`, not a Windows device name. Must equal `aki.json` `id`. |
| `version` | `x.y.z`, optionally `-suffix`. |
| `game_id` | The game's runtime id: `wcwnwoworldtour`, `wcwnworevenge`, `wwfwm2k`, `vpw64`, `wwfnomercy`, `vpw2`. |
| `display_name`, `authors` | Required. `authors` is a list of at least one name. |
| `base_rom.sha1` | Required for a conversion: a list of SHA-1s of the ROMs it is made for. The launcher refuses to install it on any other ROM. |
| `requires.launcher` | Optional minimum launcher version. |
| `requires.packages` | Packages this one needs, by id (a separate texture pack, for example). Recorded only: the launcher doesn't check or fetch them yet, so ship them side by side. |
| `payload.folder` | The folder inside the zip that becomes `conversions\<id>\`. |
| `payload.addons` | Optional list of `.nrm` / `.rtz` files that go into `mods\` alongside the conversion. |
| `license`, `permission`, `thumb`, `source`, `replaces` | Optional. `permission` here is an object: `redistribute` and `modify`, each `yes`, `no`, `ask` or `unknown`, and a free-text `statement`. |

The launcher refuses a package that holds a `.z64`, `.v64` or `.n64` file, that is over 8 GB, or whose conversion
folder has no `aki.json`.

`tools/romhack/aki_conv.py` (`import-pj64`, `pack-nrm`) also writes an older Thunderstore-shaped zip, with
`manifest.json`, `icon.png`, `README.md` and `aki.json` at its root. The launcher does not install that shape as a
conversion. `aki_import.py build` repacks the `import-pj64` zips it makes itself into the shape above (it drops
`manifest.json` and moves the rest under `conversion/`); a `pack-nrm` zip is not converted, so drop its `.nrm` on
the launcher instead (see [Installing](#how-the-launcher-installs-and-runs-a-conversion)).

Source of truth: `AkiLauncher/src/packages.cpp` (`parse_manifest`, `check_package`),
`tools/romhack/aki_import.py` (`package_manifest`, `write_conversion`).

## aki.json, field by field

A complete example (every component is optional; use only the ones you need):

```json
{
  "format": "aki-conversion",
  "format_version": 1,
  "id": "my-conversion",
  "name": "My Conversion",
  "version": "1.0.0",
  "authors": ["you"],
  "game": "nomercy",
  "base_rom": { "sha1": "91CEE3D096F4A76644D8B35B9AEAD6448909ABD1", "revision": "nomercy-rev1" },
  "description": "What it is, in a sentence or two.",
  "credits": "Who made what.",
  "permission": "Free to share and change.",
  "links": [{ "label": "Home page", "url": "https://example.com" }],
  "thumb": "icon.png",
  "source": { "mode": "byo" },
  "components": {
    "patch":    { "file": "patch.nrm", "mod_id": "my-conversion" },
    "codes":    { "file": "codes.txt", "format": "aki-codes", "rom": "nomercy-rev1" },
    "saves":    [{ "file": "saves/wwf.nomercy.us.bin", "kind": "flash", "mode": "seed" }],
    "textures": { "package": "my-conversion-textures", "version": "1.0.0", "naming": "rice", "optional": false },
    "models":   { "dir": "models", "format": 2, "roster": "models/roster.json",
                  "slots": [{ "wrestler": "0x0404", "name": "Example Wrestler", "costume": 0, "pack": "q-robot" }] }
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `format` | string | Always `aki-conversion`. |
| `format_version` | number | Always `1`. |
| `id` | string | Same rules as the package id. Must equal the folder name and the package id. |
| `name` | string | Display name. The launcher falls back to the folder name. |
| `version` | string | Use `x.y.z`. `aki_import.py` refuses anything else; the launcher only shows this one and checks the `aki-package.json` version. |
| `authors` | list of strings | At least one name. The tools write `["unknown"]` when none is given. |
| `game` | string | The game key: `wt`, `revenge`, `wm2k`, `vpw64`, `nomercy`, `vpw2`. (Note: `aki-package.json` uses the runtime id instead.) |
| `base_rom.sha1` | string, or list of strings | SHA-1 of the ROM (in `.z64` byte order) the conversion runs on. The launcher accepts either form. With no `sha1`, the launcher treats every ROM as a match. |
| `base_rom.revision` | string | The revision tag of that ROM, e.g. `nomercy-rev1`. The ports run `nomercy-rev1`, `wt-rev0`, `revenge-rev0`, `wm2k-rev0`, `vpw64-rev0` and `vpw2-rev0`. |
| `requires.patch` | object | Written by `import-pj64` only, when a mod's patch needs a ROM the player hasn't given yet: `{format: "ninja2-rup", base, source_md5, note}`. Rerun the import with that ROM to replace it with a real `patch` component. |
| `source.mode` | string | `byo` ("bring your own" ROM). It is the only mode. The tools add `imported_from` (`project64-bundle`, `rom` or `patch`) to record what they built from. |
| `permission` | string | Free text: what others may do with your conversion. Shown in the README; no tool checks it. |
| `description`, `credits` | string | Free text. |
| `links` | list | `[{label, url}]`; `url` starts with `http://` or `https://`. |
| `thumb` | string | Path of the picture, usually `icon.png`. |
| `components` | object | What the conversion is made of. See [Components](#components). |
| `survey` | object | Written by the tools: how the conversion was built and how well it was tested. See below. |

**`survey`.** You don't write this by hand; the converters fill it in. Keys you may see:

| Key | Meaning |
|---|---|
| `tier` | How the converter classified the hack: `patch-data` (data only), `patch-code` (changes code in place), `bundled-rom` (built from a ROM inside a mod bundle), `rev0-layout`, `relinked` or `restructured` (built by derelink). |
| `notes` | Free text about the build. |
| `code_bake` | `{writes_on_code, bytes_baked, not_baked, excluded}`: GameShark codes that edit game instructions do nothing as codes, so they are baked into `patch.nrm`; this counts them. |
| `aki_tier`, `aki_tier_label` | The player-facing tier (see [Tiers](#tiers)). |
| `converter`, `engine`, `catalog`, `input_sha1`, `result_sha1`, `output_sha1`, `codes_sha1`, `known_issues`, `note`, `tier_note`, `base` | Build records: which tool built it, from what, what it made, and the catalog entry it matched. |

Source of truth: `tools/romhack/aki_conv.py` (`cmd_import_pj64`, `cmd_pack_nrm`, `cmd_bake_codes`),
`tools/romhack/aki_import.py` (`conversion_manifest`, `write_conversion`), `AkiLauncher/src/packages.cpp`
(`list_conversions`).

## Components

### patch

```json
"patch": { "file": "patch.nrm", "mod_id": "my-conversion" }
```

- `file`: a patch mod (`.nrm` holding `patch.bps` and `mod.json`). A bare `.bps` is refused; wrap it with
  `tools/romhack/make_patch_mod.py` (in-place hacks) or `tools/romhack/derelink.py` (hacks that move or add code).
- `mod_id`: the id inside that `.nrm`'s `mod.json`. **Make it the conversion id.** The launcher refuses a
  conversion whose patch mod id differs, because the game keeps the patch's saves in `saves\<mod id>\`.
- Only one patch mod runs at a time. A `.nrm` in `mods\` with the same id as a conversion stops the conversion
  from loading.

Source of truth: `AkiLauncher/src/packages.cpp` (`check_package`), `tools/romhack/aki_import.py` (`build_hack`,
`nrm_with_id`), `tools/romhack/README.md`.

### codes

```json
"codes": { "file": "codes.txt", "format": "aki-codes", "rom": "nomercy-rev1" }
```

- `file`: the codes file in the conversion folder.
- `format`: always `aki-codes` (see [codes.txt](#codestxt-aki-codes)).
- `rom`: the revision the addresses are for. Make it the port's revision (`base_rom.revision`). Codes for another
  revision need translating first (`aki_conv.py translate-codes`); the bake step refuses them, and No Mercy Rev 0
  lists are the only ones the game can translate itself.

When the game runs the conversion, it reads this file and runs every code in it every frame.

Source of truth: `tools/romhack/aki_conv.py` (`codes_txt`, `add_watch_codes`, `cmd_bake_codes`),
`mods/cheats/src/cheats.c`, `mods/cheats/src/codes_engine.h`.

### saves

```json
"saves": [{ "file": "saves/wwf.nomercy.us.bin", "kind": "flash", "mode": "seed" }]
```

- `file`: a save in the port's own format and name: `<game id>.bin` (cartridge save) or `<game id>.pak.bin`
  (Controller Pak). The game ids are `wcw.nwo.worldtour.us`, `wcw.nwo.revenge.us`, `wwf.wm2k.us`, `vpw.vpw64.jp`,
  `wwf.nomercy.us` and `vpw.vpw2.jp`. Only WrestleMania 2000, No Mercy and VPW2 have a separate `.pak.bin`; World
  Tour keeps its Controller Pak in `<game id>.bin`, and Revenge and VPW64 have no separate pak file.
- `kind`: `flash`, `sram` or `pak` (`eep` exists in the tool, though none of the six games uses it). No Mercy saves
  to flash; Revenge, WrestleMania 2000, VPW64 and VPW2 to SRAM; World Tour has only a Controller Pak.
- `mode`: `seed`, the only mode: the file is copied into `saves\<id>\` once, for a fresh start, and never over a
  player's progress.

To make these from an emulator save (`.fla`, `.sra`, `.mpk`, `.eep`, RetroArch `.srm`), use
`aki_save.py convert <file> --game <key> -o <out>`; it handles byte order and pak splitting.

Source of truth: `tools/romhack/aki_conv.py` (`cmd_import_pj64`, saves part), `tools/romhack/aki_save.py`
(`GAMES`, `KINDS`, `install --if-missing`).

### textures

```json
"textures": { "package": "my-conversion-textures", "version": "1.0.0", "naming": "rice", "optional": false }
```

- `package`: the texture package this conversion uses. Big packs ship as their own package so several conversions
  can share one and players can skip a download they already have.
- `version`: that package's version.
- `sha256`: optional; the SHA-256 of the texture package zip, which pins the exact file. `import-pj64` writes it
  (its zips are byte-for-byte reproducible); `aki_import.py build` drops it and lists the package in
  `aki-package.json` `requires.packages` instead.
- `naming`: how the images are named: `rt64`, `rice` or `jabo` (see [Textures and naming](#textures-and-naming)).
- `optional`: `true` if the conversion plays fine without the pack. The tools always write `false`.

Source of truth: `tools/romhack/aki_conv.py` (`cmd_import_pj64`, textures part), `tools/romhack/aki_import.py`
(Project64 bundle repack).

### RT lights and materials

The ray-traced renderer reads per-venue lights and per-texture surface materials from the game's
`assets/rt_lights.json` and `assets/rt_materials.json`. A component that lets a conversion ship its own (planned
as `components.rt`) is **not read by any tool or by the launcher yet**, so it has no fields to document. Leave it
out for now; this section will grow when the game reads it.

Source of truth: none yet (see [Known gaps](#known-gaps-between-code-and-docs)).

### models

```json
"models": {
  "dir": "models",
  "format": 2,
  "roster": "models/roster.json",
  "slots": [
    { "wrestler": "0x0404", "name": "Example Wrestler", "costume": 0, "pack": "q-robot" },
    { "wrestler": "0x0405", "name": "Another Wrestler", "pack": "q-monk" }
  ]
}
```

- `dir`: the folder in the conversion that holds the model packs (`<pack>.akimodel\`). While the conversion runs,
  the game reads models from here **instead of** `mods\models\`: the player's own packs and `models.txt` don't mix
  in, and the model import mod is turned on for the session.
- `format`: the AKP1 pack version the packs need (see [Model packs](#model-packs)). Games read up to `2`; missing
  means `1`. A newer value turns the models off with an "update the game" message.
- `roster`: optional; a roster file for the conversion's wrestlers:
  `{"format": 1, "game": "nomercy", "wrestlers": [{"id": "0x0404", "name": "...", "costumes": 4}]}`. This is not
  the same shape as the launcher's model-picker rosters (`<game>-roster.json`, `"schema": 1`, `costumes` as a list
  of `{costume, name}`), so don't copy one into the other.
- `slots`: which pack replaces which wrestler. Each entry: `wrestler` (the game's wrestler id, hex), `name` (for
  people), `costume` (optional; leave it out for every costume), `pack` (the pack id). Each entry works like a
  `<pack> wrestler 0xNNNN [costume C]` line in `models.txt`. Without `slots`, the game reads `<dir>\models.txt` as
  it is.
- HD textures for the packs (`<pack>-hd.rtz`) go beside `aki.json`.

The game runs the conversion **without models** (one line in its log, a warning in the launcher) when `format` is
too new, a slot is bad, or the hack itself puts code or data in the RAM the model packs load into
(`0x80400000`-`0x80800000`). Plain patch hacks are fine.

Older game builds ignore this component.

Source of truth: the game runtime's conversion rules (`librecomp/src/conversion_rules.cpp` in N64ModernRuntime),
`mods/modelimport/src/core.c` (packs and `models.txt`).

## codes.txt (aki-codes)

Plain UTF-8 text (a BOM is fine), LF or CRLF line endings, one code per line.

```
# My Conversion: GameShark codes
# rom: nomercy-rev1

[Always full spirit]
# default: on
8109A2B4 0064

[Refill health on one screen]
D10A0000 0001
8109A2B6 00FF  # only while the check above holds
```

(These addresses are made up; don't paste them into a game.)

| Line | Meaning |
|---|---|
| `# My Conversion: ...` | A comment: lines starting with `#`, `//` or `;`. |
| `# rom: nomercy-rev1` | The revision tag. The first `# rom:` line counts, and nothing may follow the tag on that line. |
| `[Always full spirit]` | A name for people: any line that isn't a code. |
| `# default: on` | A comment (see below). |
| `8109A2B4 0064` | A 16-bit write, made every frame. |
| `D10A0000 0001` | A conditional: run the next code only if the 16-bit value at `800A0000` is `0001`. |
| `8109A2B6 00FF  # ...` | The code that conditional governs, with a comment after it. |

- **A code** is 8 hex digits, a separator (space, tab, `:` or `-`), then 4 hex digits; or 12 hex digits run
  together. A note or comment may follow it after whitespace.
- **Code types the engine runs:** `80` / `A0` (8-bit write), `81` / `A1` (16-bit write), `D0` / `D1` (run the next
  code if the 8 / 16-bit value is equal), `D2` / `D3` (if it differs), and `50` (repeat the next write: `50 00NNSS
  IIII` = NN times, address + SS, value + IIII each time). A conditional governs the next code, so stacked
  conditionals must all hold. Addresses must fall inside the 8 MB of RAM.
- **Ignored and counted** in the log: enablers (`F0`, `F1`, `F6`, `FF`, `EE`, `DE`, `2A`, `3C`), button codes
  (`88`, `89`) and any other type. A line that starts like a code but isn't one is counted as malformed.
- **`# rom:`** is `<game>` or `<game>-rev<N>` with the game key. No tag means the port's revision. A tag for
  another game, an unknown revision, or anything after the tag, and the whole file is refused.
- **`# default: on/off`** is written by the tools as a note to people. The engine treats it as a comment and runs
  **every** code in the file, so put only codes that should always be on in a conversion's codes file, and never
  two alternatives for the same thing.
- **`[Name]`** lines are labels for people; the engine ignores them.
- **Who runs it.** A file tagged with the port's own revision (what the tools write) is run by the game itself.
  A No Mercy Rev 0 list (`# rom: nomercy-rev0`) is handed to the cheats mod instead, which translates it to Rev 1
  and runs it; that path allows 4096 codes in all, counted together with the cheat menu's toggles and Codes box.
  The rules above are the cheats mod's; the game's own parser is meant to read the same syntax.
- Codes that write over game instructions do nothing as codes.
  `aki_conv.py bake-codes <folder> --port-rom <port ROM>` moves them into `patch.nrm` (codes.txt itself is left as
  it is).

Source of truth: `mods/cheats/src/codes_engine.h` (`parse_code_file`), `mods/cheats/src/cheats.c` (who runs which
file), `mods/cheats/README.md` ("Codes files"),
`tools/romhack/aki_conv.py` (`codes_txt`), `tools/romhack/aki_conv_bake.py` (`parse_units`).

## Textures and naming

A texture pack is a folder of images with an `rt64.json` at its root; that file is what makes the game open the
folder as a texture pack. Ship it as its own package (`kind: texture-pack`), which the launcher installs as
`mods\<id>.rtz`.

```json
{ "configuration": { "autoPath": "rice", "configurationVersion": 3, "hashVersion": 5 }, "textures": [] }
```

`autoPath` tells the renderer how the images are named:

| Naming | File names look like | Notes |
|---|---|---|
| `rt64` | `0123456789abcdef.png` (16 hex digits) | rt64's own hashes. A pack made in rt64 (`.rtz`, or a folder with its own `rt64.json`) installs as it is. |
| `rice` | `<ROM NAME>#<crc>#<fmt>#<siz>[#<palette crc>]_all.png` | Rice Video / GLideN64 names. The ROM name is the one in the ROM header. |
| `jabo` | 32 hex digits, `.png` | Jabo's Direct3D names. `import-pj64` leaves out other pictures in a Jabo pack (artists' references); `aki_import.py` keeps every image. |

- **GLideN64 caches (`.hts`)** can't be loaded directly. The importer exports their RGBA8 textures to Rice-named
  PNGs (`<ROM NAME>#<crc>#<fmt>#<siz>[#<pal>]_all.png`, or `#$#$` when the cache doesn't record format and size)
  and the pack is then `rice`. Other formats in the cache are skipped.
- The importers guess the naming from the file names, and the two guess differently. `aki_import.py`: a pack with
  an `rt64.json` is `rt64`, one with `.hts` files is exported as above, otherwise the naming with the most matching
  names wins (at least 5 images, or it isn't taken for a texture pack). `import-pj64`: at least half Jabo names
  makes it `jabo`, else at least half Rice names makes it `rice`, else `rt64`. Keep a pack to one naming and both
  agree.
- Use only images you made or have permission to share. Never ship images ripped from a game.

Source of truth: `tools/romhack/aki_conv.py` (`hts_to_rice_pngs`, textures part of `cmd_import_pj64`),
`tools/romhack/aki_import.py` (`texture_naming`, `build_textures`), `AkiLauncher/src/packages.cpp` (texture-pack
checks).

## Model packs

A model pack is a folder named `<pack id>.akimodel` (id: `a-z 0-9 _ -`, at most 31 characters):

| File | What it is |
|---|---|
| `model.bin` | The model: an AKP1 header and its data. The only file the game reads. |
| `pack.json` | For people and the launcher: `schema` (1), `id`, `name`, `author`, `version`, `license`, `source`, `game`, `profile`, `suggested`, `preview`, `notes`. Safe to edit. |
| `manifest.json` | What the build tool did (sizes, textures, warnings). Tool output; don't edit it. |
| `preview.png` | Optional picture. |
| `<pack id>-hd.rtz` | Optional HD textures. |

**AKP1 header** (big-endian, 64 bytes; offsets count from the start of the file):

| Offset | Field |
|---|---|
| 0 | Magic `AKP1` (`0x414B5031`) |
| 4 | u16 header version (1 or 2) |
| 6 | u16 header size (64) |
| 8 | 8-byte profile id, NUL-padded (must match the game, e.g. `vpw2`) |
| 16 | Address the pack was built for |
| 20 | File size |
| 24 | Flags: 1 welded, 2 CI4 textures, 4 cull back faces, 8 skinned |
| 28, 32 | Entry table offset and count (1 to 64 entries of: u32 body part, u32 display list offset) |
| 36, 40 | Relocation table offset and count |
| 44, 48 | Texture table offset and count |
| 52 | Required runtime version |
| 56, 60 | Version 2: skin table offset and count (with flag 8). Version 1: reserved. |

The game refuses a pack whose header version is newer than 2 ("made for a newer runtime") or whose required runtime
is newer than 2 ("needs a newer aki_modelimport"). The `models.format` field of a conversion is this version.

Outside a conversion, packs live in `mods\models\` and `mods\models\models.txt` maps them:

```
q-robot slot 1                     # replace whoever is in slot 1
q-monk wrestler 0x0302 costume 0   # replace one wrestler's costume (leave out "costume" for all of them)
off q-robot                        # turn a pack off
```

See `mods/modelimport/MODDING.md` for building packs.

Source of truth: `mods/modelimport/src/core.c` (header checks, `models.txt`), `mods/modelimport/tools/to_akp1.py`,
`mods/modelimport/MODDING.md`, `AkiLauncher/src/mods.cpp`.

## Tiers

Every conversion built by `aki_import.py` (the converter the launcher runs) gets a tier:

| Tier | Meaning |
|---|---|
| **Verified** | Plays through a match like the original on an emulator. |
| **Experimental** | Runs, with known problems. |
| **Unsupported** | Known not to work. |
| **Untested** | Built by aki-import, not tested by the AKI team; it may crash. |

The AKI team's catalog (`tools/romhack/catalog.json`) lists hacks it has tested, by hash only. A conversion gets
the catalog's tier only when what the player's build makes matches what was tested (the patched ROM's SHA-1, and
the codes file's SHA-1 when listed; a codes-only pack matches by its codes file alone). Anything else is
Untested, and so is a patched pack built without the port's ROM to check it against. The tier is written to `aki.json` as
`survey.aki_tier` / `survey.aki_tier_label`, and into `aki-package.json` `short_description`.

Don't confuse it with `survey.tier`, which is how the converter classified the hack, not how well it plays.

Source of truth: `tools/romhack/catalog.json` (`tiers`), `tools/romhack/aki_import.py` (`TIER_LABELS`, `judge`,
`write_conversion`).

## How the launcher installs and runs a conversion

**Installing** (drag the zip onto the launcher, or Install files):

1. It checks the package: `aki-package.json`, `aki.json` in the payload folder, matching ids, the patch mod id,
   safe file names, no ROM files.
2. It checks the player's ROM against `base_rom.sha1` and refuses a conversion made for another ROM.
3. It stages everything, then moves the payload folder to `conversions\<id>\` and any add-ons to `mods\`. On any
   failure it rolls back. Upgrading puts old files in the Recycle Bin (or keeps edited ones as
   `<name>.edited-<date>`). Saves in `saves\<id>\` are kept.
4. It records the install in `<app folder>\packages.json`.

A bare ROM-hack patch mod (`.nrm` with `patch.bps`) dropped on the launcher is wrapped into a conversion for you.

**Running:** pick the conversion in **Play as...** on the home page. The launcher starts the game with the
environment variable `AKI_CONVERSION=<id>` (the folder name) for that start only. The game then loads
`conversions\<id>\aki.json`, applies `patch.nrm` to the player's ROM, runs `codes.txt` through the cheats engine,
seeds saves into `saves\<id>\`, and loads the conversion's textures and models. The launcher warns, but still
starts, when the ROM doesn't match.

Source of truth: `AkiLauncher/src/packages.cpp` (`install_package`, `install_patch_mod`), `AkiLauncher/src/launch.cpp`
(`AKI_CONVERSION`), `mods/cheats/README.md` ("Codes files").

## Testing a conversion

1. **Check it builds and installs.** Run the tool tests after changing a tool:
   `python -m unittest discover -s tools\romhack\tests` (they use synthetic ROMs, no game data). For your own
   files, `python -I tools\romhack\aki_import.py plan <folder> --rom nomercy=<your ROM>` says what each input is
   without building, and `build ... --out Converted` makes the package.
2. **Install it** with the launcher. An install error names the field that is wrong.
3. **Play it.** Pick it in Play as..., start a match, and check the game's log:
   - `[conversion] codes: N active, ...` (from the cheats mod, for a translated Rev 0 list; malformed or dropped
     codes are counted here),
   - `[romhack] ...` lines (functions the patch changed),
   - one line if the models were turned off, and why.
4. **Check the saves** appear in `saves\<id>\` and that your stock saves are untouched.
5. **Try it without the optional parts** (textures off) if you marked them optional.

## Known gaps between code and docs

Where these disagree, the code (listed first) wins.

1. **Package shape.** `aki_conv.py` describes and writes Thunderstore-shaped zips (`manifest.json` at the root);
   the launcher installs only `aki-package.json` packages, and `aki_import.py build` converts the first into the
   second.
2. **Shared texture packages.** The design note puts shared packages in `conversions\_packages\`; the launcher
   only reserves that name (it skips `_` folders) and today installs texture packs as `mods\<id>.rtz`.
3. **`textures.sha256`.** Written by `import-pj64`, dropped by `aki_import.py build`; nothing in the launcher checks
   it.
4. **`base_rom` placement.** The launcher requires `base_rom` in `aki-package.json` to install, but reads it from
   `aki.json` for the Mods page and Play as...; with none in `aki.json`, every ROM shows as a match.
5. **`patch.mod_id` default.** `import-pj64` defaults it to `<id>_patch`, `bake-codes` to `<id>`, `pack-nrm` to the
   `.nrm`'s own id; the launcher requires it to equal the conversion id, and `aki_import.py build` rewrites it so.
6. **`# default: on/off`** in codes.txt is ignored by the engine; every code runs.
7. **`mods/cheats/README.md`** calls the manifest `conversion.json` (it is `aki.json`) and says only No Mercy
   builds the conversion codes host; `mods/cheats/mod.psd1` now enables it for all six games.
8. **`format` / `format_version`** are not checked by the launcher.
9. **RT lights and materials.** Planned as a `components.rt` entry; no code reads one yet.
10. **`models`** is read by the game runtime, not by anything in this repository; this guide follows its design.
11. **`pack-nrm --tier`** accepts any string for `survey.tier`.
12. **`requires.packages`** in `aki-package.json` is parsed by the launcher but not checked or installed.
13. **Conversion `models.roster`** (`format` 1, `costumes` as a count) differs from the launcher's model-picker
    roster files (`schema` 1, `costumes` as a list).
14. **What the game does at start** (seeding saves, loading the conversion's textures and models) happens in the
    game runtime, which isn't in this repository. The steps in [Running](#how-the-launcher-installs-and-runs-a-conversion)
    follow its design; only the codes path (`mods/cheats`) and the save layout (`aki_save.py`) are checked here.
