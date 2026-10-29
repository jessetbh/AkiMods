# Builds AKI family mods: one .nrm per (mod, game).
#   .\build.ps1                    every mod for every game it supports
#   .\build.ps1 cheats             one mod, all its games
#   .\build.ps1 cheats nomercy     one mod, one game
#   .\build.ps1 camera nomercy -Define CAMERA_DEBUG    extra -D flags (debug builds)
# Output: build\<mod>\<game>\<mod id>.nrm
param([string]$Mod = 'all', [string]$Game = 'all', [string[]]$Define = @())
# Native tools print warnings on stderr; failures are caught via $LASTEXITCODE below.
$ErrorActionPreference = 'Continue'

$root = $PSScriptRoot
# The game repos (for their symbol files): next to this repo, or wherever AKI_GAMES_DIR points.
$depot = if ($env:AKI_GAMES_DIR) { $env:AKI_GAMES_DIR } else { Split-Path $root -Parent }
$games = Import-PowerShellDataFile "$root\common\games.psd1"

$modNames = @(Get-ChildItem "$root\mods" -Directory | Where-Object { Test-Path "$($_.FullName)\mod.psd1" } | ForEach-Object Name)
if ($Mod -ne 'all' -and $Mod -notin $modNames) { throw "Unknown mod '$Mod' (known: $($modNames -join ', '), all)" }
if ($Game -ne 'all' -and -not $games.ContainsKey($Game)) { throw "Unknown game '$Game' (known: $($games.Keys -join ', '), all)" }
$selectedMods = if ($Mod -eq 'all') { $modNames } else { @($Mod) }

# Tools: zig cc (MIPS), ld.lld and RecompModTool, found by common\tools.ps1.
. "$root\common\tools.ps1"

# No odd single-precision FPRs: in FR=0 an odd single is the high half of a double pair, and
# the mod recompiler mis-handles it (garbage floats). zig cc ignores -mno-odd-spreg, so the
# feature goes straight to clang.
$baseFlags = @('-target', 'mips-freestanding-none', '-mcpu=mips2', '-mabi=32', '-O2', '-G0', '-mno-abicalls',
    '-mno-odd-spreg', '-Xclang', '-target-feature', '-Xclang', '+nooddspreg', '-mno-check-zero-division', '-fomit-frame-pointer', '-fno-builtin', '-nostdinc',
    '-ffreestanding', '-ffunction-sections', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Werror=section',
    '-I', "$root\common\include")

