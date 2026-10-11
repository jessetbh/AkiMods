# Stages built mods into the game repos so releases ship them:
#   build\<mod>\<game>\<id>.nrm  ->  ..\<Repo>\assets\mods\<id>.nrm
# Each game copies assets\mods\*.nrm into the player's mods folder at startup
# (install_bundled_mods in src/main/main.cpp), and CMake/CI already ship assets\.
# Run .\build.ps1 first; commit the staged .nrm in each game repo.
#   .\stage.ps1                 every built mod for every game
#   .\stage.ps1 cheats          one mod
# Mods with Bundle = $false in mod.psd1 (developer tools) are skipped.
param([string]$Mod = 'all')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$depot = Split-Path $root -Parent
$games = Import-PowerShellDataFile "$root\common\games.psd1"

$built = Get-ChildItem "$root\build" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'tests' -and ($Mod -eq 'all' -or $_.Name -eq $Mod) }
if (-not $built) { throw "Nothing built for '$Mod'; run .\build.ps1 first" }
foreach ($modDir in $built) {
    # Developer tools (mod.psd1 Bundle = $false, e.g. quickmatch) never ship with the games.
    $modCfg = "$root\mods\$($modDir.Name)\mod.psd1"
    if ((Test-Path $modCfg) -and ((Import-PowerShellDataFile $modCfg).Bundle -eq $false)) {
        Write-Host "$($modDir.Name): not bundled (Bundle = `$false in mod.psd1)"; continue
    }
    foreach ($gameDir in Get-ChildItem $modDir.FullName -Directory) {
        if (-not $games.ContainsKey($gameDir.Name)) { continue }
        $dest = "$depot\$($games[$gameDir.Name].Repo)\assets\mods"
        New-Item -ItemType Directory -Force $dest | Out-Null
        foreach ($nrm in Get-ChildItem $gameDir.FullName -Filter *.nrm) {
            Copy-Item $nrm.FullName $dest -Force
            [pscustomobject]@{ Mod = $modDir.Name; Game = $gameDir.Name; File = "$dest\$($nrm.Name)"; Length = $nrm.Length }
        }
    }
}
