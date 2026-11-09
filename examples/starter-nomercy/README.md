# AKI Starter Kit: an example conversion for WWF No Mercy

A complete conversion built from source by one script. It uses every part of the AKI conversion format, so you can
copy it and start your own. Every file in this folder is **CC0 1.0** (public domain; see `COPYING.CC0`), even
though the rest of AkiMods is GPL-3.0. Copy any of it into your own work without asking or crediting.

The field-by-field reference for `aki.json` is [docs/conversion-format.md](../../docs/conversion-format.md). This
README covers what the kit does and how each part was made.

## What it does in the game

Start the conversion and leave the controller alone. The intro is skipped, and about 5 seconds after the title
screen comes up the tag team attract demo starts in the Raw arena, so you can see each part:

| Part | File(s) | What you see |
|---|---|---|
| Code change | `patch/` → `patch.nrm` | no intro; the tag team demo starts after ~5 s instead of ~30 s, always in Raw |
| Codes | `codes.txt` | player 1's spirit meter is always full in a match; Championship money stays full |
| RT light rig | `rt/rt_lights.json` | Raw is lit teal and violet, with a dark arena (turn on ray tracing) |
| Seed save | `save/seed_save.py` | everything is unlocked in this conversion's save |
| Textures | `textures/make_textures.py` | a teal ring mat, an "AKI STARTER KIT" apron banner, star turnbuckle pads |
| Models | `models/slots.json` + the starter packs | eight low-poly characters replace eight stock wrestlers |

## Build it

You need the AkiMods build tools (see the main README, "Building"), Python 3 with Pillow, and a No Mercy port
checkout. For a test run, you also need AkiTools next to AkiMods.

```powershell
# Build and check the conversion. Output goes to build\starter-nomercy\.
.\build.ps1 -BlankSave <your blank wwf.nomercy.us.bin>

# Also install it into a game folder (a portable checkout, or %LOCALAPPDATA%\NoMercyRecompiled for install only).
.\build.ps1 -BlankSave <...> -GameDir <game folder>

# Also run the game once (short, muted, windowed), then check the log for every part.
.\build.ps1 -BlankSave <...> -GameDir <game folder> -Run
```

`-Run` uses AkiTools `aki-run.ps1`, which needs a **portable** checkout (`portable.txt` in it; `aki-run.ps1
-InitPortable` makes one) and refuses the main checkout `<depot>\NoMercyRecomp`: use a worktree such as
`NoMercyRecomp-kit`.

**About `-BlankSave`:** a No Mercy save holds data the game builds from the ROM, such as default attires and
movesets. So the kit can't ship a save file. It ships `save/seed_save.py` instead, which makes the seed save from a
blank save made by your own game: start the game once, quit, and use `saves\wwf.nomercy.us.bin`. For the same reason,
the conversion zip you build carries a save made from your ROM: share the kit's source rather than that zip.

