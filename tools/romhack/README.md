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

## Moved data and the size limit

Tools such as VPW Studio move data when a replaced file grows or shrinks: every later file and
the file table slide. The patch handles that with BPS SourceCopy: a moved run is copied from
where it sits in the player's stock ROM, so it costs a few bytes, not a copy of the run. Only
bytes the stock ROM doesn't have at all go into the patch as they are (TargetRead); the tool
prints how many (`patch: ... new (literal)`).

If that would be more than 2 MB, the tool refuses to write the mod, since the mod could then be
carrying ROM data (often because `--base` isn't the ROM the hack was made from). If the hack
really adds that much data of its own, raise the limit with `--max-target-read <MB>`.
See [aki-roadmap#74](https://github.com/jessetbh/aki-roadmap/issues/74).

Only one ROM-patch mod can be enabled at a time. On the `wcw-romhack` runtime a patch mod's
saves go to `saves\<mod id>\`, apart from the stock game's.

Patch mods can contain the hack's and the original game's data, so **don't share patch mods you
don't have the rights to**: make your own from your own ROMs.

## Tests

```powershell
python -m unittest discover -s tools\romhack\tests
```

## Importing downloads (aki_import.py, aki-roadmap#228)

`aki_import.py` is the converter's engine: it takes a pile of downloads (ROMs in any byte order,
patch files, `.nrm` mods, AKI packages, Project64 bundles, texture packs, zips, 7z archives) and
says what each item is, which game it is for, and (`build`) builds it into a package the
launcher installs.

```powershell
python -I tools\romhack\aki_import.py plan C:\Downloads\wrestling --rom nomercy=nomercy.z64 `
    --rom-dir C:\ROMs --ledger import-ledger.json --json results.json
python -I tools\romhack\aki_import.py build C:\Downloads\wrestling --rom nomercy=nomercy.z64 `
    --out Converted --ledger Converted\import-ledger.json
```

- **Status per item:** `ready` (with a `route`: `survey` for hack ROMs, which `aki-hack survey`
  sends to make_patch_mod or derelink; `import-pj64`; `wrap-rice` / `wrap-jabo` / `hts-export` /
  `install`), `stock`, `have-it`, `needs-rom` (`needs` names the base when the patch says which),
  `unsupported` (`why`), `failed` (`why`).
- **Tiers:** a hack that matches the recipe catalog (`--catalog`, by SHA-1) gets that entry's id,
  name, route and tier; any other hack is `untested` (#224, #172).
- **Patches** are applied with `patchfmt.py` in this folder (`detect(patch)`,
  `apply(patch, source) -> (target, info)`, `PatchError`). Every given stock ROM is tried as the
  base, port ROMs first; stock ROMs among the inputs count too. Until `patchfmt.py` lands,
  `patchfmt_standin.py` stands in (BPS, IPS and UPS; xdelta, APS, PPF and .rup are refused by name).
- **Ids and names** come from the catalog, else from the file or folder name, made unique
  against the ledger.
- **The ledger** keys every input by content hash (a ROM in `.z64` order, a patch file, a
  folder's files), so a rerun reports `have-it` instead of building again. `plan` only reads it;
  `--record` (worker: `"record": true`) adds the items made ready.
- **Archives:** zip members are read on demand (nothing is extracted); members over 128 MB or
  inflating more than 100:1 are refused. 7z needs `7zr.exe` (`bin\7zr.exe` here, PATH, or
  `--sevenzip`) and unpacks into `--work` (keyed by the archive's content), or into a temp folder
  deleted after the batch. RAR: extract first.
- **Untrusted input:** one bad file fails alone; links and junctions aren't followed; files are
  read whole only when they look like a ROM, patch or mod, and their bytes are dropped after each
  item.
- **`worker`** is the launcher's entry point: one process per batch (process starts are
  expensive on some PCs, aki-roadmap#111). It reads one JSON job on stdin (`paths`, `roms` or
  `rom_dirs`, `ledger`, `record`, `catalog`, `sevenzip`, `work`; for a build also `build`, `out`,
  `game`, `version`, `authors`) and writes one JSON line per item, then a `done` line with counts.

### Building (`build`)

Every `ready` item becomes a #154 package (`aki-package.json`, the shape the launcher's package
installer takes) in `<out>\<game>\<id>-<version>.zip`; the item's status becomes `converted`
with `package`, `converter` and `aki_tier`:

| Input | Converter | Package |
|---|---|---|
| hack ROM, or a patch applied to its base | `aki_hack.survey_rom` against the port's ROM: `patch-data` / `patch-code` -> `make_patch_mod`; `rev0-layout` / `relinked` / `restructured` -> `derelink` (also when make_patch_mod refuses); `different-base` refused | conversion: `conversion/aki.json` + `conversion/patch.nrm` (its mod id is the conversion id), + `codes.txt` when derelink writes a code watch table |
| Project64 bundle (folder or zip) | `aki_conv` import-pj64 with the item's id and name; the game from the bundle's cheat sections or a ROM in it, else `game` | conversion (codes, saves, docs, patch) + a texture-pack package it requires |
| texture pack (rt64, Rice, Jabo, `.hts`) | images copied, an `rt64.json` added for Rice/Jabo (`autoPath`), `.hts` exported to Rice PNGs | texture-pack (`payload.folder: textures/`) |

- Conversions are built against the port's ROM, so a build needs it (`--rom`, `--rom-dir`, or
  among the inputs): without it a hack is `needs-rom`.
- A hack the catalog doesn't know is labelled **Untested** (`survey.aki_tier: "untested"` in
  `aki.json`, and the package's `short_description` says so). A catalog entry can set `converter`.
- `make_patch_mod` needs function tables to tell in-place hacks apart. Developers pass `--repo
  GAME=<game repo>` (its `RecompiledFuncs`); without tables the survey can't tell, and derelink
  (which has a layout for all six games) builds the hack.
- The tools run in the one worker process; a hacked ROM is on disk only while derelink reads it.
- `.nrm` mods and existing packages stay `ready` (`route: install`): the launcher installs them as
  they are.

### The converter's tools

All of them live here (owner decision on #228: the player-facing converter is in AkiMods):
`derelink.py` (+ `layouts/`, `mips.py`, `bps.py`; from the No Mercy repo's `tools/romhack`),
`aki_hack.py` (survey), `aki_conv.py` + `aki_conv_bake.py` (import-pj64, pack-nrm, bake-codes,
translate-codes) and `aki_save.py` (save conversion; from AkiTools). They find each other in this
folder; `AKI_DEPOT` points the developer-only lookups (stock ROMs and RecompiledFuncs in game
repos) at a depot.

Stdlib only. It never writes ROM bytes; tests use synthetic images
(`python -m unittest discover -s tools/romhack/tests`).
