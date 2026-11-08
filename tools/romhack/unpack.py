"""unpack: extract a downloaded zip or 7z archive safely for ROM-hack imports (aki-roadmap#228).

API (aki_import calls these; stdlib only, 3.7+):
  extract(archive, dest, max_total=MAX_TOTAL, sevenzip=None)      zip or 7z, by the file's magic
  extract_zip(archive, dest, max_total=MAX_TOTAL)
  extract_7z(archive, dest, max_total=MAX_TOTAL, sevenzip=None)
  find_7z(sevenzip=None) -> path of 7z.exe / 7za.exe / 7zr.exe, or None
Each returns {"files": n, "bytes": total uncompressed} and leaves the files in dest, which must not exist yet.

Every failure raises UnpackError: str(err) is player-readable, err.kind is one of
  bad-path   a member is an absolute path, has '..', a drive letter, ':' or a Windows device name (CON, NUL ...)
  too-big    more than max_total bytes would come out (default 4 GB). Counted on the bytes actually written, not
             the sizes the archive declares, so an archive that lies about its sizes is still stopped part way.
  need-7zip  a 7z archive and no 7-Zip found (it isn't bundled: install it from 7-zip.org)
  damaged    not a readable archive, or 7-Zip failed
  exists     dest is already there
  not-writable  dest can't be written

Nothing is written outside dest: the archive is unpacked into a temp folder beside it (dest.part-*), which is
renamed to dest only when everything is out and checked, and deleted on any failure.

Command line: python -I unpack.py ARCHIVE DEST [--max-mb N] [--sevenzip EXE] prints one JSON line,
{"ok": true, "files": n, "bytes": n} or {"ok": false, "error": kind, "message": text}; exit 0 / 1.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

MAX_TOTAL = 4 << 30           # default cap on everything an archive unpacks to
CHUNK = 1 << 20
POLL = 0.2                    # seconds between size checks while 7-Zip runs
ZIP_MAGIC = b'PK\x03\x04'
SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"
DEVICE_NAMES = {'con', 'prn', 'aux', 'nul'} | {'com%d' % i for i in range(1, 10)} | {'lpt%d' % i for i in range(1, 10)}
SEVENZ_NAMES = ('7z.exe', '7za.exe', '7zr.exe')


class UnpackError(Exception):
    """An archive can't be unpacked. str(err) is a player-readable message; err.kind says why (see the docstring)."""

    def __init__(self, message, kind='damaged'):
        super().__init__(message)
        self.kind = kind


def _gb(n):
    return '%.1f GB' % (n / float(1 << 30)) if n >= (1 << 30) else '%d MB' % (n >> 20)


def _too_big(archive, max_total):
    return UnpackError('%s unpacks to more than %s, the limit for one import. It may be damaged or a "zip bomb"; '
                       'if it really is that big, extract it yourself and import the folder.'
                       % (os.path.basename(archive), _gb(max_total)), 'too-big')


def check_name(name):
    """The member name as safe relative parts, or raise bad-path. Both / and \\ count as separators."""
    parts = name.replace('\\', '/').split('/')
    if name.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', name):
        raise UnpackError('The archive has a file with an absolute path (%r), which could write anywhere on the PC. '
                          'It was not unpacked.' % name, 'bad-path')
    clean = [p for p in parts if p not in ('', '.')]
    for p in clean:
        if p == '..':
            raise UnpackError('The archive has a file that points outside its folder (%r). It was not unpacked.' % name,
                              'bad-path')
        if ':' in p:
            raise UnpackError('The archive has a file name with ":" (%r), which Windows reads as a drive or hidden '
                              'stream. It was not unpacked.' % name, 'bad-path')
        if p.split('.')[0].rstrip(' ').lower() in DEVICE_NAMES:
            raise UnpackError('The archive has a file named like a Windows device (%r). It was not unpacked.' % name,
                              'bad-path')
    if not clean:
        raise UnpackError('The archive has a file with an empty name (%r). It was not unpacked.' % name, 'bad-path')
    return clean


def declared_total(sizes):
    """What the archive says it unpacks to: a quick refusal before anything is written. Not trusted on its own;
    the bytes written are counted too."""
    return sum(sizes)


