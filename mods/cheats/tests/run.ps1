# Builds and runs the host-side parser test for the cheats mod.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\..\.."
. "$root\common\tools.ps1"
$out = "$root\build\tests\cheats"
New-Item -ItemType Directory -Force $out | Out-Null
& $zig cc -target x86_64-windows-gnu -O1 -Wno-int-to-pointer-cast -I "$PSScriptRoot" -I "$root\common\include" "$PSScriptRoot\parse_test.c" -o "$out\parse_test.exe"
if ($LASTEXITCODE) { throw 'test build failed' }
# The conversion-codes glue only builds for games with the host export: check it compiles too.
& $zig cc -target x86_64-windows-gnu -O1 -Wno-int-to-pointer-cast -DAKI_HOST_CONVERSION -I "$PSScriptRoot" -I "$root\common\include" -c "$PSScriptRoot\parse_test.c" -o "$out\parse_test_conversion.o"
if ($LASTEXITCODE) { throw 'conversion build failed' }
& "$out\parse_test.exe"
$cFailed = $LASTEXITCODE
python "$PSScriptRoot\test_cht2codes.py"
exit ($cFailed -bor $LASTEXITCODE)
