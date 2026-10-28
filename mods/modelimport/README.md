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

## Find a wrestler's id

Turn on **Log wrestler ids** in the Mods menu and start a match. The log (stderr, or `<Game>Recompiled.log`) prints
one line per slot, such as `slot 1: wrestler 0302`. Write that id as `0x0302` in `models.txt`.

On No Mercy the costume isn't decoded yet, so a `costume` line never matches there. Leave the costume out. On VPW2
the costume is the value the game builds the slot with (`func_80014730`); its numbering isn't checked against the
costume menu yet.

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
- `src/adapters/nomercy.c`, `src/adapters/vpw2.c`: the game adapters. `src/adapter.c` picks the adapter for the game being built.
- **Adding a game:** write `adapters/<game>.c` against `adapter.h`, add it to `adapter.c`, and add the game's
  defines to `mod.psd1`. They mirror the `runtime` block of the game's AkiTools profile.
- **Host exports**, which each game provides (No Mercy and VPW2: `feat/130-model-host`): `aki_log`, `aki_models_read`
  (reads only from `mods\models`, at most one folder deep) and `aki_models_list`.
- **Build:** `..\..\build.ps1 modelimport nomercy`.
