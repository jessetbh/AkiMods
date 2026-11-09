# Conversion format (aki-conversion v1)

A **conversion** turns one of the AKI recomps into a different game: a ROM hack, a code pack, a texture pack, saves
and models, all run on top of the player's own ROM. This guide covers every file a conversion holds and every field
of its manifest, `aki.json`, so you can write one by hand or check what the tools made.

A conversion never contains ROM data. Patches are applied to the player's own cartridge when the game starts.

Each section ends with a **Source of truth** line that names the code that reads or writes it. Where this guide and
the code disagree, the code is right; the known gaps are listed at the end.

Paths below are in this repository (AkiMods) unless they start with `AkiLauncher/` or follow "N64ModernRuntime"
(the game runtime every port is built on).

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
    textures\         a texture pack inside the conversion (optional; aki.json textures "dir")
    rt_lights.json    ray-tracing lights (optional; aki.json rt "lights")
    rt_materials.json ray-tracing materials (optional; aki.json rt "materials")
    icon.png          256x256 picture (optional; aki.json "thumb")
    README.md         what it is, credits, permission (optional)
    docs\             anything else the author shipped, e.g. move lists (optional)
```

- `<app folder>` is `%LOCALAPPDATA%\<Game>Recompiled`, or the folder next to the game's exe when it holds
  `portable.txt`.
- The folder name **is** the id: the game finds a conversion by its folder, and the launcher lists it under that
  name whatever `aki.json` says.
- Folders under `conversions\` whose names start with `_` or `.` are not conversions; the launcher and the game
  skip them. `conversions\_packages\<package>\` holds shared texture packages (see [textures](#textures)).
- A conversion's saves live in `<app folder>\saves\<id>\`, apart from the stock game's saves. Upgrading or removing
  a conversion never touches them.
- Any patch mod (`.nrm`) or texture pack (`.rtz`, or a folder with an `rt64.json`) in the conversion folder
  is opened with it: the game scans the folder for mods when the conversion runs.

Source of truth: `AkiLauncher/src/packages.cpp` (`list_conversions`, `install_package`),
`AkiLauncher/src/conversions.cpp` (`list_conversions`, `add_conversion`), N64ModernRuntime
`librecomp/src/conversion.cpp` (`load_active_conversion`, `list_installed_conversions`), `tools/romhack/aki_save.py`
(header, `install`).

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

**Without `aki-package.json`.** `tools/romhack/aki_conv.py` (`import-pj64`, `pack-nrm`) writes an older
Thunderstore-shaped zip, with `manifest.json`, `icon.png`, `README.md` and `aki.json` at its root. The launcher
installs a zip or folder with `aki.json` at its root and no `aki-package.json` along a second, simpler path:

- the whole zip becomes `conversions\<id>\` (`aki.json` `id`), in the game that `aki.json` `game` names;
- it checks `format` (`aki-conversion`), `format_version` (`1`), the id, that the paths in `aki.json` stay inside
  the folder, and the player's ROM against `aki.json` `base_rom.sha1`;
- it seeds the conversion's saves (see [saves](#saves));
- it doesn't check the patch's `mod_id` and doesn't record the install in `packages.json`. It refuses an id that
  the package installer already installed.

The same path installs a shared texture package (see [textures](#textures)). `aki_import.py build` repacks the
`import-pj64` zips it makes itself into the `aki-package.json` shape (it drops `manifest.json` and moves the rest under
`conversion/`).

Source of truth: `AkiLauncher/src/packages.cpp` (`parse_manifest`, `check_package`),
`AkiLauncher/src/conversions.cpp` (`is_conversion_source`, `add_conversion`), `tools/romhack/aki_import.py`
(`package_manifest`, `write_conversion`).

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
    "rt":       { "lights": "rt_lights.json", "materials": "rt_materials.json" },
    "models":   { "dir": "models", "format": 2, "roster": "models/roster.json",
                  "slots": [{ "wrestler": "0x0404", "name": "Example Wrestler", "costume": 0, "pack": "q-robot" }] }
  },
  "addons": { "allow": "data", "mods": ["aki_camera"] }
}
```

