# Packages the built mods as AKI packages (aki-package.json, aki-roadmap#154): one zip per mod and game, plus one per
# game for a mod's model packs, in dist\<game>\. Players install them with the launcher's or the game's Install, or by
# dropping them into the game's import folder. The manifests come from each mod's own mod.json (pack.json for model
# packs) through ..\AkiTools\aki-package.py (package --from), which also validates them; a package with errors stops
# the run.
#   .\build.ps1; .\package.ps1                 every built mod
#   .\package.ps1 -Mod camera                   one mod
# Developer tools (mod.psd1 Bundle = $false, e.g. quickmatch) are packaged only with -IncludeDev.
param([string]$Mod = 'all', [switch]$IncludeDev)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$depot = Split-Path $root -Parent
$tool = Join-Path $depot 'AkiTools\aki-package.py'
if (-not (Test-Path $tool)) { throw "no $tool (AkiTools next to AkiMods)" }
$python = @(Get-Command python, py -CommandType Application -ErrorAction SilentlyContinue)[0]
if (-not $python) { throw 'no python or py on PATH' }
$games = Import-PowerShellDataFile "$root\common\games.psd1"

$built = Get-ChildItem "$root\build" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'tests' -and ($Mod -eq 'all' -or $_.Name -eq $Mod) }
if (-not $built) { throw "Nothing built for '$Mod'; run .\build.ps1 first" }

$failed = 0
function Package([string]$Source, [string]$Out, [string]$GameKey, [string[]]$Extra) {
    New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
    $cmdArgs = @($tool, 'package', $Source, '--from', '-o', $Out, '--force', '--game', $GameKey, '--license', 'GPL-3.0-only') + $Extra
    $text = & $python.Source @cmdArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ge 2) {
        Write-Host $text.Trim()
        $script:failed++
        return
    }
    [pscustomobject]@{ Game = $GameKey; Package = (Split-Path $Out -Leaf); Warnings = ($LASTEXITCODE -eq 1) }
}

foreach ($modDir in $built) {
    $modCfg = "$root\mods\$($modDir.Name)\mod.psd1"
    $cfg = if (Test-Path $modCfg) { Import-PowerShellDataFile $modCfg } else { @{} }
    if ($cfg.Bundle -eq $false -and -not $IncludeDev) {
        Write-Host "$($modDir.Name): developer tool, not packaged (-IncludeDev to package it)"
        continue
    }
    foreach ($gameDir in Get-ChildItem $modDir.FullName -Directory) {
        if (-not $games.ContainsKey($gameDir.Name)) { continue }
        foreach ($nrm in Get-ChildItem $gameDir.FullName -Filter *.nrm) {
            Package $nrm.FullName "$root\dist\$($gameDir.Name)\$($nrm.BaseName).zip" $gameDir.Name @()
        }
        if ($cfg.Packs) {
            $packs = "$root\mods\$($modDir.Name)\$($cfg.Packs)\$($gameDir.Name)"
            if (Test-Path $packs) {
                # A folder of <id>.akimodel packs becomes one model-pack package for the game.
                Package $packs "$root\dist\$($gameDir.Name)\$($modDir.Name)-packs.zip" $gameDir.Name @('--id', "$($modDir.Name)-packs")
            }
        }
    }
}
if ($failed) { throw "$failed package(s) had errors (above); fix them and run again" }
Write-Host "packages: $((Get-ChildItem "$root\dist" -Recurse -Filter *.zip).Count) in dist"
exit 0
