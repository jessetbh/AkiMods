# Making model packs: the modder's guide

This guide is for anyone who has a rigged 3D character (a `.glb`, `.gltf`, `.fbx`, `.dae` or `.blend`) and wants it in
the ring in **WWF No Mercy** or **Virtual Pro Wrestling 2** (the recompiled PC ports). You don't need to know our tools.
The game's own skeleton and animations move your model: it walks, grapples, takes falls and climbs like the wrestler it
replaces.

You need:
- **Python 3**. Nothing else is needed for `.glb` / `.gltf`. Optional extras: `pip install xxhash` for full-resolution
  textures, `pip install pillow` for JPEG textures.
- **AkiTools** (the `aki-model.py` converter).
- **Blender 4.2 LTS** only for `.fbx`, `.dae` and `.blend` models.
- **The game**, with this mod (**Model import**, `aki_modelimport`). It comes with the game.

Tracking issue: aki-roadmap#130. Everything below matches AkiTools `8bd5068` and AKI Launcher `feat/130-install-packs`.

## 1. The short version

```powershell
cd AkiTools
python aki-model.py quick mymodel.glb --game nomercy --slot 1 --install "$env:LOCALAPPDATA\NoMercyRecompiled" `
    --license CC0-1.0 --source "https://where.it.came.from" --author "you"
```

Then start the game, open **Mods**, turn on **Model import** (and the pack's `-hd` texture pack, if it made one), and
start a match. The wrestler in slot 1 is your model. `quick` prints exactly these next steps, plus anything worth a
look.

## 2. What a model pack is

A pack is a folder, `<id>.akimodel`. The id is lower case `a-z`, `0-9`, `_` and `-`, at most 31 characters (the
converter makes one from your file name). Inside:

| File | What it is |
|---|---|
| `model.bin` | The model as the N64 draws it: geometry, display lists and textures, behind a 64-byte **AKP1** header. The header says which game it's for, how big it is, and where its tables are. The mod loads it into the game's free memory (about 3.5 MB on both games, shared by every pack in a match; the mod also keeps its depth image and the smooth-skinning buffers there, so a pack near the limit can pass `validate` and still be refused in game). |
| `manifest.json` | What the converter did: parts, sizes, the texture tiles, the warnings. Tools read it; you don't need to. |
| `pack.json` | The facts for people: `name`, `author`, `license`, `source`, a suggested slot. **Edit this one freely**: converting again keeps your edits. |
| `preview.png`, `preview-lit.png` | Front and side views in the rest pose. `preview-lit.png` shows the colours the game will multiply (the baked light included); packs built before it existed, like the starter packs, have only `preview.png`. |
| `<id>-hd.rtz` | Optional full-resolution textures, as a texture pack (see "Textures"). |

**Parts.** AKI's wrestlers are made of rigid body parts (No Mercy has 28 part slots, VPW2 has 25: pelvis, chest,
head, upper arms, forearms, hands, thighs, shins, feet and so on), each moved by the game's skeleton. The converter
maps your model's bones onto those parts. It recognises Mixamo, Unreal and common generic names by itself; `bones` and
`new` (section 9) cover an unusual skeleton. With `--weld` and `--skin` (what `quick` uses), the model is drawn as one
welded mesh, and vertices near a joint blend between the parts, the way skinning works in modern games.

**Textures.** The N64 holds at most 4 KB of texture at a time, so each texture becomes a small tile (for example
32x32), and the model looks blurrier than the source. The `<id>-hd.rtz` texture pack swaps the original full-resolution
texture back in on the PC. Install it with the pack (`quick --install` does) and turn it on in the Mods menu. Making it
needs `pip install xxhash`.

## 3. Rights and licences: read this first

Only make and share packs of models **you have the rights to**:
- **Models you made yourself.**
- **Models under an open licence** that allows it:
  - CC0: no conditions;
  - CC-BY: you must credit the author, and `pack.json` carries the credit.
- **Never** ripped commercial models from other games.
- **Never** anything made from ROM data.

A pack must say where its model came from and under what licence: `--license` and `--source` (and `--author`) fill
`pack.json`. Without them, `quick` and `validate` warn you, and the AKI Launcher marks the pack as missing a licence.
The starter packs (section 11) are CC0 examples of doing it right.

## 4. The one-command way: `aki-model.py quick`

```powershell
python aki-model.py quick mymodel.glb --game nomercy --slot 1 --install "$env:LOCALAPPDATA\NoMercyRecompiled" `
    --license CC0-1.0 --source "https://where.it.came.from" --author "you"
```