foreach ($m in $selectedMods) {
    $modDir = "$root\mods\$m"
    $cfg = Import-PowerShellDataFile "$modDir\mod.psd1"
    $modGames = if ($cfg.Games -eq 'all') { @($games.Keys) } else { @($cfg.Games) }
    $targetGames = if ($Game -eq 'all') { $modGames } elseif ($Game -in $modGames) { @($Game) } else { @() }
    if ($targetGames.Count -eq 0) { Write-Warning "$m doesn't support $Game; skipped"; continue }

    foreach ($g in ($targetGames | Sort-Object)) {
        $info = $games[$g] # not $game: PowerShell names are case-insensitive, and [string]$Game would coerce it
        $over = if ($cfg.PerGame -and $cfg.PerGame.ContainsKey($g)) { $cfg.PerGame[$g] } else { @{} }
        $out = "$root\build\$m\$g"
        New-Item -ItemType Directory -Force $out | Out-Null

        # Optional per-mod generator: writes headers into $out (on the include path) and
        # returns extra manifest values.
        $generated = @{}
        if (Test-Path "$modDir\generate.ps1") {
            $generated = & "$modDir\generate.ps1" -Game $g -Overrides $over -OutDir $out
            if (-not ($generated -is [hashtable])) { throw "${m}/${g}: generate.ps1 must return a hashtable" }
        }

        # Compile each source with the game's frame hook and AKI_GAME_<KEY> defined.
        $flags = $baseFlags + @('-I', "$modDir\include", '-I', $out, "-DAKI_FRAME_HOOK=$($info.FrameHook)",
            "-DAKI_GAME_$($g.ToUpper())=1") + @($over.Defines | Where-Object { $_ }) + @($Define | ForEach-Object { "-D$_" })
        $objects = @(foreach ($src in $cfg.Sources) {
            $obj = "$out\$([IO.Path]::GetFileNameWithoutExtension($src)).o"
            # Out-Host: compiler output must not leak into $objects.
            & $zig cc @flags -c "$modDir\$src" -o $obj 2>&1 | Where-Object { "$_" -notmatch 'small-data accesses' } | Out-Host
            if ($LASTEXITCODE) { throw "${m}/${g}: compiling $src failed" }
            $obj
        }) # @(): one source would otherwise be a bare string, which splats char by char
        & $lld @objects -nostdlib -T "$root\common\mod.ld" -Map "$out\mod.map" `
            --unresolved-symbols=ignore-all --emit-relocs -e 0 --no-nmagic -gc-sections -o "$out\mod.elf"
        if ($LASTEXITCODE) { throw "${m}/${g}: link failed" }

        # Render the manifest: built-in values, then the mod's, per-game overrides, generated.
        $values = @{
            MOD_ID     = $cfg.Id
            GAME_ID    = $info.GameId
            GAME_TITLE = $info.Title
            SYMS       = ("$depot\$($info.Repo)\$($info.Syms)" -replace '\\', '/') # TOML: no bare backslashes
        }
        foreach ($src in @($cfg.Values, $over.Values, $generated)) { if ($src) { foreach ($k in $src.Keys) { $values[$k] = $src[$k] } } }
        $toml = Get-Content "$modDir\mod.template.toml" -Raw
        # Twice: a generated value may itself contain placeholders (e.g. {{GAME_TITLE}} in a description).
        foreach ($pass in 1..2) { foreach ($k in $values.Keys) { $toml = $toml.Replace("{{$k}}", "$($values[$k])") } }
        $toml = [regex]::Replace($toml, '\{\{[A-Z0-9_]+\}\}', '') # unset placeholders render empty
        [IO.File]::WriteAllText("$out\mod.toml", $toml) # no BOM: RecompModTool's TOML parser rejects one

        & $modTool "$out\mod.toml" $out
        if ($LASTEXITCODE) { throw "${m}/${g}: RecompModTool failed" }
        if ($cfg.ContainsKey('EnabledByDefault')) {
            # RecompModTool doesn't write the manifest's enabled_by_default (librecomp: true when absent), so it goes
            # into the packed mod.json here. False: a new install of the mod stays off until the player turns it on.
            Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
            $flag = if ($cfg.EnabledByDefault) { 'true' } else { 'false' }
            $zip = [IO.Compression.ZipFile]::Open("$out\$($cfg.Id).nrm", 'Update')
            try {
                $entry = $zip.GetEntry('mod.json')
                $reader = New-Object IO.StreamReader($entry.Open())
                $json = $reader.ReadToEnd(); $reader.Dispose()
                $stamp = $entry.LastWriteTime   # RecompModTool's; a fixed new one would also do, it just mustn't be now
                if ($json -match '"enabled_by_default"') { throw "${m}/${g}: mod.json already has enabled_by_default" }
                if ($json -notmatch '^\s*\{\s*"') { throw "${m}/${g}: mod.json doesn't start like RecompModTool's ({ `"key`" ...)" }
                $json = $json -replace '^\s*\{', "{`n    `"enabled_by_default`" : $flag,"
                $check = $json | ConvertFrom-Json
                if ($check.enabled_by_default -ne [bool]$cfg.EnabledByDefault) { throw "${m}/${g}: enabled_by_default didn't land in mod.json" }
                $entry.Delete()
                $new = $zip.CreateEntry('mod.json')
                $new.LastWriteTime = $stamp   # RecompModTool's time, like its other entries (it stamps them all with the build time)
                $writer = New-Object IO.StreamWriter($new.Open(), (New-Object Text.UTF8Encoding $false))
                $writer.Write($json); $writer.Dispose()
            }
            finally { $zip.Dispose() }
        }
        Get-Item "$out\$($cfg.Id).nrm" | Select-Object @{n = 'Mod'; e = { $m } }, @{n = 'Game'; e = { $g } }, Length, FullName
    }
}
