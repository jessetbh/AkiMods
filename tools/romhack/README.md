# ROM hacks as patch mods (make_patch_mod.py)

A ROM hack can ship as a regular recomp mod (`.nrm`) that holds only a BPS patch against the
stock ROM. Players install it like any other mod; the runtime applies the patch to their own
ROM when the game starts. No ROM data is distributed.

This tool is shared by all six AKI ports. Each game keeps a thin `tools/romhack/make_patch_mod.py`
that forwards here, so you run it the same way from any game's repository root (it reads that
game's `RecompiledFuncs/`):

```powershell
python tools\romhack\make_patch_mod.py --base vpw2.z64 --hack my_hack.z64 `
    --game-id vpw2 --id my_hack --name "My Hack" --version 1.0.0 --author me `
    --out my_hack.nrm
```

The game's wrapper finds this file through `AKIMODS_DIR` (an AkiMods checkout) or a sibling
`..\AkiMods` folder. Both ROMs must be big-endian `.z64`. Without `--out` the tool only prints
the report.

## The report

It lists every recompiled function the hack changes, read from the section and function tables
in `RecompiledFuncs/recomp_overlays.inl`:

- **Data-only** (no changed functions): works on any runtime with ROM-patch mods.
- **Changed functions**: the `wcw-romhack` runtime recompiles them from the patched ROM with the
  live recompiler at load time (`[romhack]` lines in stderr list them).
- **REPAIRED**: a changed function the game's post-recompile pipeline (or a hand-edit) changed,
  listed in `RecompiledFuncs/repaired_functions.txt` (`tools/romhack/list_repaired_functions.py`
  in the game). The live recompiler has none of those repairs, so test such a hack closely. The
  game also registers that list with the runtime, which logs a `[romhack] ... was repaired by the
  port offline` line for each one at load (or refuses the mod, if the game asks it to).
- **Changed bytes outside functions**, or **ESCAPES** lines (a changed function branches or calls
  somewhere that is neither inside it nor a known function start): the hack added code or
  restructured code across function boundaries. Patch mods can't run that, so the tool won't
  write the mod (`--force` overrides); it needs a full recompile of the hacked ROM.

Only one ROM-patch mod can be enabled at a time. On the `wcw-romhack` runtime a patch mod's
saves go to `saves\<mod id>\`, apart from the stock game's.

Patch mods can contain the hack's and the original game's data, so **don't share patch mods you
don't have the rights to**: make your own from your own ROMs.

## Tests

```powershell
python -m unittest discover -s tools\romhack\tests
```