- `--game nomercy` or `--game vpw2`: the game the pack is built for. A pack only loads in its own game.
- **Who it replaces**, one of:
  - `--slot 1` to `--slot 4`: the wrestler in that slot of a match (default 1);
  - `--wrestler NAME_OR_ID`: a particular wrestler (with `--costume`, only one costume). See "Wrestlers by name" below.
- `--install`: where the game keeps its mods. Any of these works:
  - the game's config folder: `%LOCALAPPDATA%\NoMercyRecompiled` (or `Vpw2Recompiled`) for a normal install;
  - the game's own folder when it runs portable (a `portable.txt` next to it);
  - the `mods` folder itself.

  It refuses the other game's folder. Without `--install`, it builds the pack and prints the three steps to install
  it by hand.
- `--name` sets the pack id. The default comes from the file name.
- `--height` sets the final height in game units. The default is a stock wrestler's, 180.
- `--out` sets where the pack is built. The default is `packs\<game>\`; it can't be inside the mods folder.
- An installed pack of the same id built from a *different* model isn't replaced unless you add `--force`.

What it does, in order:
1. **Converts** with the recommended settings: smooth skinning, welded joints, texture tiles plus the HD texture pack,
   a stock wrestler's height, and the baked soft light (section 8, "Faceted"). If the model has more than 4096
   blended vertices, it says so and builds without smooth skinning (section 8).
2. **Validates** the pack the way the mod will. Errors stop the install and say what to fix. Warnings are listed in
   plain words, with the option that fixes each one.
3. **Installs** (with `--install`):
   - copies the pack into `mods\models\` and its `-hd.rtz` into `mods\`;
   - removes an earlier build's `-hd.rtz` when the new build has none;
   - writes the pack's line at the top of `mods\models\models.txt`, so it wins. An earlier line for the same pack, or
     an `off` line, is replaced. Another pack already on that slot is named in the output.

### Wrestlers by name

`--wrestler` takes a wrestler's **name** once the game has a *roster*, a list of its wrestlers by id. Rosters live in
AkiTools at `model-templates\profiles\<game>-roster.json`. They are being captured from the running games, so a game
may not have one yet. Until then, use the **id** instead (next paragraph).

```powershell
python aki-model.py roster --game nomercy            # list the roster
python aki-model.py roster "mr fixture" --game nomercy   # look one up
python aki-model.py quick mymodel.glb --game nomercy --wrestler "mr fixture" --costume 1 --install ...
```

Names match without caring about case, accents or punctuation, and aliases count. A unique part of a name works, as
do the starts of its words (`"s smith"`). Several matches are listed, and a typo gets "did you mean ...". `--costume`
takes a number, or a costume's name from the roster. In `models.txt` the pack still gets the id, with the name as a
comment: the mod reads ids.

**Finding an id without a roster:**
1. Turn on **Log wrestler ids** on the Model import page of the Mods menu.
2. Start a match. The game's log (its console, or `<Game>Recompiled.log`) prints a line per slot, such as
   `slot 1: wrestler 0302 costume 0`.
3. Use that as `--wrestler 0x0302`, or write `0x0302` in `models.txt`. **Always write the `0x`**: the mod reads a bare
   `0302` as the decimal number 302.

### An FBX, DAE, .blend or OBJ model

`quick` takes these too: it has Blender export a `.glb` first, by itself (Blender 4.2 LTS; Blender 5 has no `.dae`
import).

```powershell
python aki-model.py quick mymodel.fbx --game nomercy --slot 1 --install ... --exclude "*_Sword"
```

- **Finding Blender:** `--blender <blender.exe>`, else the `AKI_BLENDER` environment variable, else Blender in the
  depot's `_tools\blender`, else PATH.
- `--exclude` leaves props out, by object name (repeat it for several).
- A `.blend` file's missing image links are found by file name next to the model and relinked.
- An `.obj` has no skeleton: `quick` fits the game's skeleton to it (`--auto-split`).
- `convert` (section 9) reads glTF only. For it, make the `.glb` with
  `.\aki-model-import.ps1 -Model mymodel.fbx -Out mymodel.glb`, which takes `-Blender` and `-Exclude` the same way.

## 5. Look before you play

Open the pack's `preview-lit.png`: the rest pose from the front and the side, in the colours the game will use. Check
for:
- **Missing or misplaced pieces:** a bone the converter couldn't map (see section 9).
- **A model lying down or facing backwards:** an export with an unusual "up" axis. Re-export it as glTF, Y up.
- **Height:** the converter prints the final height. A stock wrestler is about 180 units, and Snake is about 175.

`python aki-model.py gallery packs` makes `packs\gallery.html`: one card per pack, with its preview, name, author,
licence (flagged when missing), triangles, height and the games it validates for. `--embed` makes it one file to send.

## 6. Choosing who it replaces

### In the AKI Launcher (no text editing)

Select the game and press **M** (Start on a controller) for its **Mods** page. For No Mercy and VPW2 it has a
**Model packs** section. It lists the packs in `mods\models\` with their preview, name, author and licence, and
gives each pack:
- **On**: untick to keep the pack installed but unused.
- **Replaces**: nobody, slot 1-4 of the match, or a wrestler by name, with a costume when the roster names them. A
  wrestler can only be picked by name when the launcher finds the game's roster. Otherwise a wrestler line is shown,
  and **Open models.txt** lets you edit it.

The launcher saves `models.txt` straight away:
- It changes only that pack's line, and keeps everything else (comments, other packs, lines it doesn't know).
- It writes the file in one step, so the game never reads half of it.
- It works on the file as it is on disk at that moment, so edits you made in Notepad meanwhile stay.

Some packs can't be fully edited there, and the page says why:
- **Fully read-only:** the folder name isn't a valid pack id, or `models.txt` can't be read.
- **Only Replaces locked (On still works):** the pack has more than one line in `models.txt`, or it has a wrestler
  line and there is no roster.

**Installing a pack there.** **Install files...**, **Install a pack folder...** or dropping it on the window installs
a model pack:
- **What it takes:** an `<id>.akimodel` folder, a folder holding such folders (an unzipped share), or a `.zip` of
  either.
- **Where it goes:** the pack into `mods\models\`, and its `<id>-hd.rtz` (inside the pack folder, or next to it)
  into `mods\` as a texture pack.
- **Refused:** a pack built for the other game, a folder whose name isn't a valid pack id (upper case is made lower
  case: `Snake.akimodel` installs as `snake`), and replacing a pack the game has open (close the game first).
- **Replacing:** a pack with the same id is replaced, and the old copy goes to the Recycle Bin.
- **Nothing is assigned yet:** pick who it replaces with the pack's **Replaces** control.

**Remove** on a pack moves the pack and its HD textures to the Recycle Bin. Its `models.txt` lines stay, listed as
naming a pack that isn't installed, so you can delete them or install the pack again.

### In the game: the Mods menu

On the **Model import** page of the in-game **Mods** menu:

| Option | What it does |
|---|---|
| **Enabled** | draws model packs (off: every wrestler is stock) |
| **Slot 1-4 pack** | type a pack id to put it in that slot for a quick test; this beats `models.txt` (empty = `models.txt` decides) |
| **Reload** | flip it to read `models.txt` and the packs again right now (they are read at every match start anyway) |
| **Log wrestler ids** | prints each slot's wrestler id and costume to the log, for `wrestler` lines |
| **Log poses (developer)** | logs part matrices for the skinning tools; leave it off |

The mod's README also describes a **Model packs** dialog on this page for picking packs. It is part of the game's
Mods menu (outside this repository), so it isn't covered here.

### `models.txt` reference

The file is `mods\models\models.txt` (create it if there is none). It holds one rule per line, and `#` starts a
comment, also at the end of a line:

