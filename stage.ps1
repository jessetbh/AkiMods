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
# assets\mods\models\. The staged models folder is replaced as a whole, so a pack taken out of the mod goes too.
param([string]$Mod = 'all', [string]$RepoSuffix = '')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$depot = Split-Path $root -Parent
$games = Import-PowerShellDataFile "$root\common\games.psd1"

$built = Get-ChildItem "$root\build" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'tests' -and ($Mod -eq 'all' -or $_.Name -eq $Mod) }
if (-not $built) { throw "Nothing built for '$Mod'; run .\build.ps1 first" }
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
        if (-not (Test-Path $gamePacks)) { continue }
        $models = "$dest\models"
        if (Test-Path $models) { Remove-Item $models -Recurse -Force }
        New-Item -ItemType Directory $models | Out-Null
        foreach ($pack in Get-ChildItem $gamePacks -Directory -Filter *.akimodel) {
            Copy-Item $pack.FullName $models -Recurse
            $len = (Get-ChildItem $pack.FullName -File | Measure-Object Length -Sum).Sum
            [pscustomobject]@{ Mod = $modDir.Name; Game = $gameDir.Name; File = "$models\$($pack.Name)"; Length = $len }
        }
        if (Test-Path "$packs\CREDITS.md") { Copy-Item "$packs\CREDITS.md" $models }
        foreach ($rtz in Get-ChildItem $gamePacks -File -Filter *.rtz) {
            Copy-Item $rtz.FullName $dest -Force
            [pscustomobject]@{ Mod = $modDir.Name; Game = $gameDir.Name; File = "$dest\$($rtz.Name)"; Length = $rtz.Length }
        }
    }
}