def _staging(dest):
    """A fresh temp folder beside dest (same disk, so the final rename is atomic)."""
    if os.path.lexists(dest):
        raise UnpackError('The folder %s is already there; nothing was unpacked.' % dest, 'exists')
    parent = os.path.dirname(os.path.abspath(dest)) or '.'
    try:
        os.makedirs(parent, exist_ok=True)
        return tempfile.mkdtemp(prefix=os.path.basename(dest) + '.part-', dir=parent)
    except OSError as e:
        raise UnpackError("Can't write to %s (%s)." % (parent, e.strerror or e), 'not-writable')


def _finish(part, dest):
    try:
        os.rename(part, dest)
    except OSError as e:
        raise UnpackError("Can't move the unpacked files to %s (%s)." % (dest, e.strerror or e), 'not-writable')


def _rmtree(path):
    def onerror(func, p, _):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)


# ------------------------------------------------------------------------------------ zip

def extract_zip(archive, dest, max_total=MAX_TOTAL):
    part = _staging(dest)
    try:
        result = _zip_into(archive, part, max_total)
        _finish(part, dest)
        return result
    finally:
        if os.path.isdir(part):
            _rmtree(part)


def _zip_into(archive, part, max_total):
    try:
        z = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise UnpackError('%s is not a readable zip (%s). Try downloading it again.' % (os.path.basename(archive), e))
    with z:
        members = z.infolist()
        plans = [(m, check_name(m.filename)) for m in members]   # every name checked before anything is written
        if declared_total(m.file_size for m in members) > max_total:
            raise _too_big(archive, max_total)
        total, files = 0, 0
        for m, parts in plans:
            target = os.path.join(part, *parts)
            try:
                if m.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(m) as src, open(target, 'wb') as out:
                    while True:
                        # Never read past the cap, whatever the header says the member's size is.
                        chunk = src.read(min(CHUNK, max_total - total + 1))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_total:
                            raise _too_big(archive, max_total)
                        out.write(chunk)
            except UnpackError:
                raise
            except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError, RuntimeError,
                    NotImplementedError) as e:
                # RuntimeError: an encrypted member; NotImplementedError: a compression method zipfile lacks.
                raise UnpackError('%s is damaged or uses a feature the importer can\'t read (%s: %s). Try '
                                  'downloading it again, or extract it yourself and import the folder.'
                                  % (os.path.basename(archive), m.filename, e))
            except OSError as e:
                if getattr(e, 'errno', None) in (13, 28, 30):   # EACCES, ENOSPC, EROFS
                    raise UnpackError("Can't write %s (%s)." % (target, e.strerror or e), 'not-writable')
                raise UnpackError('%s: %s could not be unpacked (%s).' % (os.path.basename(archive), m.filename,
                                                                         e.strerror or e))
            files += 1
    return {'files': files, 'bytes': total}


# ------------------------------------------------------------------------------------ 7z

def find_7z(sevenzip=None):
    """The 7-Zip program to use: sevenzip if given (and there), else 7z / 7za / 7zr on PATH, else 7-Zip's install
    folder. None when there is none; 7-Zip isn't bundled."""
    if sevenzip:
        return sevenzip if os.path.isfile(sevenzip) else None
    for name in SEVENZ_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for env in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
        base = os.environ.get(env)
        exe = base and os.path.join(base, '7-Zip', '7z.exe')
        if exe and os.path.isfile(exe):
            return exe
    return None


def _need_7zip(archive):
    return UnpackError('%s is a 7z archive, and 7-Zip is needed to open it. Install 7-Zip (free, from 7-zip.org) '
                       'and import it again, or extract it yourself and import the folder.'
                       % os.path.basename(archive), 'need-7zip')