```
# pack       replaces
snake        slot 1                     # the wrestler in slot 1 of a match
banjo        wrestler 0x0404            # that wrestler, any costume
cesiumman    wrestler 0x0302 costume 2  # that wrestler, costume 2 only
off banjo                               # banjo is off, whatever the other lines say
```

| Line | Meaning |
|---|---|
| `<pack> slot N` | the wrestler in slot N (1-4) of the match |
| `<pack> wrestler 0xNNNN` | this wrestler (the id from **Log wrestler ids**, written with `0x`) |
| `<pack> wrestler 0xNNNN costume C` | only in costume C (the number the log prints) |
| `off <pack>` | the pack is off, whatever the other lines say |

Which line wins, in order:
1. The Mods menu's **Slot N pack** options.
2. The first `slot` line that matches.
3. The first `wrestler` line that matches. A matching `slot` line beats a `wrestler` line, wherever each one is in
   the file.
4. `off <pack>`: when that pack is the one chosen for a slot, the slot stays stock. A lower-priority line for
   another pack does not take over.

Limits and rules:
- Pack ids are lower case, as in the folder name.
- At most 32 lines count, the file can be at most 16 KB, and a match loads at most 8 packs.
- A line the mod can't read is skipped, and the log says why (`models.txt line N: ...`).

