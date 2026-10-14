# Builds and runs the host-side parser test for the cheats mod.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
. "$root\common\tools.ps1"
$out = "$root\build\tests\cheats"
New-Item -ItemType Directory -Force $out | Out-Null
& $zig cc -target x86_64-windows-gnu -O1 -Wno-int-to-pointer-cast -I "$PSScriptRoot" -I "$root\common\include" "$PSScriptRoot\parse_test.c" -o "$out\parse_test.exe"
if ($LASTEXITCODE) { throw 'test build failed' }
& "$out\parse_test.exe"
exit $LASTEXITCODE
