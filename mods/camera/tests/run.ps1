# Builds and runs the host-side math test for the camera mod.
$ErrorActionPreference = 'Continue'
$zig = 'C:\Users\selki\toolchains\zig-windows-x86_64-0.14.0\zig.exe'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
$out = "$root\build\tests\camera"
New-Item -ItemType Directory -Force $out | Out-Null
& $zig cc -target x86_64-windows-gnu -O1 -Wno-int-to-pointer-cast -I "$root\common\include" "$PSScriptRoot\view_test.c" -o "$out\view_test.exe"
if ($LASTEXITCODE) { throw 'test build failed' }
& "$out\view_test.exe"
exit $LASTEXITCODE