| Field | Type | Meaning |
|---|---|---|
| `format` | string | Always `aki-conversion`. The game refuses to start the conversion otherwise. |
| `format_version` | number | Always `1`. The game refuses to start the conversion otherwise. |
| `id` | string | Same rules as the package id. Must equal the folder name and the package id; the game refuses a mismatch. |
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
| `components` | object | What the conversion is made of. See [Components](#components). At least one component the game can use is required, or it refuses to start the conversion. |
| `addons` | object | Optional: which of the player's own mods may run beside the conversion. See below. |
| `survey` | object | Written by the tools: how the conversion was built and how well it was tested. See below. |

**`addons`.** While a conversion runs, the game turns off the player's other mods unless this allows them:

| Key | Meaning |
|---|---|
| `allow` | `none`: only the conversion's own mods. `data` (the default, also used for any value the game doesn't know): the texture packs the player has turned on, and the code mods listed in `mods` if the player has turned them on. `all`: every mod the player has turned on. |
| `mods` | Ids of code mods allowed beside it, e.g. `aki_camera`. |

Another ROM hack's patch never runs beside the conversion's. The game turns on the cheats mod for a conversion with
codes and the model import mod for one whose models are on; otherwise the cheats mod stays off. The player can
change this for one conversion in `conversions\<id>\addons.json` (`{"enabled": [ids], "disabled": [ids]}`), which
wins over `addons` (but can't turn on another ROM hack or turn off the conversion's own mods).

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
(`list_conversions`), N64ModernRuntime `librecomp/src/conversion_rules.cpp` (`parse`, `session_enabled_mods`).

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
- `rom`: the revision the addresses are for (`rom_revision` is read too, as an older name). Make it the port's
  revision (`base_rom.revision`). Codes for another revision need translating first (`aki_conv.py translate-codes`);
  the bake step refuses them, and No Mercy Rev 0 lists are the only ones the game can translate itself. The
  launcher uses `rom` for its notes (codes for another game stay off); when the game runs the codes, the file's own
  `# rom:` line decides.

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
  to flash; Revenge, WrestleMania 2000, VPW64 and VPW2 to SRAM; World Tour has only a Controller Pak. A note for
  people and tools: the launcher doesn't read it.
- `mode`: `seed`, the only mode. The launcher doesn't read it either; every entry is seeded.

