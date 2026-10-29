# aki_modelimport: imported models replace wrestlers

Draws 3D models in place of wrestlers. The game's own skeleton and animations move them: they walk, grapple, take
falls and climb. Supported games: **WWF No Mercy** and **Virtual Pro Wrestling 2**. Tracking: aki-roadmap#130.

The models come as **model packs**, folders made by AkiTools `aki-model.py` from glTF, GLB or OBJ files. Adding a
model means copying its folder in. Nothing has to be rebuilt.

## Install a pack

1. Put the pack folder in the game's `mods\models\` folder:
   - a normal install: `%LOCALAPPDATA%\<Game>Recompiled\mods\models\`;
   - a portable install: `mods\models\` next to the game.
2. Say who it replaces in `mods\models\models.txt` (create the file if there isn't one):

```
# pack       replaces
snake        slot 1                   # wrestler slot 1-4 of a match
banjo        wrestler 0x0404          # a wrestler, by the game's id (see "Find a wrestler's id")
cesiumman    wrestler 0x0302 costume 2
off banjo                             # turns a pack off, whatever the other lines say
```

- A pack id is its folder name without `.akimodel`: lower case `a-z`, `0-9`, `_` and `-`, at most 31 characters.
- The Mods menu's **Slot 1-4 pack** options beat `models.txt`. Type a pack id there for a quick test.
- After that, the first matching `slot` line wins, then the first matching `wrestler` line.
- The mod reads `models.txt` and the packs again at the start of every match. To read them right away, flip
  **Reload** in the Mods menu.

## Starter packs

The games ship eight ready-made packs, all CC0 (credits and sources: `packs\CREDITS.md`, installed as
`mods\models\CREDITS.md`): `q-warrior`, `q-monk`, `q-rogue`, `q-ranger`, `q-cleric`, `q-wizard`, `q-human` and
`q-robot`. At startup each game copies them into `mods\models\`, each one only when no folder of that name is there
yet, so a pack you edited or replaced is never overwritten. Their HD textures come as one texture pack,
`mods\aki_starter_packs_hd.rtz`. Nothing writes `models.txt`: no wrestler changes until you turn the mod on and pick a
pack (the **Model packs** dialog on this mod's page, `models.txt`, or the Slot N options).

For mod developers: the packs live in `packs\<game>\`, built with AkiTools
`aki-model.py convert --space model --weld --skin --textures tiles --profile <game>`. Merge their `<id>-hd.rtz` files
with `tools\merge_rtz.py packs\<game>\aki_starter_packs_hd.rtz ...`. `.\stage.ps1 modelimport -RepoSuffix -fullconv`
copies them into the games' `assets\mods\` with the .nrm (mod.psd1 `Packs`).

## Find a wrestler's id

Turn on **Log wrestler ids** in the Mods menu and start a match. The log (stderr, or `<Game>Recompiled.log`) prints
one line per slot, such as `slot 1: wrestler 0302`. Write that id as `0x0302` in `models.txt`.

The costume is the value the game builds the slot with: on No Mercy `func_800140B4`'s costume argument (a byte per
slot at 0x8007626C, the one it looks the wrestler's appearance up with), on VPW2 `func_80014730`'s. The log prints it
(`slot 1: wrestler 0302 costume 0`). Its numbering isn't checked against the costume menus yet: write what the log
shows.

## What goes wrong, and what the log says

Every problem is one line in the log, starting with `[mod]`, and the wrestler stays stock:

| Log | Meaning |
|---|---|
| `model pack X: no model.bin` | the folder isn't `mods\models\X.akimodel`, or it has no `model.bin` |
| `... no AKP1 header` | made by an older aki-model: convert it again (or `tools\to_akp1.py`) |
| `... built for another game` | the pack's profile isn't this game's |
| `... too big for the memory left` | the packs in this match need more than the game's free memory (3.75 MB on No Mercy and VPW2) |
| `slot N: its part matrices aren't where welded pack X needs them` | this slot's body is laid out differently (report it on #130) |
| `models.txt line N: ...` | that line was skipped; the message says what was expected |

## For modders: making packs

The pack contract (the `model.bin` AKP1 header, `manifest.json`, `pack.json`, `models.txt`) is
aki-roadmap#130, "runtime contract v1". AkiTools' README covers conversion: bone maps, `--space model`, `--weld`,
textures and `validate`. A pack must say where its model came from and under what license (`pack.json`). Don't share
models you don't have the rights to.

## For developers: the code

- `src/core.c`: the shared part. It loads packs and moves them into the game's free memory, reads `models.txt` and
  the Mods-menu options, and builds two display lists per replaced slot (one per frame parity).
- `src/adapter.h`: what a game adapter provides: its slots, part nodes, matrices, wrestler ids, render state and the
  hook that draws the parts.
- `src/adapters/aki_nodes.c`: the adapter for AKI's node-based wrestler renderer, shared by the games that run it.
  `src/adapters/nomercy.c` and `src/adapters/vpw2.c` each set four things and include it: the profile id, the parts
  per slot, the part emitter to hook, and (in `mod.psd1`) the addresses. `src/adapter.c` picks the game's file.
- **Adding a game that runs the same renderer** (WM2000 next): a 4-line `adapters/<game>.c` (`AN_PROFILE`,
  `AN_PARTS`, `AN_EMITTER`, `#include "aki_nodes.c"`), a line in `adapter.c`, and the game's defines in `mod.psd1`
  (`MI_ACTORS`, `MI_WRESTLER_IDS`, `MI_MEM_BASE` / `MI_MEM_SIZE`, `MI_SCRATCH`, and `MI_COSTUMES` if the game keeps
  the costume). They mirror the `runtime` block of the game's AkiTools profile. A game with a different renderer
  writes its own adapter against `adapter.h`.
- **Host exports**, which each game provides (No Mercy and VPW2: `feat/130-model-host`): `aki_log`, `aki_models_read`
  (reads only from `mods\models`, at most one folder deep) and `aki_models_list`.
- **Build:** `..\..\build.ps1 modelimport nomercy` (or `vpw2`). VPW2 packs take its part table:
  `aki-model.py convert ... --parts model-templates\profiles\vpw2.json` (AkiTools), which also makes the pack's game `vpw2`.
