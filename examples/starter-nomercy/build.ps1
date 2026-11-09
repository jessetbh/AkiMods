# AKI Starter Kit (No Mercy): builds the example conversion from source, checks it, and optionally installs and runs it.
# Because it touches every part of the conversion format, it doubles as the format's regression test.
#
#   .\build.ps1 -BlankSave <blank save>                       build + validate into AkiMods\build\starter-nomercy
#   .\build.ps1 -BlankSave <...> -GameDir <game checkout>     ... then install into <game checkout>\conversions
#   .\build.ps1 -BlankSave <...> -GameDir <...> -Run          ... then a short muted test run (AkiTools aki-run.ps1)
#                                                             and the per-part check of its log
#
# -BlankSave: a No Mercy save from YOUR game, before you played (start the game once, quit, take
#   saves\wwf.nomercy.us.bin). The seed save is made from it: save files hold ROM-built data, so the kit never ships one.
#   Without it the conversion is built without its saves component (and -Run's check reports the save missing).
# -Inputs <file>: build another conversion from the same kit (kit.py's --inputs: id, name, slots, textures, ...).
#   Keep such a file outside the repo when it names private files. Its output goes to build\<id> unless -Out is given.
# Needs: the AkiMods build tools (README.md, Building), Python 3 with Pillow, and for -Run AkiTools next to AkiMods and
# a portable game checkout (portable.txt in it; aki-run.ps1 -InitPortable makes one) that isn't the main checkout
# <depot>\NoMercyRecomp (aki-run refuses that one). -GameDir %LOCALAPPDATA%\NoMercyRecompiled works for install only.
param(
    [string]$BlankSave = '',
    [string]$GameDir = '',
    [switch]$Run,
    [int]$Seconds = 75,
    [string]$Out = '',
    [string]$Inputs = ''
)
$ErrorActionPreference = 'Stop'
$kit = $PSScriptRoot
$akimods = Split-Path (Split-Path $kit -Parent) -Parent
if ($GameDir) { $GameDir = (Resolve-Path $GameDir).Path }
if ($BlankSave) { $BlankSave = (Resolve-Path $BlankSave).Path }
$py = if ($env:AKI_PYTHON) { $env:AKI_PYTHON } else { 'python' }
$convId = 'aki_starter_kit'
$in = @{}
if ($Inputs) {
    $Inputs = (Resolve-Path $Inputs).Path
    $j = Get-Content $Inputs -Raw | ConvertFrom-Json
    if ($j.id) { $convId = $j.id }
    foreach ($p in 'patch', 'rt') { $in[$p] = -not ($j.PSObject.Properties.Name -contains $p -and $j.$p -eq $false) }
}
else { $in = @{ patch = $true; rt = $true } }
if (-not $Out) { $Out = Join-Path $akimods $(if ($convId -eq 'aki_starter_kit') { 'build\starter-nomercy' } else { "build\$convId" }) }
$inArgs = if ($Inputs) { @('--inputs', $Inputs) } else { @() }

# 1. The code change: patch\ is an ordinary AkiMods mod folder, built by AkiMods build.ps1 (zig cc + RecompModTool).
#    Its mod id must be the conversion id: for another id the folder is copied with mod.psd1's Id changed.
$nrm = $null
if ($in.patch) {
    $modsDir = $kit
    if ($convId -ne 'aki_starter_kit') {
        $modsDir = Join-Path $akimods "build\_kit-src-$convId"
        Remove-Item $modsDir -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $kit 'patch') (Join-Path $modsDir 'patch') -Recurse
        $psd = Join-Path $modsDir 'patch\mod.psd1'
        [IO.File]::WriteAllText($psd, ([IO.File]::ReadAllText($psd) -replace "Id\s*=\s*'aki_starter_kit'", "Id      = '$convId'"))
    }
    & (Join-Path $akimods 'build.ps1') -ModsDir $modsDir -Mod patch -Game nomercy | Out-Host
    $nrm = Join-Path $akimods "build\_external\patch\nomercy\$convId.nrm"
    if (-not (Test-Path $nrm)) { throw "the patch didn't build ($nrm)" }
}

# 2. Everything else: textures, texture package, models, seed save, aki.json.
$assemble = @('assemble', '--out', $Out) + $inArgs
if ($nrm) { $assemble += @('--nrm', $nrm) }
if ($BlankSave) { $assemble += @('--blank-save', $BlankSave) }
& $py (Join-Path $kit 'kit.py') @assemble
if ($LASTEXITCODE) { throw 'kit.py assemble failed' }

# 3. The format checks (ours + AkiTools aki-package validate).
& $py (Join-Path $kit 'kit.py') validate --out $Out @inArgs
if ($LASTEXITCODE) { throw 'kit.py validate failed' }

if (-not $GameDir) { return }

# 4. Install the way the launcher does, including the two steps only the launcher does today: the seed save goes into
#    saves\<id>\ (once), and the test run below sets WCW_RT_LIGHTS_FILE.
& $py (Join-Path $kit 'kit.py') install --out $Out --game-dir $GameDir @inArgs
if ($LASTEXITCODE) { throw 'kit.py install failed' }

if (-not $Run) { return }

# 5. A short muted, windowed run with no input: the kit's code change starts the attract demo in Raw by itself.
$aki = Join-Path (Split-Path $akimods -Parent) 'AkiTools'
$vars = @{ AKI_CONVERSION = $convId }
if ($in.rt) { $vars.WCW_RT_LIGHTS_FILE = Join-Path $GameDir "conversions\$convId\rt_lights.json" }
$label = $convId -replace '_', '-'
$before = @(Get-ChildItem (Join-Path $aki 'runs') -Directory -ErrorAction SilentlyContinue | ForEach-Object Name)
# Muted: the game's sound.json gets main_volume 0 for the run and its old contents back afterwards.
$sound = Join-Path $GameDir 'sound.json'
$soundOld = if (Test-Path $sound) { [IO.File]::ReadAllBytes($sound) } else { $null }
[IO.File]::WriteAllText($sound, "{`n    `"main_volume`": 0`n}`n")
try {
    & (Join-Path $aki 'aki-run.ps1') -Worktree $GameDir -Game nomercy -Label $label -Windowed -Seconds $Seconds `
        -Preset rt -ShotSec 30 -ShotCount 6 -ShotEvery 300 -Vars $vars | Out-Host
}
finally {
    if ($null -eq $soundOld) { Remove-Item $sound -ErrorAction SilentlyContinue } else { [IO.File]::WriteAllBytes($sound, $soundOld) }
}
$runDir = Get-ChildItem (Join-Path $aki 'runs') -Directory | Where-Object { $_.Name -notin $before -and $_.Name -like "*$label*" } |
    Sort-Object Name | Select-Object -Last 1
if (-not $runDir) { throw 'no aki-run output folder found' }
& $py (Join-Path $kit 'kit.py') check --run $runDir.FullName --game-dir $GameDir @inArgs
if ($LASTEXITCODE) { throw "the run's check failed ($($runDir.FullName))" }
