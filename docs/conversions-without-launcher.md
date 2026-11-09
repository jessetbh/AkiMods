# Playing old conversions without AKI Launcher

A **conversion** turns one of the six AKI recomps into a different game: a ROM hack, a set of GameShark codes, saves
and textures that play together, with saves of their own. AKI Launcher normally builds and installs conversions for
you. This guide does the same by hand, with the converter in this repository and the game's own menus.

It covers:

- ROM hacks (a hacked ROM file),
- patches (`.xdelta`, `.bps`, `.ips`, `.ups`, `.aps`),
- Project64 bundles (a Project64 folder or zip that holds a mod's cheats, save and textures),
- texture packs (Rice, Jabo, GLideN64 `.hts`, or rt64),
- texture + save mods (a texture pack and a save named after the same ROM, with no ROM or patch).

Nothing here sends or shares ROM data. The converter works on your own files, on your own PC.

## What you need

1. **The game.** One of the six recomps, started once, with your ROM imported (the game asks for it on the first
   start). The ports need these exact releases:

   | Game | Release | Game key |
   |---|---|---|
   | [WCW vs. nWo World Tour: Recompiled](https://github.com/jessetbh/WCWvsNWOWorldTourRecomp) | USA | `wt` |
   | [WCW/nWo Revenge: Recompiled](https://github.com/jessetbh/WCWnWoRevengeRecomp) | USA | `revenge` |
   | [WWF WrestleMania 2000: Recompiled](https://github.com/jessetbh/WWFWrestleMania2000Recomp) | USA | `wm2k` |
   | [Virtual Pro Wrestling 64: Recompiled](https://github.com/jessetbh/VPW64Recomp) | Japan | `vpw64` |
   | [WWF No Mercy: Recompiled](https://github.com/jessetbh/WWFNoMercyRecomp) | USA, Rev 1 | `nomercy` |
   | [Virtual Pro Wrestling 2: Recompiled](https://github.com/jessetbh/VPW2Recomp) | Japan | `vpw2` |

   Conversions need a game build from 0.3.0 on.
2. **Your stock ROM** of that game, as a file (`.z64`, `.v64` or `.n64`; any byte order). Some patches were made
   for a different stock ROM (a VPW64 hack made on VPW2, or an old No Mercy hack made on the first USA release).
   Keep those stock ROMs in the same folder.
3. **The hack's files**, as you downloaded them. Zip and 7z archives are fine; you don't need to unpack them.
4. **The converter**: download this repository (on [GitHub](https://github.com/jessetbh/AkiMods), **Code**,
   **Download ZIP**) and unpack it, for example to `C:\AkiMods`. The converter is the `tools\romhack` folder.
5. **Python 3**, one time. Either:
   - install it from [python.org](https://www.python.org/downloads/windows/) (tick "Add python.exe to PATH"), or
   - let the converter's own script fetch the same Python AKI Launcher uses (3.12.10, about 11 MB, checked against a
     pinned hash). In PowerShell:

     ```powershell
     powershell -NoProfile -ExecutionPolicy Bypass -File C:\AkiMods\tools\romhack\pyfetch.ps1
     ```

     It prints one line such as `{"ok":true,"python":"C:\\Users\\you\\AppData\\Local\\AkiLauncher\\python\\3.12.10\\python.exe",...}`.
     Use that `python.exe` path wherever this guide says `python`. A second run finds it again without downloading.
6. **7-Zip**, only for `.7z` archives: install it from [7-zip.org](https://www.7-zip.org/). The converter finds a
   normal install by itself. RAR archives can't be read: extract them with 7-Zip first (right-click, **7-Zip**,
   **Extract to**) and use the folder that makes.

## Step 1: put the files in folders

Make two folders:

- `C:\Hacks\in`: the hack's downloads (archives, patches, bundles, texture packs). One hack or many, as you like.
- `C:\ROMs`: your stock ROMs.

Any folder names work. The converter only reads them.

## Step 2: see what the converter makes of them

Open PowerShell and run:

```powershell
python -I C:\AkiMods\tools\romhack\aki_import.py plan C:\Hacks\in --rom-dir C:\ROMs
```

`plan` changes nothing. It prints one line per item: its status, the game, what kind of file it is, its tier (see
[Tiers](#tiers)), a detail, and the file in brackets. The statuses:

| Status | Meaning |
|---|---|
| `ready` | The converter knows what to do with it. |
| `stock` | An unmodified game ROM: nothing to convert. It can still serve as the base for a patch. |
| `needs-rom` | A patch whose stock ROM isn't in `C:\ROMs`, or a hack whose game ROM isn't there. The line says which ROM. |
| `needs-game` | A texture pack, bundle or patch that doesn't say which game it is for. Add `--game <key>` in Step 3. |
| `have-it` | Already converted (see `--ledger` below), or the same hack twice in this batch. |
| `unsupported` | Not one of the six games, another region, or a format the converter can't read. The line says why. |
| `failed` | Something went wrong with that one file. The line says what. |

`--rom-dir` searches the folder and its subfolders for stock ROMs. You can also name one ROM with
`--rom nomercy=C:\ROMs\nomercy.z64` (the game key from the table above); it is used only when it is exactly the
release the port needs.

## Step 3: build the conversions

```powershell
python -I C:\AkiMods\tools\romhack\aki_import.py build C:\Hacks\in --rom-dir C:\ROMs --out C:\Hacks\Converted
```

Every `ready` item becomes a zip in `C:\Hacks\Converted\<game key>\<id>-1.0.0.zip`, and its line now says
`converted`. A hack, patch, bundle or texture + save mod becomes a conversion. A texture pack on its own becomes a
texture pack for the game's Mods menu.

Useful options:

- `--game <key>`: the game for texture packs and bundles that don't say.
- `--ledger C:\Hacks\Converted\import-ledger.json`: remembers what was built, so a second run over the same folder
  says `have-it` instead of building again. Delete that file to build everything again.
- `--version 1.2.0` and `--author "Name"`: the version and authors the packages show (the version must be `x.y.z`).
- `--json results.json`: writes every detail of the run to a file, for a bug report or a catalog submission.

## Step 4: install into the game

The game keeps conversions in its data folder:

| Game | Data folder |
|---|---|
| World Tour | `%LOCALAPPDATA%\WCWRecompiled\` |
| Revenge | `%LOCALAPPDATA%\RevengeRecompiled\` |
| WrestleMania 2000 | `%LOCALAPPDATA%\Wm2kRecompiled\` |
| VPW64 | `%LOCALAPPDATA%\Vpw64Recompiled\` |
| No Mercy | `%LOCALAPPDATA%\NoMercyRecompiled\` |
| VPW2 | `%LOCALAPPDATA%\Vpw2Recompiled\` |

In **portable mode** (a `portable.txt` in the folder you start the game from, normally the folder that holds the
exe), the data folder is that folder instead.

Install a zip from Step 3 in any one of these ways:

- **From the game's menu.** Start the game but don't start playing yet. Open the Mods menu, press **Install Mods**
  and pick the zip. Dragging the zip onto the game window does the same. (Mods can't be installed once a game is
  running; restart it first.)
- **With the import folder.** Make a folder named `import` in the data folder, put the zip in it, and start the game.
  The game installs it and removes it from `import`. One it refuses moves to `import\not-installed\`, with a `.txt`
  beside it that says why.
- **By hand.** Unzip it. Copy its `conversion` folder to `<data folder>\conversions\` and rename the copy to the
  conversion's id (the `id` in its `aki.json`, also the start of the zip's name). The folder name must be the id
  exactly. This way works for conversions only; a texture pack zip goes through the menu or the import folder.

The game refuses a conversion made for a different ROM than the one you imported.

`.nrm` mods and packages that hold an `aki-package.json` need no converting: install them the same way.

## Step 5: pick it and play

1. In the game's Mods menu, press **Conversions**.
2. Press **Choose** next to the conversion. **Off** goes back to the normal game.
3. Restart the game. It starts as the conversion from now on, until you choose another one or Off.

Or start it for one run from PowerShell, with the `AKI_CONVERSION` environment variable set to the conversion's id:

```powershell
cd "C:\Games\WWF No Mercy Recompiled"
$env:AKI_CONVERSION = 'my-hack'
.\NoMercyRecompiled.exe
```

This wins over the Mods menu's choice. A conversion that can't run this way (a wrong id, not installed, a broken
`aki.json`) stops the game with a message, and your normal saves are not touched. A broken one chosen in the Mods menu
is dropped instead, and the game starts normally.

A conversion keeps its saves in `<data folder>\saves\<id>\`, apart from the normal game's. If the conversion comes
with a save (most Project64 bundles do), the game copies it there on the first start, and never over a save that is
already there. While a conversion runs, your other mods are off, except the ones the conversion allows (texture packs
you turned on, by default).

## Tiers

Every conversion the converter builds shows a tier:

| Tier | What it means for you |
|---|---|
| **Verified** | The AKI team built this exact conversion and played a match in it; it played like the original hack on an emulator. |
| **Experimental** | It runs, with known problems. The conversion's notes say which. |
| **Untested** | Nobody on the AKI team has tested this build. It may work fine, or it may crash. |

The tier comes from the converter's catalog of tested conversions (`tools\romhack\catalog.json`). A conversion
gets a Verified or Experimental tier only when what you built is byte for byte what was tested. A hack the catalog
doesn't know, another version of a known hack, or a build that came out different, is Untested. Untested doesn't mean
broken; it means unknown. To get a hack tested, see [Submitting a conversion](submitting-a-conversion.md).

## What you miss without the launcher

The game does almost all of the work itself: it applies the patch, runs the codes, seeds the saves, loads the
textures, the conversion's own models and its ray-tracing lights. Without the launcher you don't get:

- the automatic Python download (use `pyfetch.ps1`, above);
- the launcher's notes under each conversion (a missing shared texture pack, codes that won't run, a game too old for
  the conversion);
- the launcher's extra checks when it installs a conversion: the game's own installer doesn't look inside the
  conversion's `aki.json`. If a conversion shows "Can't be played" in the Conversions list, the line after it says
  what is wrong.

A conversion's ray-tracing **materials** file isn't used by anything yet, with or without the launcher.

## Troubleshooting

| You see | What to do |
|---|---|
| `needs-rom ... needs the <game> ROM the port uses` | Put your stock ROM of that game in the `--rom-dir` folder. It must be the release in the table under [What you need](#what-you-need). |
| `needs-rom ... it does not apply to: ...` | The patch was made for a different stock ROM than the ones you have. The hack's readme usually says which (for example "No Mercy (USA) 1.0"). Add that ROM to `C:\ROMs`. |
| `unsupported ... a RAR archive` | Extract it with 7-Zip, then import the folder it made. |
| `unsupported ... a 7z archive, and 7-Zip is needed` | Install 7-Zip, or point at its `7z.exe` with `--sevenzip "C:\Program Files\7-Zip\7z.exe"`. |
| `unsupported ... PAL version of ...` (or Japanese) | The hack is for another region's release. The ports run only the releases in the table above. |
| `unsupported ... is not one of the six AKI games` | The hack is for another game. |
| `unsupported ... rup patches are not read` (or ppf) | These formats aren't supported. A Project64 bundle's own `.rup` is fine: import the whole bundle folder. |
| `needs-game` | Run `build` again with `--game <key>`. |
| `failed ... different-base: ...` | The hack was made on another release or revision of the game, which the port can't run. |
| The tier is Untested | Expected for any hack the AKI team hasn't tested. Try it; report what you find. |
| The game says a conversion "needs ... your ROM is a different one" | The conversion was built against another ROM. Import the release the port needs in the game, then build again. |
| "import it in the game first" | Start the game once and import your ROM before installing conversions. |
| Codes don't seem to do anything | Some GameShark codes change the game's own code rather than its data, and some of those do nothing in a recomp. Check the game's log for the `[conversion] codes:` line, which counts the codes it runs and skips. |

**The game's log.** Started from Explorer, the game writes its log to a file named after its exe in its data folder,
for example `%LOCALAPPDATA%\NoMercyRecompiled\NoMercyRecompiled.log`. Start it with `--show-console` to watch the
same lines in a console window. The conversion's lines start with `[conversion]`.

For the details of every file in a conversion, see [the conversion format](conversion-format.md).