The build writes:
- `build\starter-nomercy\aki_starter_kit\`: the conversion folder.
- `AKI-Starter_Kit_Textures-1.0.0.zip`: the texture package. Players add it on the launcher's Mods page;
  `aki.json` pins its sha256.
- `aki_starter_kit-1.0.0.zip`: the conversion as a package, made and checked by AkiTools `aki-package.py`.

### It's also the format's regression test

`build.ps1` checks each step. `kit.py validate` checks the format:
- every file `aki.json` names is there;
- the patch's mod id matches;
- the codes parse and carry the right ROM tag;
- the rig is valid;
- the texture package's sha256 matches;
- each model slot has an AKP1 pack and a roster entry;
- AkiTools `aki-package.py` (the launcher's install checks) accepts the package.

With `-Run`, `kit.py check` reads the game's log and confirms that each part loaded:
- the conversion line lists all six parts;
- `patch.nrm` and the texture package are enabled;
- the codes are active;
- the rig loaded from the conversion;
- the model import is on, and all eight slots are served;
- the seed save is in place.

Run it after any change to the conversion format, the launcher's install, or the runtime.

Two steps exist only in the launcher today, so the kit does them itself:
- **The RT rig** reaches the game only as `WCW_RT_LIGHTS_FILE`, which the launcher sets, so `-Run` sets it too
  (aki-roadmap#241).
- **The seed save** is copied into `saves\aki_starter_kit\` only by the launcher's Install, so `kit.py install`
  copies it too, and never over an existing save (aki-roadmap#242).

## How each part was made

### Code change (`patch/`)

`patch/` is an ordinary AkiMods mod folder: `mod.psd1`, `mod.template.toml` and `src/starter_patch.c`.
AkiMods `build.ps1 -ModsDir` compiles it with zig cc and packs it with RecompModTool. The source hooks three game
functions by their recompiler names:
- the intro's per-frame function, which does what Start does as soon as the intro allows it;
- the title screen's per-frame function, which picks the tag team demo and moves the idle counter forward;
- the demo setup, which sets the venue byte after it returns.

The comments explain each hook and address. The addresses come from `mods/quickmatch/mod.psd1`, where they were
found and checked. To find your own, read the game repo's `RecompiledFuncs` and `syms/dump.toml`, and test with
the free camera and the log.

The mod id must equal the conversion id (`aki_starter_kit`): that's what `components.patch.mod_id` names, and the
launcher refuses a mismatch.

### Codes (`codes.txt`)

GameShark-style codes that the game applies every frame. The file shows two kinds:
- a plain per-frame write;
- a conditional (`D3`: "only while a match runs") that guards a write to memory that holds wrestlers only during
  a match.

The full grammar is in `mods/cheats/README.md` ("Codes files"). The port runs No Mercy **Rev 1**. Codes published
for Rev 0 must be translated (`tools/romhack/aki_conv.py`).

### RT light rig (`rt/rt_lights.json`)

A full rig file. While the conversion runs it replaces the game's `assets/rt_lights.json`, so it has a `default`
plus its own venues. Raw is matched by the hashes of textures it draws. Those hashes name the original textures, so
the match still works after the kit replaces the mat and logo. Every field is described at the top of the game's
own `assets/rt_lights.json`. `WCW_RT_VENUE_LOG=1` prints a scene's hashes.

The format also has an `rt.materials` field, but nothing reads it yet (aki-roadmap#241), so the kit leaves it out.

### Seed save (`save/seed_save.py`)

One edit: the 22-byte unlock table in the save image is set to `FF`, which unlocks everything (the same table the
"unlock everything" code writes in RAM).

No Mercy saves to flash as a log of 128-byte pages, each with an XOR checksum. The script reads the newest copy
of each chunk, applies the edit, and writes a compacted log the way the game does. It then re-reads its own output
and checks that only those 22 bytes changed. The layout is decoded in aki-roadmap#51.

A seed save is copied into the conversion's own `saves\<id>\` once, on install, and never over a save that's
already there.

### Textures (`textures/make_textures.py`)

Three images drawn entirely in code: shapes, seeded noise and a block font defined in the script. There's no game
art and no font file, and on the same toolchain the same run always gives the same bytes. Each file is named by the **rt64 hash** of the
texture it replaces (`"naming": "rt64"`). Find a texture's hash with a texture dump.

Keep the original's aspect ratio and scale it up: 4x here. The kit puts the images in a separate **texture
package**, so a big pack can be added once and pinned by sha256. An inline `"dir"` inside the conversion works
too (docs/conversion-format.md).

### Models (`models/slots.json`)

The eight starter packs from `mods/modelimport/packs/nomercy` (CC0 models by Quaternius; credits in that folder's
CREDITS.md). Each one replaces a stock wrestler through `components.models.slots`:
- `wrestler` is the game's id: turn on the aki_modelimport mod's **Log wrestler ids** option to see them;
- `pack` is the `.akimodel` folder;
- `name` is a label for people.

The labels are neutral on purpose: the kit names no real wrestlers. `models/roster.json` is written from the same
list. The packs' HD textures (`aki_starter_packs-hd.rtz`) sit beside `aki.json` and load as the conversion's own
data mod.

To make your own model pack, see `mods/modelimport/MODDING.md` and AkiTools `aki-model.py`.

## How to test a conversion

- **AkiTools `aki-run.ps1`**: start the game with `AKI_CONVERSION=<id>`, muted and windowed, with timed
  screenshots. Then read `stderr.txt`. Every part logs a `[conversion]` or `[mod]` line, listed in
  `kit.py check`.
- **Check each part**, not only "it boots":
  - the code change does what it should;
  - codes report N active;
  - textures show (a screenshot);
  - the rig loaded from your file;
  - the models are drawn;
  - the save is seeded.
- **Support tiers** (aki-roadmap#172) are about catalogued conversions of existing ROM hacks: a conversion counts as
  tested only when its built output matches the tested pack byte for byte. Your own conversion doesn't need a tier.
