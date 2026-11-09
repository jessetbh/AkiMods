<#
.SYNOPSIS
pyfetch: Python on demand for ROM-hack imports (aki-roadmap#228).

.DESCRIPTION
Players run the importer (aki_import.py) from AKI Launcher and usually have no Python. The first time an import
needs it, the launcher runs this script, which downloads python.org's Windows embeddable zip for one pinned
version, checks it against the SHA-256 pinned below, unpacks it to %LOCALAPPDATA%\AkiLauncher\python\<version>
and prints where python.exe is. Later runs find it there and touch no network.

This is PowerShell, not Python, because it has to run before there is a Python: Windows PowerShell 5.1 ships with
every Windows 10 and 11, so the launcher can always start it:

    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File pyfetch.ps1

It prints exactly one JSON line on stdout and exits 0 when Python is ready, 1 when not:

    {"ok":true,"python":"C:\\...\\python.exe","version":"3.12.10","fetched":true}
    {"ok":false,"error":"no-network","message":"<player-readable text>"}

error is one of: no-network, download-failed, bad-hash, unpack-failed, not-writable, not-installed (-NoDownload),
failed (anything else). message is meant to be shown to the player as it is.

The unpack goes to a temp folder next to the target and is renamed into place only after it is complete and
checked, so a crash or power cut part way never leaves a folder that looks installed. Leftovers are cleared on
the next run.

3.12.10 is pinned because it is the last 3.12 release with Windows binaries (later 3.12 releases are source
only). Its SHA-256 was computed from the download and matches the MD5 python.org lists for the file.

-Url, -Sha256 and -SkipRunCheck exist for tests (a local file:// zip with a fake python.exe); the launcher
passes none of them.

.PARAMETER Root
Parent folder for the installs. Default: %LOCALAPPDATA%\AkiLauncher\python.

.PARAMETER NoDownload
Only report whether Python is installed; never download (error "not-installed" when it isn't).
#>
param(
    [string]$Root = '',
    [string]$Url = '',
    [string]$Sha256 = '',
    [int]$TimeoutSec = 60,
    [switch]$NoDownload,
    [switch]$SkipRunCheck
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$PYTHON_VERSION = '3.12.10'
$PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
$PYTHON_SHA256 = '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3'
$PYTHON_STDLIB = 'python312.zip'      # the embeddable package's standard library: it must be there too
$STALE_MINUTES = 60                   # temp folders older than this are a run that died; newer may be another run

class FetchError : System.Exception {
    [string]$Kind
    FetchError([string]$kind, [string]$message) : base($message) { $this.Kind = $kind }
}

function Emit($obj) {
    # Pure ASCII: redirected stdout is in the OEM code page, and PS 5.1 doesn't escape non-ASCII, so a user name
    # like Jose with an accent would reach the launcher garbled. \uXXXX is plain JSON.
    $json = $obj | ConvertTo-Json -Compress
    $json = [regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
    [Console]::Out.WriteLine($json)
}

function Test-Installed([string]$dir) {
    # Complete = renamed into place by this script, which writes the marker last, before the rename.
    return (Test-Path -LiteralPath (Join-Path $dir 'python.exe') -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $dir $PYTHON_STDLIB) -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $dir '.aki-complete') -PathType Leaf)
}

function Remove-Quietly([string]$path) {
    if (Test-Path -LiteralPath $path) {
        try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop } catch { }
    }
}

function Clear-Stale([string]$root, [string]$version) {
    # A run that died part way leaves <version>.part-* folders or <version>.download-* files.
    $cutoff = (Get-Date).AddMinutes(-$STALE_MINUTES)
    Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue |
        Where-Object { ($_.Name -like "$version.part-*" -or $_.Name -like "$version.download-*") -and
                       $_.LastWriteTime -lt $cutoff } |
        ForEach-Object { Remove-Quietly $_.FullName }
}

function Assert-Writable([string]$root) {
    try {
        New-Item -ItemType Directory -Force -Path $root | Out-Null
        $probe = Join-Path $root ('.write-test-' + $PID)
        [IO.File]::WriteAllText($probe, 'x')
        Remove-Item -LiteralPath $probe -Force
    } catch {
        throw [FetchError]::new('not-writable', ("Can't write to $root, so Python can't be set up for ROM-hack " +
            "imports. Check that the folder isn't read-only and that the disk has space, then try again."))
    }
}

function Get-Download([string]$url, [string]$file, [int]$timeoutSec) {
    try {
        $sp = [Net.ServicePointManager]::SecurityProtocol
        [Net.ServicePointManager]::SecurityProtocol = $sp -bor [Net.SecurityProtocolType]::Tls12
    } catch { }
    $stream = $null; $out = $null; $resp = $null
    try {
        $req = [Net.WebRequest]::Create($url)
        $req.Timeout = $timeoutSec * 1000
        if ($req -is [Net.HttpWebRequest]) { $req.ReadWriteTimeout = $timeoutSec * 1000 }
        $resp = $req.GetResponse()
        $stream = $resp.GetResponseStream()
        $out = [IO.File]::Create($file)
        $stream.CopyTo($out)
    } catch {
        $e = $_.Exception
        while ($e.InnerException -and -not ($e -is [Net.WebException])) { $e = $e.InnerException }
        $offline = 'NameResolutionFailure', 'ProxyNameResolutionFailure', 'ConnectFailure', 'Timeout',
                   'ConnectionClosed', 'ReceiveFailure', 'SendFailure', 'SecureChannelFailure', 'KeepAliveFailure'
        $socket = $false   # the connection dropped part way: an IOException around a SocketException
        for ($x = $_.Exception; $x; $x = $x.InnerException) { if ($x -is [Net.Sockets.SocketException]) { $socket = $true } }
        if ($socket -or ($e -is [Net.WebException] -and $offline -contains [string]$e.Status)) {
            throw [FetchError]::new('no-network', ("Couldn't download Python for ROM-hack imports: no connection " +
                "to python.org ($($e.Status)). Check the internet connection and try again."))
        }
        if ($e -is [IO.IOException] -and -not ($e -is [IO.FileNotFoundException]) -and
            -not ($e -is [IO.DirectoryNotFoundException])) {
            throw [FetchError]::new('not-writable', ("Couldn't save the Python download to $file ($($e.Message)). " +
                "Check that the disk has space and try again."))
        }
        throw [FetchError]::new('download-failed', ("Couldn't download Python for ROM-hack imports from $url " +
            "($($e.Message)). Try again later."))
    } finally {
        if ($out) { $out.Dispose() }
        if ($stream) { $stream.Dispose() }
        if ($resp) { $resp.Dispose() }
    }
}

function Install-Python([string]$root, [string]$version, [string]$url, [string]$sha256) {
    $dest = Join-Path $root $version
    $tag = '{0}-{1}' -f $PID, [DateTime]::Now.Ticks
    $zip = Join-Path $root "$version.download-$tag.zip"
    $part = Join-Path $root "$version.part-$tag"
    try {
        Get-Download $url $zip $TimeoutSec
        $got = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
        if ($got -ne $sha256.ToUpperInvariant()) {
            throw [FetchError]::new('bad-hash', ("The Python download was damaged or isn't the expected file " +
                "(SHA-256 $got). Nothing was installed. Try again; if it keeps happening, your connection may be " +
                "altering downloads."))
        }
        try {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $part)
        } catch {
            $e = $_.Exception; if ($e.InnerException) { $e = $e.InnerException }
            if ($e -is [UnauthorizedAccessException] -or ($e -is [IO.IOException] -and
                    -not ($e -is [IO.InvalidDataException]))) {
                throw [FetchError]::new('not-writable', ("Couldn't unpack Python into $root ($($e.Message)). " +
                    "Check that the folder isn't read-only and that the disk has space, then try again."))
            }
            throw [FetchError]::new('unpack-failed', "Couldn't unpack the Python download ($($e.Message)). Try again.")
        }
        foreach ($need in 'python.exe', $PYTHON_STDLIB) {
            if (-not (Test-Path -LiteralPath (Join-Path $part $need) -PathType Leaf)) {
                throw [FetchError]::new('unpack-failed', ("The Python download has no $need, so it isn't the " +
                    "Python embeddable package. Nothing was installed."))
            }
        }
        if (-not $SkipRunCheck) {
            $exe = Join-Path $part 'python.exe'
            $ver = ''
            try { $ver = (& $exe -I -c 'import sys, zipfile, hashlib, json; print(sys.version.split()[0])' 2>$null) }
            catch { }
            if ([string]$ver -ne $version) {
                throw [FetchError]::new('unpack-failed', ("The downloaded Python doesn't start on this PC (it " +
                    "answered '$ver', expected $version). Nothing was installed."))
            }
        }
        [IO.File]::WriteAllText((Join-Path $part '.aki-complete'), "$url`r`n$sha256`r`n")
        if (Test-Path -LiteralPath $dest) {
            if (Test-Installed $dest) { return $false }   # another run finished first: use that one
            Remove-Item -LiteralPath $dest -Recurse -Force   # incomplete (not made by this script's rename)
        }
        # Directory.Move, not Move-Item: if another run got there first, Move-Item would move part *into* dest.
        try { [IO.Directory]::Move($part, $dest) }
        catch {
            if (Test-Installed $dest) { return $false }
            throw [FetchError]::new('not-writable', ("Couldn't move Python into place at $dest " +
                "($($_.Exception.Message)). Close other AKI Launcher windows and try again."))
        }
        return $true
    } finally {
        Remove-Quietly $zip
        Remove-Quietly $part
    }
}

try {
    if (-not $Root) {
        if (-not $env:LOCALAPPDATA) { throw [FetchError]::new('not-writable', 'LOCALAPPDATA is not set, so there is no per-user folder for Python.') }
        $Root = Join-Path $env:LOCALAPPDATA 'AkiLauncher\python'
    }
    if (-not $Url) { $Url = $PYTHON_URL }
    if (-not $Sha256) { $Sha256 = $PYTHON_SHA256 }
    $dest = Join-Path $Root $PYTHON_VERSION
    $fetched = $false
    if (-not (Test-Installed $dest)) {
        if ($NoDownload) {
            throw [FetchError]::new('not-installed', "Python for ROM-hack imports isn't set up yet (it is downloaded the first time an import needs it).")
        }
        Assert-Writable $Root
        Clear-Stale $Root $PYTHON_VERSION
        $fetched = Install-Python $Root $PYTHON_VERSION $Url $Sha256
    }
    Emit ([ordered]@{ ok = $true; python = (Join-Path $dest 'python.exe'); version = $PYTHON_VERSION; fetched = $fetched })
    exit 0
} catch {
    $e = $_.Exception
    if ($e -is [FetchError]) {
        Emit ([ordered]@{ ok = $false; error = $e.Kind; message = $e.Message })
    } else {
        Emit ([ordered]@{ ok = $false; error = 'failed'; message = "Setting up Python for ROM-hack imports failed: $($e.Message)" })
    }
    exit 1
}
