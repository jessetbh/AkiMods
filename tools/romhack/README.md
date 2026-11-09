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
  `install`), `stock`, `have-it`, `needs-rom` (`needs` names the base when the patch says which;
  also a hack whose port ROM wasn't given, in `plan` as in `build`), `needs-game` (`build`: a
  texture pack or bundle that doesn't say its game; pass `--game`), `unsupported` (`why`),
  `failed` (`why`).
- **One conversion per hack in a batch** (#268): an item that builds the same ROM as an earlier
  one (a hack ROM and its patch) is `have-it` with `duplicate_of`, and the ledger records its input
  too. In an archive holding several patches, a patch the catalog doesn't know whose sibling (same
  base) matched a catalog entry is that hack's other build (VPW64 Deluxe's Emulator_over64MB
  xdelta beside the Summercart one): `have-it` with `alternative_of`, not built; imported alone
  it builds as usual.
- **Tiers:** a hack that matches the recipe catalog (`--catalog`, by SHA-1) gets that entry's id,
  name, route and tier; any other hack is `untested` (#224, #172). A built package keeps the tier
  only when it is the tested build: its patch makes the entry's `outputs.sha1`, else its
  `codes.txt` is `outputs.codes_sha1`, else (textures + save mods, no patch or codes) its saves and
  texture images hash to `outputs.content_sha1` (SHA-1 of the sorted `<path> NUL <file SHA-1> LF`
  lines for `conversion/saves/*` and the images under `conversion/textures/`, not `rt64.json`).
- **Patches** are applied with `patchfmt.py` in this folder (`detect(patch)`,
  `apply(patch, source) -> (target, info)`, `PatchError`). Every given stock ROM is tried as the
  base, port ROMs first; stock ROMs among the inputs count too. `.rup` and PPF files are refused
  by name (a Project64 bundle's `.rup` is applied by import-pj64).
- **Ids and names** come from the catalog, else from the file or folder name, made unique
  against the ledger. A build the catalog matches by what it built (its output, codes or content
  hash; entries without `inputs.sha1` such as nmp_v21, starrcade, wmx64) takes the entry's id and
  name too, with a `-2` suffix when the ledger or batch already holds that id (#268). After a
  build, `tier` is the built package's tier (the same as `aki_tier`).
- **The ledger** keys every input by content hash (a ROM in `.z64` order, a patch file, a
  folder's files), so a rerun reports `have-it` instead of building again. `plan` only reads it;
  `--record` (worker: `"record": true`) adds the items made ready.
- **Archives:** zip members are read on demand (nothing is extracted); members over 128 MB or
  inflating more than 100:1 are refused. 7z needs `7zr.exe` (`bin\7zr.exe` here, PATH, or
  `--sevenzip`) and unpacks into `--work` (keyed by the archive's content), or into a temp folder
  deleted after the batch. RAR: extract first.
- **Texture packs** stay one item however deep their folders go. An archive in a folder of the
  pack's own images doesn't split the pack (#268): it becomes its own item (on disk), or a
  `unsupported` note when it holds nothing to import alone (or sits in a zipped pack). An archive
  anywhere else makes the folder a folder of items, as ROMs, patches and mods do.
- **OS clutter** (`Thumbs.db`, `desktop.ini`, `.DS_Store`, `._*` AppleDouble files, `__MACOSX\`)
  is skipped without a line (#268).
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
| hack ROM, or a patch applied to its base | `aki_hack.survey_rom` against the port's ROM: `patch-data` / `patch-code` -> `make_patch_mod`; `rev0-layout` / `relinked` / `restructured` -> `derelink` (also when make_patch_mod refuses); `different-base` refused unless the catalog has a derelink recipe for that exact ROM (9 Underground) | conversion: `conversion/aki.json` + `conversion/patch.nrm` (its mod id is the conversion id), + `codes.txt` when derelink writes a code watch table |
| Project64 bundle (folder or zip), or a textures + save mod (a texture pack that names its ROM, such as a GLideN64 `WWF No Mercy_HIRESTEXTURES.hts`, beside a save named after the same ROM, and no ROM, patch or archive: N64Modderz64's WWF No Mercy MOD) | `aki_conv` import-pj64 with the item's id and name; the game from the bundle's cheat sections or a ROM in it, else the ROM name the textures and save share, else `game`; the save becomes the conversion's seed save | conversion (codes, saves, docs, patch, and the textures inside it at `conversion/textures/`: the game reads a conversion's textures from its own folder, while a texture-pack package installs to `mods\`) |
| texture pack (rt64, Rice, Jabo, `.hts`) | images copied, an `rt64.json` added for Rice/Jabo (`autoPath`), `.hts` exported to Rice PNGs | texture-pack (`payload.folder: textures/`) |

- Conversions are built against the port's ROM, so a build needs it (`--rom`, `--rom-dir`, or
  among the inputs): without it a hack is `needs-rom`.
- A hack the catalog doesn't know is labelled **Untested** (`survey.aki_tier: "untested"` in
  `aki.json`, and the package's `short_description` says so). A catalog entry can set `converter`.
- `make_patch_mod` needs function tables to tell in-place hacks apart. Developers pass `--repo
  GAME=<game repo>` (its `RecompiledFuncs`); without tables the survey can't tell, and derelink
  (which has a layout for all six games) builds the hack.
- **Codes for another revision** (a No Mercy bundle's Rev 0 cheats; the port runs Rev 1) are
  translated the way the hand-made packs were: `import-pj64 --map` with the Rev 0 derelink map
  and `--map-range 200800,400000,-1000` (a catalog entry's `map_range` list overrides it), which
  also runs `translate-code-values` when a derelink layout matches the port's ROM. Then derelink's
  watch codes join them. The map ships as `maps\nomercy-rev0.json` (address ranges and shifts
  only, no ROM bytes; made with `derelink.py map --base <port ROM> --hack <stock No Mercy (USA) 1.0>`);
  without it one is made once per batch with `derelink.py map` from a stock No Mercy (USA)
  1.0 ROM: the player's (`--rom-dir`, or among the inputs) or one inside the bundle. Without one,
  the codes keep their Rev 0 tag and stay off. Each item says what happened in `codes` (for example
  `codes translated from Rev 0 (41 of 44 lines; 3 dropped); 12 values inside the code they write
  translated`, or `codes for Rev 0 not translated: needs a stock ... ROM ...; they stay off`), with
  the numbers in `codes_translation`, and `watch_codes` / `watch_codes_note` when a patch was built.
- The tools run in the one worker process; a hacked ROM is on disk only while derelink reads it.
- `.nrm` mods and existing packages stay `ready` (`route: install`): the launcher installs them as
  they are.

### The converter's tools

All of them live here (owner decision on #228: the player-facing converter is in AkiMods):
`derelink.py` (+ `layouts/`, `mips.py`, `bps.py`; from the No Mercy repo's `tools/romhack`),
`aki_hack.py` (survey), `aki_conv.py` + `aki_conv_bake.py` (import-pj64, pack-nrm, bake-codes,
translate-codes, translate-code-values; with `aki_conv_values.py`) and `aki_save.py` (save
conversion; from AkiTools). They find each other in this folder; `AKI_DEPOT` points the
developer-only lookups (stock ROMs and RecompiledFuncs in game repos) at a depot.

These are the only copies. AkiTools' `aki-conv.py`, `aki-hack.py`, `aki_conv_bake.py` and
`aki_conv_values.py` are thin shims that run or import the ones here (AkiTools `aki_romhack.py`
finds this checkout), so change the tools here, and their tests in `tests/`.

`translate-code-values` fixes the addresses inside the code a translated GameShark list writes
(aki-roadmap#205): hooks, `lui`/low pairs and pointers to what the list wrote, through a derelink
map (`derelink.py map --base <port ROM> --hack <old revision's ROM> --out map.json`):

```powershell
python tools\romhack\aki_conv.py translate-code-values <pack>\codes.txt --map map.json `
    --port-rom ..\NoMercyRecomp\nomercy.z64 [--map-range LO,HI,DELTA] [--move-writes LO,HI,DELTA] [--dry-run]
```

Only values known to be instructions change. It tags the file and refuses to run twice; read the
report (`unmapped`, `lui_conflicts` and `stepped_bases` are left alone). `--map-range` overrides
the map for a list translated with an older one; `--move-writes` moves what a `stepped_bases` `lui`
points at. Run the pack after any move. import-pj64 never takes a known stock cartridge in a bundle
for the hack, and adds derelink's code-watch list to the pack's codes (aki-roadmap#207).

Stdlib only. It never writes ROM bytes; tests use synthetic images
(`python -m unittest discover -s tools/romhack/tests`).

## Python on demand (pyfetch.ps1, aki-roadmap#228)

Players usually have no Python, so the launcher runs `pyfetch.ps1` the first time an import needs
it. It is PowerShell because it must run before there is a Python, and Windows PowerShell 5.1
ships with every Windows 10 and 11:

```powershell
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\romhack\pyfetch.ps1
```

It downloads python.org's embeddable zip for Python 3.12.10 amd64 (the last 3.12 release with
Windows binaries), checks it against the SHA-256 pinned in the script, unpacks it to a temp folder
and renames that to `%LOCALAPPDATA%\AkiLauncher\python\3.12.10` only once it is complete and
`python.exe` has answered with its version. Later runs reuse it without touching the network. It
prints one JSON line: `{"ok":true,"python":"...\python.exe","version":"3.12.10","fetched":true}`
or `{"ok":false,"error":"<kind>","message":"<player-readable>"}` with kind `no-network`,
`download-failed`, `bad-hash`, `unpack-failed`, `not-writable`, `not-installed` (with
`-NoDownload`, which only checks) or `failed`. Exit code 0 / 1.

The embeddable Python ignores `PYTHONPATH` and the script's folder (its `._pth` file), which is
fine for `aki_import.py`: it loads `patchfmt.py` and `unpack.py` by path.

## Safe archive extraction (unpack.py, aki-roadmap#228)

`unpack.extract(archive, dest, max_total=4 GB, sevenzip=None)` unpacks a zip (zipfile) or a 7z
(7-Zip's `7z.exe`, `7za.exe` or `7zr.exe` from `sevenzip`, PATH or the 7-Zip install folder;
7-Zip isn't bundled, and without it the error says to install it). Every name is checked before
anything is written: absolute paths, `..`, drive letters, `:` and Windows device names are
refused. The total is capped on the bytes actually written (zip: counted while inflating; 7z: the
folder is watched while 7-Zip runs and checked at the end), so an archive that lies about its
sizes is stopped part way. It unpacks into `dest.part-*` beside `dest` and renames it only on
success; on any failure nothing is left. Errors are `UnpackError` with `kind` `bad-path`,
`too-big`, `need-7zip`, `damaged`, `exists` or `not-writable`. `aki_import.py` uses it for 7z
archives when it is there (job key `max_unpack` sets the cap). Command line:
`python -I unpack.py ARCHIVE DEST [--max-mb N]` prints one JSON line.