def list_7z(exe, archive):
    """[(name, declared size, is_dir)] from `7z l -slt`."""
    try:
        r = subprocess.run([exe, 'l', '-slt', '-sccUTF-8', '-y', archive], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise UnpackError('7-Zip could not be run (%s).' % e)
    if r.returncode != 0:
        raise UnpackError('%s is not a readable 7z archive (7-Zip exit %d). Try downloading it again.'
                          % (os.path.basename(archive), r.returncode))
    text = r.stdout.decode('utf-8', 'replace')
    body = text.split('\n----------', 1)
    entries = []
    if len(body) < 2:
        return entries
    for block in re.split(r'\r?\n\r?\n', body[1]):
        fields = {}
        for line in block.splitlines():
            if ' = ' in line:
                k, v = line.split(' = ', 1)
                fields[k.strip()] = v
        if 'Path' not in fields:
            continue
        size = fields.get('Size', '').strip()
        is_dir = fields.get('Folder', '').strip() == '+' or 'D' in fields.get('Attributes', '').split(' ')[0]
        entries.append((fields['Path'], int(size) if size.isdigit() else 0, is_dir))
    return entries


def _tree_size(root):
    """Bytes and files under root; raise bad-path for a link or junction (never followed)."""
    total, files = 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            try:
                st = os.lstat(os.path.join(dirpath, n))
            except OSError:   # 7-Zip is still writing; the final count catches it
                continue
            if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
                raise UnpackError('The archive made a link (%s), which could point anywhere on the PC. It was not '
                                  'unpacked.' % n, 'bad-path')
            if n in filenames:
                total += st.st_size
                files += 1
    return total, files


def extract_7z(archive, dest, max_total=MAX_TOTAL, sevenzip=None):
    exe = find_7z(sevenzip)
    if not exe:
        raise _need_7zip(archive)
    entries = list_7z(exe, archive)
    for name, _, _ in entries:
        check_name(name)
    if declared_total(size for _, size, _ in entries) > max_total:
        raise _too_big(archive, max_total)
    part = _staging(dest)
    try:
        # -snl-: links come out as plain files. The folder's size is watched while 7-Zip runs, so a lying archive
        # is stopped part way (and checked again at the end).
        log = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen([exe, 'x', '-y', '-bd', '-snl-', '-o' + part, archive], stdout=log,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            try:
                while proc.poll() is None:
                    time.sleep(POLL)
                    if _tree_size(part)[0] > max_total:
                        raise _too_big(archive, max_total)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        finally:
            log.close()
        if proc.returncode != 0:
            raise UnpackError('%s could not be unpacked (7-Zip exit %d). It may be damaged or password protected; '
                              'try downloading it again.' % (os.path.basename(archive), proc.returncode))
        total, files = _tree_size(part)
        if total > max_total:
            raise _too_big(archive, max_total)
        _finish(part, dest)
        return {'files': files, 'bytes': total}
    finally:
        if os.path.isdir(part):
            _rmtree(part)


# ------------------------------------------------------------------------------------ either

def extract(archive, dest, max_total=MAX_TOTAL, sevenzip=None):
    """A zip or 7z, by its first bytes (not its extension)."""
    try:
        with open(archive, 'rb') as f:
            head = f.read(8)
    except OSError as e:
        raise UnpackError("Can't read %s (%s)." % (archive, e.strerror or e))
    if head.startswith(ZIP_MAGIC) or head.startswith(b'PK\x05\x06'):
        return extract_zip(archive, dest, max_total)
    if head.startswith(SEVENZ_MAGIC):
        return extract_7z(archive, dest, max_total, sevenzip)
    raise UnpackError('%s is not a zip or 7z archive.' % os.path.basename(archive))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archive')
    ap.add_argument('dest', help='folder to create (must not exist)')
    ap.add_argument('--max-mb', type=int, default=MAX_TOTAL >> 20, help='cap on the unpacked total (default 4096)')
    ap.add_argument('--sevenzip', help='7z.exe / 7za.exe to use')
    args = ap.parse_args(argv)
    try:
        r = extract(args.archive, args.dest, args.max_mb << 20, args.sevenzip)
        print(json.dumps(dict(ok=True, **r), sort_keys=True))
        return 0
    except UnpackError as e:
        print(json.dumps(dict(ok=False, error=e.kind, message=str(e)), sort_keys=True))
        return 1


if __name__ == '__main__':
    sys.exit(main())