## 7. `validate`: errors and their fixes

```powershell
python aki-model.py validate packs\nomercy\mymodel.akimodel
```

`validate` checks the pack the way the mod will:
- the folder name and pack id;
- the AKP1 header and tables, and the game it's built for;
- the size against the game's memory;
- every display list;
- `manifest.json` and `pack.json`.

**ERROR** lines mean the mod would refuse the pack or draw garbage. Each one is followed by a `fix:` line. The most
common:

| The error mentions | Fix |
|---|---|
| the folder must be named ... | rename the folder to `<id>.akimodel`, or convert again with `--name <id>` |
| pack id ... | rename the folder (and `pack.json` `"id"`) to lower case `a-z`, `0-9`, `_` and `-`, at most 31 characters, or convert again with such a `--name` |
| skinned vertices: the runtime refuses | fewer blended vertices: drop `--subdivide`, convert without `--skin`, or decimate the model in Blender first |
| memory window | make it smaller: fewer or smaller textures (`--textures bake`, or `none`), drop `--subdivide`, or decimate the model |
| is built for ... | convert again for the game you install it into (`--game` with `quick`, `--profile` with `convert`) |
| pack.json profile / pack.json game | `pack.json` was edited or copied from another build: set it back, or convert again |
| pack.json id | set `pack.json` `"id"` to the folder name (without `.akimodel`), or convert again |
| which this aki-model doesn't know | update AkiTools: the pack was built by a newer `aki-model` |
| manifest.json doesn't describe | convert again: `model.bin` and `manifest.json` come from different conversions |
| anything else (display lists, tables, relocations) | the pack is damaged or was edited by hand: convert it again; if the error stays, report it on aki-roadmap with the model and the command |

**Warnings** are worth a look, but the pack loads: for example an empty licence, or a texture shrunk to fit the N64.
`--json` prints everything for scripts.

## 8. Troubleshooting

**The model is see-through from some angles.** The game skips the back side of each triangle, as it does for its own
wrestlers, so a single-sided surface (a cape, a skirt, hair cards) vanishes when seen from behind.
- Make the material **double-sided** in your 3D program before exporting: the converter then turns culling off for
  the pack.
- Inside-out pieces come from mirrored or flipped geometry, and the converter warns about mirrored bones. Recalculate
  the normals outward in Blender and export again.

**It hovers above the mat (or sinks into it).** The converter lowers the feet onto the game's floor when it can, and
warns when the model still hovers (`the model's feet are N units above the game's floor`). Try these:
- Check that the model's feet are at its lowest point, with no stray vertex below the soles.
- Without smooth skinning (`convert` with no `--skin`), add `--retarget stretch`: it moves every bone onto the game's
  own skeleton, short legs included. `quick`'s skinned builds use it already.

