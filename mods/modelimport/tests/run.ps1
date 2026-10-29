# Builds and runs the host-side logic tests for the modelimport mod.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
. "$root\common\tools.ps1"
$out = "$root\build\tests\modelimport"
New-Item -ItemType Directory -Force $out | Out-Null
$fail = 0
foreach ($t in 'window_test', 'restore_test') {
    & $zig cc -target x86_64-windows-gnu -O1 "$PSScriptRoot\$t.c" -o "$out\$t.exe"
    if ($LASTEXITCODE) { throw "$t build failed" }
    & "$out\$t.exe"
    if ($LASTEXITCODE) { $fail = 1 }
}
exit $fail
