# Stages built mods into the game repos so releases ship them:
#   build\<mod>\<game>\<id>.nrm  ->  ..\<Repo>\assets\mods\<id>.nrm
# Each game copies assets\mods\*.nrm into the player's mods folder at startup
# (install_bundled_mods in src/main/main.cpp), and CMake/CI already ship assets\.
# Run .\build.ps1 first; commit the staged .nrm in each game repo.
#   .\stage.ps1                 every built mod for every game
#   .\stage.ps1 cheats          one mod
#   .\stage.ps1 -RepoSuffix -fullconv   into the ..\<Repo>-fullconv checkouts instead
# Mods with Bundle = $false in mod.psd1 (developer tools) are skipped.
# A mod with Packs = '<folder>' in mod.psd1 also ships what <folder>\<game>\ holds (aki-roadmap#145): its
# <id>.akimodel model packs -> assets\mods\models\, its .rtz texture packs -> assets\mods\, and <folder>\CREDITS.md ->
# assets\mods\models\. The staged models folder is replaced as a whole, so a pack taken out of the mod goes too; a
# renamed or dropped .rtz is not removed from assets\mods (delete it there by hand).
# Before anything is copied, every <id>.akimodel under those Packs folders goes through `aki-model.py validate`
# (..\AkiTools, aki-roadmap#130), against packs\<game>\'s own game profile: a pack with errors stops the staging, so
# a broken pack can't ship. Without ..\AkiTools, or without a working Python on PATH, it's skipped with a warning.
param([string]$Mod = 'all', [string]$RepoSuffix = '')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$depot = Split-Path $root -Parent
$games = Import-PowerShellDataFile "$root\common\games.psd1"

$built = Get-ChildItem "$root\build" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'tests' -and ($Mod -eq 'all' -or $_.Name -eq $Mod) }
if (-not $built) { throw "Nothing built for '$Mod'; run .\build.ps1 first" }

