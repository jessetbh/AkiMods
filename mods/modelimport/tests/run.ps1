# Builds and runs the host-side logic tests for the modelimport mod.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
. "$root\common\tools.ps1"
$out = "$root\build\tests\modelimport"
New-Item -ItemType Directory -Force $out | Out-Null
& $zig cc -target x86_64-windows-gnu -O1 "$PSScriptRoot\window_test.c" -o "$out\window_test.exe"
if ($LASTEXITCODE) { throw 'test build failed' }
& "$out\window_test.exe"
exit $LASTEXITCODE
