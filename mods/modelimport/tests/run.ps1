# Builds and runs the host-side logic tests for the modelimport mod.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
. "$root\common\tools.ps1"
$out = "$root\build\tests\modelimport"
New-Item -ItemType Directory -Force $out | Out-Null
$fail = 0
foreach ($t in 'window_test', 'restore_test', 'alloc_test', 'layout_test', 'rules_test', 'churn_test') {
    & $zig cc -target x86_64-windows-gnu -O1 "$PSScriptRoot\$t.c" -o "$out\$t.exe"
    if ($LASTEXITCODE) { throw "$t build failed" }
    & "$out\$t.exe"
    if ($LASTEXITCODE) { $fail = 1 }
}
# gbi.h and the scratch layout twice: F3DEX2 (the default) and F3DEX 1.x (aki-roadmap#163, #247).
# list_room.h too: the slot list bound (aki-roadmap#175).
foreach ($v in @(@('gbi_test', 'gbi_test', @()), @('gbi_test', 'gbi_test_f3dex1', @('-DMI_F3DEX1')),
                 @('rules_test', 'rules_test_f3dex1', @('-DMI_F3DEX1')),
                 @('list_room_test', 'list_room_test', @()), @('list_room_test', 'list_room_test_f3dex1', @('-DMI_F3DEX1')))) {
    & $zig cc -target x86_64-windows-gnu -O1 @($v[2]) "$PSScriptRoot\$($v[0]).c" -o "$out\$($v[1]).exe"
    if ($LASTEXITCODE) { throw "$($v[1]) build failed" }
    & "$out\$($v[1]).exe"
    if ($LASTEXITCODE) { $fail = 1 }
}
exit $fail
