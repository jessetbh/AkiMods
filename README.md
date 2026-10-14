# AKI Mods

Recomp mods (`.nrm`) for the AKI wrestling recompilation family. Each mod is written once and built for all
six games:

- [WCW vs. nWo World Tour: Recompiled](https://github.com/jessetbh/WCWvsNWOWorldTourRecomp)
- [WCW/nWo Revenge: Recompiled](https://github.com/jessetbh/WCWnWoRevengeRecomp)
- [WWF WrestleMania 2000: Recompiled](https://github.com/jessetbh/WWFWrestleMania2000Recomp)
- [Virtual Pro Wrestling 64: Recompiled](https://github.com/jessetbh/VPW64Recomp)
- [WWF No Mercy: Recompiled](https://github.com/jessetbh/WWFNoMercyRecomp)
- [Virtual Pro Wrestling 2: Recompiled](https://github.com/jessetbh/VPW2Recomp)

**Players don't need this repository**: every game release already includes these mods (Settings → Mods). It's
here for anyone who wants to see how they work, change them, or write their own.

| Mod | What it does |
|---|---|
| [camera](mods/camera/README.md) | Keyboard and mouse free camera: F6, mouse look, wheel in/out, WASD, Space/Ctrl up/down, Shift for speed |
| [cheats](mods/cheats/README.md) | Curated cheat toggles per game, plus a box for any GameShark code |
| [quickmatch](mods/quickmatch/README.md) | Testing tool: skips the intro and starts a CPU-vs-CPU match in a chosen venue within seconds (off by default) |

## Building

You need:

- **Windows** with PowerShell 5.1 or later (the build scripts are PowerShell).
- [**zig**](https://ziglang.org/download/) (0.14 or later), for `zig cc`: a C compiler with a MIPS backend. The
  Clang that ships with Visual Studio and the llvm.org Windows builds have no MIPS backend.
- **`ld.lld`**: any LLVM linker. Visual Studio's copy (C++ Clang tools) is used if it isn't on PATH.
- **`RecompModTool`** from [N64Recomp](https://github.com/N64Recomp/N64Recomp). If yours is a MinGW build, it needs
  MinGW's runtime DLLs: set `AKI_MINGW_BIN` to MinGW's `bin` folder.
- **The game repositories** you build for, cloned next to this one (folder names as in `common/games.psd1`:
  `WcwNwoWorldTour`, `WcwRevengeRecomp`, `Wm2kRecomp`, `Vpw64Recomp`, `NoMercyRecomp`, `Vpw2Recomp`), or set
  `AKI_GAMES_DIR` to the folder that holds them. Only their symbol files are read (`syms/dump.toml`, or the
  `WCWSyms` submodule for World Tour), so no ROM and no game build is needed.

The tools are found through `common/tools.ps1`: an environment variable (`AKI_ZIG`, `AKI_LLD`,
`AKI_RECOMPMODTOOL`, `AKI_MINGW_BIN`), then an optional `local-tools.ps1` in the repo root (gitignored; set
`$zig`, `$lld`, `$modTool` or `$mingwBin` there), then PATH.

```powershell
.\build.ps1                    # every mod for every game it supports
.\build.ps1 cheats             # one mod, all its games
.\build.ps1 cheats nomercy     # one mod, one game
.\mods\cheats\tests\run.ps1    # a mod's host-side tests (where it has them)
```

Output: `build\<mod>\<game>\<mod id>.nrm`. Game keys: `wt`, `revenge`, `wm2k`, `vpw64`, `nomercy`, `vpw2`.

To try a build, copy the `.nrm` into the game's mods folder (`%LOCALAPPDATA%\<Game>Recompiled\mods\`, or `mods\`
next to the exe in portable mode). A copy with the same file name as a bundled mod is replaced by the game's own on
the next start, so give a modified mod its own id (in `mod.psd1`) while you test it.

## Writing a mod

1. Create `mods/<name>/` with `mod.psd1`, `mod.template.toml` and `src/` (copying `cheats` is a good start).
2. Include `aki_mod.h`. Every build defines:
   - `AKI_FRAME_HOOK`, so `AKI_ON_FRAME void f(void) { ... }` runs at least once per frame on every game;
   - `AKI_GAME_<KEY>` (e.g. `AKI_GAME_NOMERCY`) for game-specific branches.
3. To hook or patch other game functions, use `RECOMP_HOOK("func_...")` / `RECOMP_PATCH` from `modding.h`. The
   games have no decompilation, so functions go by address, and they differ per game: put them behind
   `AKI_GAME_<KEY>` or pass them in through `PerGame.Defines` in `mod.psd1`. Each game repository's
   `syms/dump.toml` and `disasm/` are the map.
4. Optional `generate.ps1` (`-Game`, `-Overrides`, `-OutDir`): runs before compiling; can write headers into
   `-OutDir` (it's on the include path) and returns a hashtable of extra manifest values. `cheats` uses it to turn
   its per-game presets into a header and options.
5. The manifest template gets `{{MOD_ID}}`, `{{GAME_ID}}`, `{{GAME_TITLE}}` and `{{SYMS}}` automatically; anything
   else comes from `Values` / `PerGame.Values` in `mod.psd1`. Unset placeholders render empty.

Things to know:

- Mods only get the runtime's exports: config options (`recomp_get_config_*`, see `recomp_api.h`) and the games'
  keyboard, mouse and right-stick input (`recomp_input.h`). There's no `recomp_printf` (Zelda's game code provides
  it, the AKI ports don't), and importing a function the runtime doesn't have stops the mod from loading.
- The runtime switches on every newly installed mod, so a mod meant for others should do nothing until the player
  turns it on in its options.
- Floating point: the build passes `+nooddspreg` (no odd single-precision registers), because the mod recompiler
  mishandles them. Keep it if you compile by hand.

## Adding a game

Add it to `common/games.psd1`. Its `FrameHook` is the one function in the game that calls both
`osViGetCurrentFramebuffer` and `osViGetNextFramebuffer`.

## Shipping with the games

`.\stage.ps1` copies each built `.nrm` into `<game repo>\assets\mods\`, where it's committed: the game's build copies
`assets\` next to the exe, so every release and AKI Launcher install carries it. At startup each game's
`install_bundled_mods()` (`src/main/main.cpp`) copies `assets\mods\*.nrm` into the player's mods folder when missing
or changed.

## Layout

```
build.ps1               builds every (mod, game) pair
stage.ps1               copies built .nrm files into the game repos' assets\mods\
common/
  games.psd1            per-game facts: repo, mod game id, symbol file, frame hook
  tools.ps1             finds zig, ld.lld and RecompModTool
  include/aki_mod.h     shared types, AKI_ON_FRAME
  include/modding.h     RECOMP_HOOK / RECOMP_PATCH / ... (from MMRecompModTemplate, CC0)
  include/recomp_api.h  runtime imports (config options)
  include/recomp_input.h  keyboard, mouse and right-stick imports
  mod.ld                linker script
mods/<name>/
  mod.psd1              id, sources, supported games, per-game overrides
  mod.template.toml     manifest, rendered per game
  src/                  the mod's C code
  tests/                optional host-side tests
tools/romhack/
  make_patch_mod.py     packages a ROM hack as a patch mod (run from a game repo; see its README)
  tests/                python -m unittest discover -s tools/romhack/tests
```

## License

GPL-3.0, like the games (see [COPYING](COPYING)), except `common/include/modding.h`, which comes from the
[MMRecompModTemplate](https://github.com/Zelda64Recomp/MMRecompModTemplate) under CC0.
