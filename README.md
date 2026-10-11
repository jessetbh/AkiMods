# AKI Mods

Generic recomp mods (`.nrm`) for the whole AKI family: World Tour, Revenge,
WrestleMania 2000, VPW64, No Mercy and VPW2. Each mod is written once and built for
every game.

| Mod | What it does |
|---|---|
| [cheats](mods/cheats/README.md) | Curated cheat toggles per game, plus any GameShark code |
| [camera](mods/camera/README.md) | Keyboard + mouse free camera: F6, mouse look, wheel in/out, W/S up/down, A/D sideways (all six games) |
| [quickmatch](mods/quickmatch/README.md) | Testing tool: skip the intro and start the demo match in a chosen venue within seconds (No Mercy so far; bundled, off by default) |

## Build

```powershell
.\build.ps1                    # every mod for every game it supports
.\build.ps1 cheats             # one mod, all its games
.\build.ps1 cheats nomercy     # one mod, one game
```

Output: `build\<mod>\<game>\<mod id>.nrm`. Game keys: `wt`, `revenge`, `wm2k`, `vpw64`,
`nomercy`, `vpw2`.

Toolchain: `zig cc` for MIPS (VS clang and the llvm.org builds in `toolchains\` have no
MIPS backend), VS `ld.lld`, and `..\N64Recomp\build\RecompModTool.exe` (needs MinGW on
PATH; `build.ps1` adds it). Function symbols come from each game repo's `dump.toml`, so
the game repos must sit next to this one.

## Shipping with the games

`.\stage.ps1` copies each built `.nrm` into `..\<Repo>\assets\mods\`. Commit it there: CMake
copies `assets\` next to the exe and CI zips it, so every GitHub release and AKI Launcher
install carries it. At startup each game's `install_bundled_mods()` (`src/main/main.cpp`)
copies `assets\mods\*.nrm` into `%LOCALAPPDATA%\<Game>Recompiled\mods\` when missing or
changed. The runtime enables newly found mods by default (RecompModTool leaves the manifest's
`enabled_by_default` at true), so a bundled mod must do nothing until configured: cheats
has every toggle off, camera defaults to the game's own camera.

For local testing, copying the `.nrm` into that mods folder directly works too.

## Layout

```
build.ps1               builds every (mod, game) pair
stage.ps1               copies built .nrm files into the game repos' assets\mods\
common/
  games.psd1            per-game facts: repo, mod game id, symbol file, frame hook
  include/aki_mod.h     shared types, AKI_ON_FRAME
  include/modding.h     RECOMP_HOOK / RECOMP_PATCH / ... (MMRecompModTemplate, CC0)
  include/recomp_api.h  runtime imports (config options)
  mod.ld                linker script
mods/<name>/
  mod.psd1              id, sources, supported games, per-game overrides
  mod.template.toml     manifest, rendered per game
  src/                  the mod's C code
  tests/                optional host-side tests
```

## Writing a mod

1. Create `mods/<name>/` with `mod.psd1`, `mod.template.toml` and `src/` (copy `cheats`).
2. Include `aki_mod.h`. Every build defines:
   - `AKI_FRAME_HOOK`, so `AKI_ON_FRAME void f(void) { ... }` runs at least once per frame
     on every game;
   - `AKI_GAME_<KEY>` (e.g. `AKI_GAME_NOMERCY`) for game-specific branches.
3. To hook or patch other game functions, use `RECOMP_HOOK("func_...")` /
   `RECOMP_PATCH` from `modding.h`. Function names and addresses differ per game, so put
   them behind `AKI_GAME_<KEY>` or pass them in through `PerGame.Defines` in `mod.psd1`.
4. Optional `generate.ps1` (`-Game`, `-Overrides`, `-OutDir`): runs before compiling; can write
   headers into `-OutDir` (it's on the include path) and returns a hashtable of extra
   manifest values. `cheats` uses it to turn its per-game presets into a header + options.
5. The manifest template gets `{{MOD_ID}}`, `{{GAME_ID}}`, `{{GAME_TITLE}}` and `{{SYMS}}`
   automatically; anything else comes from `Values` / `PerGame.Values` in `mod.psd1`.
   Unset placeholders render empty.

Mods only get the runtime's exports. Notably there's no `recomp_printf` (Zelda's game code
provides it, the AKI ports don't), and importing a missing function stops the mod loading.

## Adding a game

Add it to `common/games.psd1`. Its `FrameHook` is the one function in the game that calls
both `osViGetCurrentFramebuffer` and `osViGetNextFramebuffer`.