**It looks faceted, or flat next to the stock wrestlers.** The game doesn't light its wrestlers: their soft shading is
painted into the vertex colours. The converter does the same, with a soft light and shadowing in creases
(`--bake-light`, on by default with smooth skinning). These are `convert` options (section 9); `quick` always uses
the default:
- `--bake-light 0.5`: half the effect;
- `--bake-light off`: a plain light;
- `--crease 30`: keeps hard edges hard (the angle in degrees);
- `--flat`: the deliberately faceted low-poly look.

**"N skinned vertices: the runtime takes at most 4096".** Smooth skinning blends vertices near joints, and the mod
holds at most 4096 of them. `quick` falls back to building without it (the model bends at the joints like the game's
rigid parts). For smooth joints, lower the vertex count in Blender (the Decimate modifier) and convert again.

**"too big for the memory left" in the log.** All the packs in a match share about 3.5 MB of the game's memory, and
the mod keeps its depth image and the smooth-skinning buffers there too. `validate` checks the converter's limit,
which is a little larger, so a pack close to it can still be refused in game. Use smaller textures, decimate the model,
or use fewer packs in one match.

**Blurry textures.** That's the N64's 4 KB texture memory. Install the `<id>-hd.rtz` texture pack and turn it on in
the Mods menu. No `-hd.rtz`? Run `pip install xxhash` and convert again.

**The wrestler stays stock.** Each reason is one line in the game's log, starting with `[mod]`:

| Log | Meaning |
|---|---|
| `model pack X: no model.bin` | the folder isn't `mods\models\X.akimodel`, or it has no `model.bin` |
| `... no AKP1 header` | made by an older aki-model: convert it again |
| `... built for another game` | the pack's game isn't this one |
| `... too big for the memory left` | see above |
| `slot N: its part matrices aren't where welded pack X needs them` | report it on aki-roadmap#130 |
| `models.txt line N: ...` | that line was skipped; the message says what was expected |

Also check that **Model import** is enabled, the pack id in `models.txt` is lower case, and nothing says `off <pack>`.

## 9. Doing it step by step: `convert`

`quick` is `convert` + `validate` + install with good defaults. Use `convert` to control every option:

```powershell
python aki-model.py convert mymodel.glb --profile nomercy --space model --weld --skin --textures tiles `
    --final-height 180 --out packs\nomercy --name mymodel --license CC0-1.0 --source "https://..." --author you