**Seeding is done by the launcher when it installs the conversion**, not by the game. Each `file` is copied to
`saves\<id>\<its file name>` (the `saves/` folder part is dropped) unless a file of that name is already there, so an
upgrade never overwrites a player's progress. Only the install path for packages without `aki-package.json` seeds
(see [The package around it](#the-package-around-it)); the `aki-package.json` installer doesn't seed yet. In that
case, or by hand, `aki_save.py install <file> --game <key> --conversion <id> --if-missing` does the same.

To make these from an emulator save (`.fla`, `.sra`, `.mpk`, `.eep`, RetroArch `.srm`), use
`aki_save.py convert <file> --game <key> -o <out>`; it handles byte order and pak splitting.

Source of truth: `AkiLauncher/src/conversions.cpp` (`add_conversion`, seeding), `tools/romhack/aki_conv.py`
(`cmd_import_pj64`, saves part), `tools/romhack/aki_save.py` (`GAMES`, `KINDS`, `install --if-missing`).

### textures

```json
"textures": { "package": "my-conversion-textures", "version": "1.0.0", "naming": "rice", "optional": false }
```

A conversion gets its textures in one of two ways: a shared **package** installed beside it, or a **folder** inside
it.

- `package`: the shared texture package this conversion uses. Big packs ship as their own package so several
  conversions can share one and players can skip a download they already have. While the conversion runs, the game
  opens `conversions\_packages\<package>\`. If that folder isn't there, the game logs it and runs without the
  textures, and the launcher shows a note.
- `version`: that package's version. Recorded only; nothing checks it.
- `sha256`: optional; the SHA-256 of the texture package zip, as lowercase hex, which pins the exact file.
  `import-pj64` writes it (its zips are byte-for-byte reproducible); `aki_import.py build` drops it and lists the
  package in `aki-package.json` `requires.packages` instead. When the launcher installs a package zip it stores the
  zip's SHA-256 in `_packages\<package>\.package.sha256`. If that differs from `sha256`, the launcher notes that the
  installed pack isn't the one the conversion was made with. It still runs.
- `dir`: a folder inside the conversion that holds a texture pack, e.g. `textures`. If it's missing, the game logs it
  and runs without it. When `naming` is `rice` or `jabo` and the folder has no `rt64.json`, the game writes one, so
  a pack can ship with its author's own file names.
- `naming`: how the images are named: `rt64`, `rice` or `jabo` (see [Textures and naming](#textures-and-naming)).
- `optional`: `true` if the conversion plays fine without the pack. The tools always write `false`. Nothing reads
  it yet.
- `file`: a texture pack file in the conversion. The game reads no field of it; an `.rtz` in the conversion folder is
  opened anyway (see [Folder layout](#folder-layout)).

**The shared package itself** is a zip named `<package>-<version>.zip` with an `aki.json` at its root:

```json
{ "format": "aki-package", "format_version": 1, "kind": "textures", "game": "nomercy", "naming": "rice",
  "folder": "My_Textures", "images": 1234 }
```

and the pack in a folder with an `rt64.json` (here `My_Textures\rt64.json`). The launcher installs the whole zip
as `conversions\_packages\<package>\`; the package name is the zip's name up to its last `-`, so
`Team-My_Textures-1.0.0.zip` becomes `Team-My_Textures` (a folder you install keeps its own name). The launcher
checks only `format`, `format_version` (`1`), `kind` and `game`. `import-pj64` writes this shape. A `kind: texture-pack` package with
`aki-package.json` is a different thing: it installs to `mods\<id>.rtz` like any texture pack, and runs beside a
conversion only as one of the player's add-ons (see `addons`).

Source of truth: N64ModernRuntime `librecomp/src/conversion.cpp` (`load_active_conversion`, textures),
`AkiLauncher/src/conversions.cpp` (`add_conversion`, texture package; `annotate`), `tools/romhack/aki_conv.py`
(`cmd_import_pj64`, textures part), `tools/romhack/aki_import.py` (Project64 bundle repack).

### RT lights and materials

```json
"rt": { "lights": "rt_lights.json", "materials": "rt_materials.json" }
```

The ray-traced renderer reads per-venue lights and per-texture surface materials from the game's
`assets/rt_lights.json` and `assets/rt_materials.json`. A conversion can replace either one:

- `lights`: a lights file in the conversion folder, in the same format as the game's `assets/rt_lights.json`.
- `materials`: a materials file, in the same format as `assets/rt_materials.json`.

Either key may be left out; the game then uses its own file. Both paths must stay inside the conversion folder (no
`..`, drive or leading `/`). The game refuses to start a conversion that breaks this, and the launcher refuses to
install one when it installs packages without `aki-package.json`.

**Only the launcher passes them on.** When it starts a conversion, the launcher sets `WCW_RT_LIGHTS_FILE` and
`WCW_RT_MATERIALS_FILE` to the full paths of these files for that start only, and the renderer reads those. The game
doesn't read `components.rt` itself (it only names `rt` in its `[conversion] running ...` log line), so a conversion
started from the game's own Mods menu runs with the stock lights and materials.

Source of truth: `AkiLauncher/src/conversions.cpp` (`parse_info`, `add_conversion`, `conversion_env`),
`AkiLauncher/src/launch.cpp` (`GameSession::launch`, the extra environment), N64ModernRuntime
`librecomp/src/conversion_rules.cpp` (`parse`, the path check).

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

- `dir`: required; the folder in the conversion that holds the model packs (`<pack>.akimodel\`). Without it the
  component is ignored. While the conversion runs, the game's model host serves models from here **instead of**
  `mods\models\`: the player's own packs and `models.txt` don't mix in, and the model import mod
  (`aki_modelimport`) is turned on for the session. If the folder is missing, the game logs it.
- `format`: a whole number, the AKP1 pack version the packs need (see [Model packs](#model-packs)). Games read up to
  `2`; missing means `1`. A newer value turns the models off with an "update the game" message; `0`, a negative number
  or a value that isn't a whole number turns them off too.
- `roster`: optional; the path of a roster file for the hack's wrestlers, for the tools and the launcher. The game
  only checks that the path stays inside the conversion folder; nothing reads the file yet. Use the shape of the
  rosters the launcher's model picker reads (`<game>-roster.json`), with `costumes` as a **list**:
  `{"schema": 1, "wrestlers": [{"id": "0x0404", "name": "Example Wrestler", "costumes": [{"costume": 0, "name": "Default"}]}]}`.
  `id` is a string starting with `0x`; `costumes` is optional.
- `slots`: optional; which pack replaces which wrestler. Each entry:
  - `wrestler`: required; the hack's wrestler id, as hex text (`"0x0404"` or `"404"`, at most 4 digits) or a number up
    to 65535.
  - `pack`: required; the pack id (`a-z A-Z 0-9 _ - .`, at most 64 characters, not starting with `.`).
  - `costume`: optional; a whole number from 0 to 255. Leave it out for every costume.
  - `name`: optional; the wrestler's name, for people. The launcher's Mods page lists up to four names, and the game
    logs one `[conversion] model slot: ...` line per slot.

  The game serves the slots to the model import mod as a `models.txt` with one `<pack> wrestler 0xNNNN [costume C]`
  line each. An empty list means no wrestler changes. Without `slots`, the game reads `<dir>\models.txt` as it is.
- HD textures for the packs (`<pack>-hd.rtz`) go beside `aki.json`, where the game opens them with the conversion's
  other mods.

The game runs the conversion **without models** (one `[conversion] ...` line in its log) when `format` is unusable or
too new, `slots` isn't a list or any slot is bad, or the hack's patch has a `relink.json` that puts code, data, preloads
or zeroed memory in the Expansion RAM the model packs load into (`0x80400000`-`0x80800000`, any address mirror), or
whose `relink.json` doesn't parse. Plain patch hacks are fine. The launcher's Mods page shows "Custom models for N
wrestlers" for the conversion, or "Custom models are off for this conversion" and why; it checks `format` and the
patch's `relink.json`, not the slots. A bad `dir` or `roster` path (one that leaves the conversion folder) is
different: the game won't run the conversion at all (see [Running](#how-the-launcher-installs-and-runs-a-conversion)).

Older game builds ignore this component.

Source of truth: N64ModernRuntime `librecomp/src/conversion_rules.cpp` (`parse_models`, `models_txt`,
`models_refusal`, `session_enabled_mods`), `librecomp/src/conversion.cpp` (`active_conversion_models_dir`,
`active_conversion_models_txt`), `librecomp/src/recomp.cpp` (the refusal at the mod scan),
`AkiLauncher/src/packages.cpp` (`conversion_models`), `AkiLauncher/src/modelpacks.cpp` (`load_roster_file`),
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
folder as a texture pack. For a conversion, ship it as a shared texture package or as a folder inside the conversion
(see [textures](#textures)). A pack for players to add to any game ships as its own package (`kind: texture-pack`),
which the launcher installs as `mods\<id>.rtz`.

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

That is the `aki-package.json` installer. A zip or folder with only an `aki.json` takes the simpler path described
in [The package around it](#the-package-around-it); that path also seeds the conversion's saves.

**Running:** pick the conversion in **Play as...** on the home page. The launcher starts the game with the
environment variable `AKI_CONVERSION=<id>` (the folder name), plus `WCW_RT_LIGHTS_FILE` / `WCW_RT_MATERIALS_FILE`
when the conversion has [RT files](#rt-lights-and-materials), for that start only. The game then loads
`conversions\<id>\aki.json` and:

- opens the mods in the conversion folder (`patch.nrm`, applied to the player's ROM, and any `.rtz`), its shared
  texture package from `conversions\_packages\` and its `textures` `dir`;
- turns off the player's other mods, except those `addons` allows;
- runs `codes.txt`: the game runs a file tagged with the port's own revision itself, and hands any other revision to
  the cheats mod;
- serves the conversion's models (see [models](#models));
- keeps its saves in `saves\<id>\`.

It logs one `[conversion] running <id> ("<name>"): patch codes ...; saves in saves/<id>` line. The launcher warns,
but still starts, when the ROM doesn't match.

A conversion the game can't run (its `aki.json` is missing or not a valid v1 manifest, the id doesn't match the folder,
it has no usable components, or a path leaves the folder) **stops the game** with a message when the launcher asked
for it, so its saves and mods never mix with the player's own. The game's own Mods menu can also pick a conversion for
the next starts (`conversions\active.txt`); one picked that way that can't run is dropped and the game starts normally.
`AKI_CONVERSION` wins over `active.txt`, and only the launcher passes the RT files.

Source of truth: `AkiLauncher/src/packages.cpp` (`install_package`, `install_patch_mod`), `AkiLauncher/src/launch.cpp`
(`GameSession::launch`), `AkiLauncher/src/conversions.cpp` (`conversion_env`), N64ModernRuntime
`librecomp/src/conversion.cpp` (`decide_session`, `load_active_conversion`, `active_native_codes_ready`),
`librecomp/src/recomp.cpp` (the mod scan), `mods/cheats/README.md` ("Codes files").

## Testing a conversion

1. **Check it builds and installs.** Run the tool tests after changing a tool:
   `python -m unittest discover -s tools\romhack\tests` (they use synthetic ROMs, no game data). For your own
   files, `python -I tools\romhack\aki_import.py plan <folder> --rom nomercy=<your ROM>` says what each input is
   without building, and `build ... --out Converted` makes the package.
2. **Install it** with the launcher. An install error names the field that is wrong.
3. **Play it.** Pick it in Play as..., start a match, and check the game's log:
   - `[conversion] running <id> ...`, listing the components the game found,
   - `[conversion] codes: N active (native table: ...)` (the game's own, for codes in the port's revision) or
     `[conversion] codes: N active, ...` (from the cheats mod, for a translated Rev 0 list); malformed or dropped
     codes are counted here,
   - `[romhack] ...` lines (functions the patch changed),
   - `[conversion] model slot: ...` per slot, or `[conversion] models refused: ...` if the models were turned off,
     and why,
   - `[conversion] texture package ... isn't installed` or `texture folder ... is missing` if the textures weren't
     found.
4. **Check the saves** appear in `saves\<id>\` and that your stock saves are untouched.
5. **Try it without the optional parts** (textures off) if you marked them optional.

## Known gaps between code and docs

Where these disagree, the code (listed first) wins.

1. **Two install paths.** The `aki-package.json` installer checks the patch's `mod_id` and records installs in
   `packages.json`, but doesn't seed saves. The path for packages with only an `aki.json` seeds saves and installs
   shared texture packages, but doesn't check `mod_id` or record the install. `aki_import.py build` makes the
   first kind and `aki_conv.py` the second.
2. **`textures.version` and `textures.optional`** are recorded only; nothing reads them.
3. **`textures.sha256`.** Written by `import-pj64`, dropped by `aki_import.py build`. The launcher only notes a
   mismatch, and only for a package installed from a zip (a folder gets no `.package.sha256`).
4. **`base_rom` placement.** The `aki-package.json` installer requires `base_rom` in `aki-package.json`, but the
   launcher reads it from `aki.json` for the Mods page, Play as... and the other install path; with none in
   `aki.json`, every ROM shows as a match.
5. **`patch.mod_id` default.** `import-pj64` defaults it to `<id>_patch`, `bake-codes` to `<id>`, `pack-nrm` to the
   `.nrm`'s own id; the `aki-package.json` installer requires it to equal the conversion id, and
   `aki_import.py build` rewrites it so.
6. **`# default: on/off`** in codes.txt is ignored by the engine; every code runs.
7. **`saves[].kind` / `saves[].mode`** are written by the tools but not read by the launcher.
8. **RT files** reach the game only through the launcher's `WCW_RT_*` variables; a conversion chosen in the
   game's own Mods menu runs with the stock lights and materials.
9. **`pack-nrm --tier`** accepts any string for `survey.tier`.
10. **`requires.packages`** in `aki-package.json` is parsed by the launcher but not checked or installed.
11. **`models.roster`** has no reader yet: the game only checks its path. The shape above is the one the
    launcher's roster reader takes, the only roster reader in the code.
12. **The model host and the RT renderer** that read `active_conversion_models_dir` / `active_conversion_models_txt`
    and `WCW_RT_*` live in the game repositories, which this guide wasn't checked against; what they are given is
    checked (N64ModernRuntime, AkiLauncher).
13. **The models line on the Mods page** checks `format` and the patch's `relink.json` but not the slots, so a bad
    slot shows "Custom models" there while the game runs without them.