# The model packs staging will copy (the same set: packs\<game>\*.akimodel for each game the mod is built for),
# grouped by game.
$packSets = @{}
foreach ($modDir in $built) {
    $modCfg = "$root\mods\$($modDir.Name)\mod.psd1"
    $cfg = if (Test-Path $modCfg) { Import-PowerShellDataFile $modCfg } else { @{} }
    if ($cfg.Bundle -eq $false -or -not $cfg.Packs) { continue }
    foreach ($gameDir in Get-ChildItem $modDir.FullName -Directory) {
        if (-not $games.ContainsKey($gameDir.Name)) { continue }
        $gamePacks = "$root\mods\$($modDir.Name)\$($cfg.Packs)\$($gameDir.Name)"
        if (-not (Test-Path $gamePacks)) { continue }
        foreach ($pack in Get-ChildItem $gamePacks -Directory -Filter *.akimodel) {
            if (-not $packSets.ContainsKey($gameDir.Name)) { $packSets[$gameDir.Name] = @() }
            $packSets[$gameDir.Name] += $pack.FullName
        }
    }
}
if ($packSets.Count) {
    $akiTools = "$depot\AkiTools"
    # The first python/py on PATH that actually starts (a Windows Store alias without Python behind it doesn't).
    $python = $null
    foreach ($cand in @(Get-Command python, py -CommandType Application -ErrorAction SilentlyContinue)) {
        $works = & {
            $ErrorActionPreference = 'Continue'
            try { $null = & $cand.Source -c 'import sys' 2>$null; $LASTEXITCODE -eq 0 } catch { $false }
        }
        if ($works) { $python = $cand; break }
    }
    if (-not (Test-Path "$akiTools\aki-model.py")) {
        Write-Warning "model packs not validated: no $akiTools\aki-model.py next door"
    } elseif (-not $python) {
        Write-Warning 'model packs not validated: no working Python (python or py) on PATH'
    } else {
        $failed = @()
        # Pack paths may hold any character: have Python write UTF-8 and read it back as UTF-8.
        $oldIo, $oldEnc = $env:PYTHONIOENCODING, [Console]::OutputEncoding
        $env:PYTHONIOENCODING = 'utf-8'
        try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }   # no console: nothing to decode
        try {
            foreach ($game in $packSets.Keys | Sort-Object) {
                $profileArg = @()
                if (Test-Path "$akiTools\model-templates\profiles\$game.json") {
                    $profileArg = @('--profile', $game)
                } else {
                    Write-Warning "$game has no aki-model profile: its packs are checked against the game model.bin was built for"
                }
                $report = @(& $python.Source "$akiTools\aki-model.py" validate @($packSets[$game]) @profileArg)
                if ($LASTEXITCODE -ne 0) {
                    $lines = @($report | Where-Object { $_ -match ': ERROR |^\s+fix: ' })
                    if ($lines.Count) {
                        $failed += $game
                        $lines | ForEach-Object { Write-Host $_ }
                    } else {
                        # It didn't get as far as judging the packs: that's the tool, not the packs.
                        $report | ForEach-Object { Write-Host $_ }
                        throw "aki-model.py validate failed (exit $LASTEXITCODE) for the $game packs; nothing staged"
                    }
                }
            }
        } finally {
            $env:PYTHONIOENCODING = $oldIo
            try { [Console]::OutputEncoding = $oldEnc } catch { }
        }
        $n = ($packSets.Values | ForEach-Object { $_ }).Count
        if ($failed) {
            throw "aki-model validate refused model packs for $($failed -join ', ') (above); nothing staged. Rebuild or fix them first."
        }
        Write-Host "model packs: $n validated by aki-model"
    }
}
foreach ($modDir in $built) {
    # Developer tools (mod.psd1 Bundle = $false, e.g. quickmatch) never ship with the games.
    $modCfg = "$root\mods\$($modDir.Name)\mod.psd1"
    $cfg = if (Test-Path $modCfg) { Import-PowerShellDataFile $modCfg } else { @{} }
    if ($cfg.Bundle -eq $false) {
        Write-Host "$($modDir.Name): not bundled (Bundle = `$false in mod.psd1)"; continue
    }
    foreach ($gameDir in Get-ChildItem $modDir.FullName -Directory) {
        if (-not $games.ContainsKey($gameDir.Name)) { continue }
        $repoDir = "$depot\$($games[$gameDir.Name].Repo)$RepoSuffix"
        if (-not (Test-Path $repoDir)) { throw "no game checkout at $repoDir" }
        $dest = "$repoDir\assets\mods"
        New-Item -ItemType Directory -Force $dest | Out-Null
        foreach ($nrm in Get-ChildItem $gameDir.FullName -Filter *.nrm) {
            Copy-Item $nrm.FullName $dest -Force
            [pscustomobject]@{ Mod = $modDir.Name; Game = $gameDir.Name; File = "$dest\$($nrm.Name)"; Length = $nrm.Length }
        }
        if (-not $cfg.Packs) { continue }
        $packs = "$root\mods\$($modDir.Name)\$($cfg.Packs)"
        $gamePacks = "$packs\$($gameDir.Name)"
        # The staged models folder mirrors packs\<game>\: replaced as a whole (and gone when the game has no packs).
        # It's staging output, so uncommitted edits there are lost: say so instead of wiping them silently.
        $models = "$dest\models"
        if (Test-Path $models) {
            $dirty = @()
            if (Get-Command git -ErrorAction SilentlyContinue) {
                try { $dirty = @(& git -C $repoDir status --porcelain -- 'assets/mods/models' 2>$null) } catch { }
            }
            if ($dirty.Count) { Write-Warning "$($gameDir.Name): replacing assets\mods\models with uncommitted changes ($($dirty.Count) paths)" }
            Remove-Item $models -Recurse -Force
        }
        if (-not (Test-Path $gamePacks)) { continue }
        New-Item -ItemType Directory $models | Out-Null
        foreach ($pack in Get-ChildItem $gamePacks -Directory -Filter *.akimodel) {
            Copy-Item $pack.FullName $models -Recurse
            $len = (Get-ChildItem $pack.FullName -File -Recurse | Measure-Object Length -Sum).Sum
            [pscustomobject]@{ Mod = $modDir.Name; Game = $gameDir.Name; File = "$models\$($pack.Name)"; Length = $len }
        }
        if (Test-Path "$packs\CREDITS.md") { Copy-Item "$packs\CREDITS.md" $models }
        foreach ($rtz in Get-ChildItem $gamePacks -File -Filter *.rtz) {
            Copy-Item $rtz.FullName $dest -Force
            [pscustomobject]@{ Mod = $modDir.Name; Game = $gameDir.Name; File = "$dest\$($rtz.Name)"; Length = $rtz.Length }
        }
    }
}