python aki-model.py validate packs\nomercy\mymodel.akimodel
```

`python aki-model.py convert -h` lists every option. The ones that matter most:
- `--profile nomercy|vpw2`: the game.
- `--space model --weld --skin`: one welded, smoothly skinned mesh, as `quick` builds.
- `--textures tiles|bake|none`:
  - `tiles`: real N64 textures, plus the HD pack;
  - `bake`: colours painted into the vertices, no textures (smallest);
  - `none`: material colours only.
- `--final-height H`: the height as converted, in game units. Alternatively `--height` with `--fit`.
- `--retarget move|stretch|off`: how far the model's joints are moved onto the game's skeleton. The default is
  `stretch` with `--weld --skin`, else `move`.
- `--normals`, `--crease`, `--flat` and `--bake-light`: the shading (section 8).
- `--subdivide N`: more triangles for a very low-poly model (watch the 4096 limit).

**An unusual skeleton.** `python aki-model.py bones mymodel.glb` lists every bone and the part it maps to; unmapped
bones are listed too. If the automatic templates miss, `python aki-model.py new mymodel.glb` writes a bone map
(`mymodel.map.json`) to fill in: each of your bones, and the game part it should drive. Then convert with
`--map mymodel.map.json`. An `.obj` with no skeleton at all converts with `--auto-split`, which fits the game's
skeleton to it.

The AkiTools README ("aki-model.py") has the full reference and the modder walkthrough this guide summarises.

## 10. Games and profiles: how a new game would be added

Everything game-specific lives in one **profile** per game, in AkiTools: `model-templates\profiles\<game>.json`. Its
format is in `profile.schema.json`, and `aki-model.py` checks the rules. It holds:
- the part table (which of the game's body parts each name is: No Mercy 28, VPW2 25);
- the rest joints of the game's skeleton, and the arm pose;
- `model_space`: the floor, top and default height;
- `runtime`: what the mod needs to know about the game's memory. That is the wrestler slots, part nodes, matrices,
  the wrestler-id address, the converter's memory window for packs and the texture formats.

The pack's header records the profile id, and the mod refuses a pack built for another game.

**Adding a game** that draws its wrestlers the same way (AKI's node renderer, as in No Mercy and VPW2; WrestleMania
2000 is next):
1. **AkiTools:** a new `profiles\<game>.json`, made from the game's reverse-engineered facts. Converting with
   `--profile <game>` then just works, and `<game>-roster.json` adds names.
2. **This mod:**
   - a four-line `src\adapters\<game>.c` (`AN_PROFILE`, `AN_PARTS`, `AN_EMITTER`, `#include "aki_nodes.c"`);
   - an `#elif defined(AKI_GAME_<KEY>)` / `#include "adapters/<game>.c"` pair in `src\adapter.c`;
   - in `mod.psd1`: the game in `Games`, and a `PerGame` entry with its addresses (`MI_ACTORS`, `MI_WRESTLER_IDS`,
     `MI_MEM_BASE` / `MI_MEM_SIZE`, `MI_SCRATCH`, and `MI_COSTUMES` or `MI_COSTUMES_U8` for the costume). They
     follow the profile's `runtime` block; the mod's memory window is a little smaller than the converter's (it keeps
     its own buffers there).
3. **The game port:** the host exports `aki_log`, `aki_models_read` and `aki_models_list`.
4. **AKI Launcher:** `"model_profile": "<game>"` on the game's entry in `assets\games.json` turns on its Model packs
   section.

A game with a different renderer writes its own adapter against `src\adapter.h`: the slots, part nodes, matrices,
wrestler ids, render state, and the hook that draws the parts. The README's "For developers" section has the details.

## 11. The starter packs: worked examples

Both games ship eight CC0 packs, built from the free Quaternius characters:
- `q-warrior`, `q-monk`, `q-rogue`, `q-ranger`, `q-cleric` and `q-wizard` (the RPG characters, weapons left out);
- `q-human` (Animated Human);
- `q-robot` (Robot Expressive).

Credits and sources are in `mods\models\CREDITS.md`.
- **Installed once:** the game copies each one into `mods\models\` once. `mods\models\.bundled` remembers which, so
  a starter pack you edit or delete stays that way. Delete `.bundled` to get them back.
- **Their HD textures** are one texture pack, `mods\aki_starter_packs_hd.rtz`.
- **No wrestler changes until you pick:** nothing writes `models.txt` for them. Pick them in the launcher's Model
  packs section or in `models.txt`.

They were made with the steps in this guide:
1. `aki-model-import.ps1` turned the Quaternius `.blend` files into `.glb`, with `-Exclude` for the weapons. The robot
   came as a `.glb` already (via three.js).
2. `aki-model.py convert --profile <game> --space model --weld --skin --textures tiles`.
3. `validate`.

Open any of their `pack.json` files to see the licence and source filled in. That's what yours should look like before
you share it.

## 12. Sharing your pack

Zip the `<id>.akimodel` folder. Its `<id>-hd.rtz`, if it has one, is inside it. A player installs it in either way:
- **With the AKI Launcher:** drop the zip on the game's Mods page (or use **Install files...**), then pick who it
  replaces under Model packs. The launcher puts the pack and its HD textures in the right folders.
- **By hand:**
  1. Unzip the folder into `mods\models\`.
  2. Copy the `-hd.rtz` from the pack folder into `mods\`.
  3. Add a `models.txt` line.

Nothing is rebuilt. Keep `pack.json` honest: the licence, the author, and where the model came from.
