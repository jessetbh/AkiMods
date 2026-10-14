# Finds the build tools. Dot-sourced by build.ps1 and the tests. Each tool comes from, in order:
#   1. an environment variable (AKI_ZIG, AKI_LLD, AKI_RECOMPMODTOOL, AKI_MINGW_BIN),
#   2. local-tools.ps1 in the repo root (gitignored; set $zig / $lld / $modTool / $mingwBin there),
#   3. PATH, or the default location noted below.
$toolsRoot = Split-Path $PSScriptRoot -Parent
$zig = $null; $lld = $null; $modTool = $null; $mingwBin = $null
if (Test-Path "$toolsRoot\local-tools.ps1") { . "$toolsRoot\local-tools.ps1" }

function Resolve-AkiTool([string]$EnvName, [string]$Current, [string]$Command, [string]$Default) {
    $fromEnv = [Environment]::GetEnvironmentVariable($EnvName)
    if ($fromEnv) { return $fromEnv }
    if ($Current) { return $Current }
    $onPath = Get-Command $Command -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    if ($Default -and (Test-Path $Default)) { return $Default }
    throw "Can't find ${Command}: put it on PATH or set $EnvName (see README.md, Building)."
}

# MIPS cross-compiler: zig cc (https://ziglang.org). Visual Studio's clang and the llvm.org Windows builds lack a
# MIPS backend.
$zig = Resolve-AkiTool 'AKI_ZIG' $zig 'zig' $null
# ld.lld: any LLVM linker works (MIPS support is built in). Visual Studio's copy is the default.
$lld = Resolve-AkiTool 'AKI_LLD' $lld 'ld.lld' 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\Llvm\x64\bin\ld.lld.exe'
# RecompModTool from N64Recomp (https://github.com/N64Recomp/N64Recomp); default: an N64Recomp build next to this repo.
$modTool = Resolve-AkiTool 'AKI_RECOMPMODTOOL' $modTool 'RecompModTool' "$(Split-Path $toolsRoot -Parent)\N64Recomp\build\RecompModTool.exe"
# A MinGW build of RecompModTool needs MinGW's runtime DLLs: set AKI_MINGW_BIN (or $mingwBin) to MinGW's bin folder.
$mingwFromEnv = [Environment]::GetEnvironmentVariable('AKI_MINGW_BIN')
if ($mingwFromEnv) { $mingwBin = $mingwFromEnv }
if ($mingwBin) { $env:PATH = "$mingwBin;$env:PATH" }
