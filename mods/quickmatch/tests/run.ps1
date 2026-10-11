# Builds and runs the host-side logic tests for the quickmatch mod.
$ErrorActionPreference = 'Continue'
$zig = 'C:\Users\selki\toolchains\zig-windows-x86_64-0.14.0\zig.exe'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
$out = "$root\build\tests\quickmatch"
New-Item -ItemType Directory -Force $out | Out-Null
& $zig cc -target x86_64-windows-gnu -O1 "$PSScriptRoot\logic_test.c" -o "$out\logic_test.exe"
if ($LASTEXITCODE) { throw 'test build failed' }
& "$out\logic_test.exe"
exit $LASTEXITCODE
