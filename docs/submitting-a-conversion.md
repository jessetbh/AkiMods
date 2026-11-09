# Submitting a conversion to the catalog

The converter (`tools/romhack/aki_import.py`) labels every conversion it builds with a **tier**: Verified,
Experimental or Untested. The tier comes from the catalog, `tools/romhack/catalog.json`: a list of the conversions
the AKI team has tested. This page says how a hack gets into it.

The catalog holds hashes and notes only. It never holds ROM data, patch data or anyone's files.

## Tiers

| Tier | Meaning |
|---|---|
| `verified` | Plays through a match like the original hack does on an emulator. |
| `experimental` | Runs, with known problems (listed in the entry's `known_issues`). |
| `unsupported` | Known not to work. |
| `untested` | Not in the catalog, or built differently from the tested build. Every build the catalog doesn't match gets this. |

**The maintainers decide the tier.** A submission proposes one; the maintainers test the build themselves before an
entry goes in.

## How a build matches an entry

An entry looks like this (the hashes are made up):

```json
{
  "id": "my_hack",
  "name": "My Hack",
  "game": "nomercy",
  "version": "1.2.0",
  "tier": "verified",
  "converter": "derelink",
  "known_issues": ["The attract demo never starts"],
  "note": "No attract demo: press Start.",
  "inputs":  { "sha1": ["0123456789ABCDEF0123456789ABCDEF01234567"] },
  "outputs": { "sha1": ["89ABCDEF0123456789ABCDEF0123456789ABCDEF"],
               "codes_sha1": ["FEDCBA9876543210FEDCBA9876543210FEDCBA98"] }
}
```

The converter matches it in two steps.

**When it reads the input** (`plan`, and the start of `build`), it looks the input up in `inputs.sha1`: the SHA-1 of a
hack ROM (in `.z64` byte order), of a patch file, or of the ROM a patch makes. A match gives the item the entry's
id, name and a provisional tier, and can pick the converter (`converter`, for example `derelink` for a hack the survey
would otherwise refuse).

**When it has built the package**, it decides the tier from what it built, not from the input:

1. A conversion with a patch: the converter applies the patch to the port's ROM and hashes the result. It must be in
   `outputs.sha1`. When the entry also lists `outputs.codes_sha1`, the conversion's `codes.txt` must hash to one of
   those too.
2. No patch, but codes: `codes.txt` must hash to an entry's `outputs.codes_sha1`, that entry must have no
   `outputs.sha1`, and no other entry may list the same codes hash.
3. No patch and no codes (a textures + save mod): the saves and texture images must hash to `outputs.content_sha1`
   (SHA-1 of the sorted lines `<path> NUL <file SHA-1> LF` for every file under `conversion/saves/` and every image
   under `conversion/textures/`, leaving out the `rt64.json` the converter writes), and the entry must have no
   `outputs.sha1` or `outputs.codes_sha1`.
4. An entry that lists `outputs.content_sha1` matches only when the content hash matches too, whatever else did.
5. The entry's `game` must be the game the conversion was built for.

A match gives the build the entry's tier, and its id, name, `known_issues` and `note`. Anything else is
**Untested**. When the input was a known hack but the build came out different (a newer converter, another version of
the hack), the conversion is Untested with a note that says so.

A build without an `inputs.sha1` match can still match by its output. That is how entries with no `inputs` work:
the tested hack reaches the converter in more than one shape (a ROM, a patch, a bundle), and only the result is
pinned.

Other entry keys: `version` (the hack's version), `converter` (`make_patch_mod`, `derelink` or `import-pj64`), and
recipe keys for Project64 bundles (`map_range`, `include`, `bake_exclude`, `all_cheats`, `cheat_name`) that only
maintainers set. `tier_proposed: true` marks a tier the maintainers haven't confirmed yet; the converter doesn't read
it.

Source of truth: `tools/romhack/aki_import.py` (`Catalog`, `classify_rom`, `classify_patch`, `Builder.judge`,
`Builder.content_sha1`, `Builder.catalog_identity`, `Builder.write_conversion`), `tools/romhack/catalog.json`
(`about`, `tiers`).

## What to test

Test the package the converter built, installed in the game, not the hack in an emulator alone.

1. **It boots.** The game starts as the conversion and reaches the title screen and the menus.
2. **It plays a match.** Start one and play it to the end (a pin or a submission), with more than one wrestler type
   if the hack changes the roster.
3. **The hack's own features work.** New wrestlers, moves, arenas, menus, text, music: whatever the hack's readme
   promises. Try its game modes, not just exhibition.
4. **Compare it with a reference emulator.** Play the same thing in an emulator the hack was made for (Project64 or
   ares, for most hacks) and note every difference you see.
5. **Saves.** Save in the conversion, quit, start it again, and check the save is there and the normal game's saves
   are untouched.
6. **Read the log.** The game's log (`<exe name>.log` in its data folder) lists what the conversion loaded
   (`[conversion]` lines) and the functions the patch changed (`[romhack]` lines). Note any errors.

## What to send

Open an issue at [github.com/jessetbh/AkiMods/issues](https://github.com/jessetbh/AkiMods/issues) with:

- the hack's name, version and author;
- a **public link** to where you downloaded it (the author's page, a forum post, a public archive);
- the **SHA-1 of each input file** you gave the converter. `aki_import.py plan <folder> --json plan.json` writes it
  for every item (`sha1`); or in PowerShell, `Get-FileHash -Algorithm SHA1 <file>` (for a ROM file this only
  matches when it is already in `.z64` byte order);
- the build's hashes: run `build` with `--json build.json` and copy `output_sha1`, `codes_sha1` and `content_sha1`
  (whichever are there), `converter` and `aki_tier` from the hack's line, and the AkiMods commit you built with;
- the game and its version;
- your test notes: what you tested, what works, what differs from the emulator, and the tier you propose.

Never send a ROM, a patched ROM, a built package or a save, and never attach the hack's files: the link is enough.
Only submit hacks whose authors share them publicly.
