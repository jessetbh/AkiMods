"""Tests for unpack.py (aki-roadmap#228): zips built in memory (zip bombs, lying sizes, path traversal), temp
folders, and 7z archives made on the spot by the local 7-Zip (those are skipped when there is none). Synthetic
bytes only:

    python -m unittest discover -s tools/romhack/tests
"""
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tools/romhack
spec = importlib.util.spec_from_file_location('unpack', os.path.join(ROOT, 'unpack.py'))
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

SEVENZ = up.find_7z()


def make_zip(members, method=zipfile.ZIP_DEFLATED):
    """Zip bytes from [(name, bytes)], names exactly as given (no normalising)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', method) as z:
        for name, data in members:
            info = zipfile.ZipInfo('placeholder')
            info.filename = name          # bypass ZipInfo's own clean-up so hostile names really go in
            info.compress_type = method
            z.writestr(info, data)
    return buf.getvalue()


def lie_about_sizes(blob, size):
    """Rewrite every local and central header's uncompressed size to `size` (the data stays as it is)."""
    b = bytearray(blob)
    i = 0
    while True:
        i = b.find(b'PK\x03\x04', i)
        if i < 0:
            break
        struct.pack_into('<I', b, i + 22, size)
        i += 4
    i = 0
    while True:
        i = b.find(b'PK\x01\x02', i)
        if i < 0:
            break
        struct.pack_into('<I', b, i + 24, size)
        i += 4
    return bytes(b)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='unpack-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, rel, data):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def dest(self, name='out'):
        return os.path.join(self.dir, name)

    def assertClean(self, dest):
        """Nothing left behind: no dest, no dest.part-* folder."""
        self.assertFalse(os.path.exists(dest))
        base = os.path.basename(dest)
        self.assertEqual([n for n in os.listdir(os.path.dirname(dest)) if n.startswith(base + '.part-')], [])


class Names(unittest.TestCase):
    def test_good_names(self):
        self.assertEqual(up.check_name('a/b\\c.z64'), ['a', 'b', 'c.z64'])
        self.assertEqual(up.check_name('./x//y'), ['x', 'y'])
        self.assertEqual(up.check_name('console.txt'), ['console.txt'])   # not a device name

    def test_bad_names(self):
        for name in ('../evil', 'a/../../evil', '/abs', '\\abs', '\\\\server\\share\\x', 'C:/x', 'c:x', 'C:\\x',
                     'a/b:stream', 'CON', 'nul.txt', 'sub/Com1.z64', 'aux ', '', './', '..', 'sub/.. /x',
                     'trail.', 'trail ', 'a?b', 'a*b', 'a<b', 'a|b', 'a\x01b', 'CONIN$', 'com\u00b9.txt'):
            with self.subTest(name=name):
                with self.assertRaises(up.UnpackError) as cm:
                    up.check_name(name)
                self.assertEqual(cm.exception.kind, 'bad-path')


class Zip(Base):
    def test_unpacks_folders_and_files(self):
        arc = self.write('a.zip', make_zip([('Mod/', b''), ('Mod/hack.bps', b'BPS1' + bytes(100)),
                                            ('Mod/readme.txt', b'hi'), ('top.txt', b'x' * 5000)]))
        r = up.extract(arc, self.dest())
        self.assertEqual(r, {'files': 3, 'bytes': 104 + 2 + 5000})
        with open(os.path.join(self.dest(), 'Mod', 'readme.txt'), 'rb') as f:
            self.assertEqual(f.read(), b'hi')
        self.assertEqual(sorted(n for n in os.listdir(self.dir)), ['a.zip', 'out'])   # no .part-* left

    def test_stored_zip_and_backslash_names(self):
        arc = self.write('a.zip', make_zip([('dir\\f.bin', b'abc')], zipfile.ZIP_STORED))
        up.extract_zip(arc, self.dest())
        self.assertTrue(os.path.isfile(os.path.join(self.dest(), 'dir', 'f.bin')))

    def test_path_traversal_writes_nothing(self):
        for i, name in enumerate(('../evil.txt', '..\\evil.txt', '/evil.txt', 'C:/evil.txt', 'ok/../../evil.txt')):
            with self.subTest(name=name):
                arc = self.write('t%d.zip' % i, make_zip([('fine.txt', b'ok'), (name, b'pwned')]))
                with self.assertRaises(up.UnpackError) as cm:
                    up.extract_zip(arc, self.dest())
                self.assertEqual(cm.exception.kind, 'bad-path')
                self.assertClean(self.dest())
                self.assertFalse(os.path.exists(os.path.join(self.dir, 'evil.txt')))

    def test_zip_bomb_refused_before_writing(self):
        # 8 MB of zeros deflates to a few KB; the declared size is honest and over the cap.
        arc = self.write('bomb.zip', make_zip([('zeros.bin', bytes(8 << 20))]))
        self.assertLess(os.path.getsize(arc), 64 << 10)
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_zip(arc, self.dest(), max_total=1 << 20)
        self.assertEqual(cm.exception.kind, 'too-big')
        self.assertIn('1 MB', str(cm.exception))
        self.assertClean(self.dest())

    def test_many_members_add_up(self):
        arc = self.write('many.zip', make_zip([('f%d.bin' % i, bytes(300 << 10)) for i in range(5)]))
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_zip(arc, self.dest(), max_total=1 << 20)
        self.assertEqual(cm.exception.kind, 'too-big')
        self.assertClean(self.dest())

    def test_counted_on_bytes_written_not_declared(self):
        # With the declared-size check fooled, the byte count still stops the unpack part way.
        arc = self.write('many.zip', make_zip([('f%d.bin' % i, bytes(300 << 10)) for i in range(5)]))
        written = []
        real_open = open

        def spy_open(path, mode='r', *a, **k):
            if 'w' in mode and '.part-' in str(path):
                written.append(path)
            return real_open(path, mode, *a, **k)
        with mock.patch.object(up, 'declared_total', return_value=0), mock.patch('builtins.open', spy_open):
            with self.assertRaises(up.UnpackError) as cm:
                up.extract_zip(arc, self.dest(), max_total=1 << 20)
        self.assertEqual(cm.exception.kind, 'too-big')
        self.assertEqual(len(written), 4)   # 3 x 300 KB fit under 1 MB; the 4th crosses it, the 5th never starts
        self.assertClean(self.dest())

    def test_lying_sizes_never_write_more_than_declared(self):
        # Headers say 1000 bytes; the data inflates to 4 MB. zipfile stops at the declared size, the CRC then fails.
        arc = self.write('liar.zip', lie_about_sizes(make_zip([('zeros.bin', bytes(4 << 20))]), 1000))
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_zip(arc, self.dest(), max_total=1 << 20)
        self.assertEqual(cm.exception.kind, 'damaged')
        self.assertClean(self.dest())

    def test_duplicate_names_ignoring_case(self):
        arc = self.write('dup.zip', make_zip([('Mod/A.bin', b'1'), ('mod/a.BIN', b'2')]))
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_zip(arc, self.dest())
        self.assertEqual(cm.exception.kind, 'bad-path')
        self.assertClean(self.dest())

    def test_corrupt_deflate_data(self):
        blob = bytearray(make_zip([('f.bin', bytes(range(256)) * 400)]))
        start = 30 + len('f.bin')            # the local header, then the compressed data
        blob[start:start + 16] = b'\xff' * 16
        arc = self.write('bad.zip', bytes(blob))
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_zip(arc, self.dest())
        self.assertEqual(cm.exception.kind, 'damaged')
        self.assertClean(self.dest())

    def test_dest_must_not_exist(self):
        arc = self.write('a.zip', make_zip([('f', b'x')]))
        os.makedirs(self.dest())
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_zip(arc, self.dest())
        self.assertEqual(cm.exception.kind, 'exists')
        self.assertEqual(os.listdir(self.dest()), [])

    def test_not_an_archive(self):
        for i, data in enumerate((b'PK\x03\x04 truncated', b'\x80\x37\x12\x40' + bytes(60))):
            with self.subTest(i=i):
                with self.assertRaises(up.UnpackError) as cm:
                    up.extract(self.write('x%d.zip' % i, data), self.dest())
                self.assertEqual(cm.exception.kind, 'damaged')
                self.assertClean(self.dest())

    def test_command_line_prints_one_json_line(self):
        arc = self.write('a.zip', make_zip([('f.txt', b'hello')]))
        r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'unpack.py'), arc, self.dest()],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(json.loads(r.stdout.decode()), {'ok': True, 'files': 1, 'bytes': 5})
        bomb = self.write('b.zip', make_zip([('z', bytes(3 << 20))]))
        r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'unpack.py'), bomb, self.dest('o2'),
                            '--max-mb', '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 1)
        lines = r.stdout.decode().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])['error'], 'too-big')


class SevenZipMissing(Base):
    def test_named_but_missing(self):
        arc = self.write('a.7z', up.SEVENZ_MAGIC + bytes(26))
        with self.assertRaises(up.UnpackError) as cm:
            up.extract(arc, self.dest(), sevenzip=os.path.join(self.dir, 'missing', '7z.exe'))
        self.assertEqual(cm.exception.kind, 'need-7zip')
        self.assertIn('7-zip.org', str(cm.exception))
        self.assertClean(self.dest())

    def test_none_installed(self):
        with mock.patch.object(up.shutil, 'which', return_value=None), \
                mock.patch.dict(os.environ, {'ProgramW6432': self.dir, 'ProgramFiles': self.dir,
                                             'ProgramFiles(x86)': self.dir}):
            self.assertIsNone(up.find_7z())

    def test_listing_without_entries_is_refused(self):
        # 7-Zip exits 0 but prints no listing (another build, a quirk): the names can't be checked, so no unpack.
        arc = self.write('a.7z', up.SEVENZ_MAGIC + bytes(26))
        exe = self.write('7z.exe', b'MZ')
        done = subprocess.CompletedProcess([], 0, stdout=b'7-Zip 99\r\n\r\nScanning\r\n')
        with mock.patch.object(up.subprocess, 'run', return_value=done), \
                mock.patch.object(up.subprocess, 'Popen', side_effect=AssertionError('must not unpack')):
            with self.assertRaises(up.UnpackError) as cm:
                up.extract_7z(arc, self.dest(), sevenzip=exe)
        self.assertEqual(cm.exception.kind, 'damaged')
        self.assertClean(self.dest())

    def test_found_in_install_folder(self):
        exe = self.write('7-Zip/7z.exe', b'MZ')
        with mock.patch.object(up.shutil, 'which', return_value=None), \
                mock.patch.dict(os.environ, {'ProgramW6432': self.dir}):
            self.assertEqual(os.path.normpath(up.find_7z()), os.path.normpath(exe))


@unittest.skipUnless(SEVENZ, 'no 7z.exe / 7za.exe found')
class SevenZip(Base):
    def make_7z(self, files):
        src = os.path.join(self.dir, 'src')
        for name, data in files:
            self.write(os.path.join('src', name), data)
        arc = os.path.join(self.dir, 'a.7z')
        r = subprocess.run([SEVENZ, 'a', '-t7z', '-y', '-bd', arc, '.'], cwd=src, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
        shutil.rmtree(src)
        return arc

    def test_unpacks(self):
        arc = self.make_7z([('Mod/hack.bps', b'BPS1' + bytes(50)), ('readme.txt', b'hi')])
        r = up.extract(arc, self.dest())
        self.assertEqual(r, {'files': 2, 'bytes': 56})
        self.assertTrue(os.path.isfile(os.path.join(self.dest(), 'Mod', 'hack.bps')))
        self.assertEqual(sorted(os.listdir(self.dir)), ['a.7z', 'out'])

    def test_listing(self):
        arc = self.make_7z([('d/x.bin', b'12345'), ('y.bin', b'')])
        names = {(n.replace('\\', '/'), s, d) for n, s, d in up.list_7z(SEVENZ, arc)}
        self.assertTrue({('d/x.bin', 5, False), ('y.bin', 0, False), ('d', 0, True)} <= names, names)

    def test_bomb_refused_from_listing(self):
        arc = self.make_7z([('zeros.bin', bytes(3 << 20))])
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_7z(arc, self.dest(), max_total=1 << 20)
        self.assertEqual(cm.exception.kind, 'too-big')
        self.assertClean(self.dest())

    def test_bomb_stopped_when_listing_lies(self):
        arc = self.make_7z([('zeros.bin', bytes(3 << 20))])
        with mock.patch.object(up, 'declared_total', return_value=0):
            with self.assertRaises(up.UnpackError) as cm:
                up.extract_7z(arc, self.dest(), max_total=1 << 20)
        self.assertEqual(cm.exception.kind, 'too-big')
        self.assertClean(self.dest())

    def test_bad_names_in_listing(self):
        arc = self.make_7z([('f.txt', b'x')])
        with mock.patch.object(up, 'list_7z', return_value=[('..\\evil.txt', 1, False)]):
            with self.assertRaises(up.UnpackError) as cm:
                up.extract_7z(arc, self.dest())
        self.assertEqual(cm.exception.kind, 'bad-path')
        self.assertClean(self.dest())

    def test_damaged(self):
        arc = self.make_7z([('f.bin', bytes(range(256)) * 64)])
        with open(arc, 'r+b') as f:
            f.seek(40)
            f.write(b'\xff' * 64)
        with self.assertRaises(up.UnpackError) as cm:
            up.extract_7z(arc, self.dest())
        self.assertEqual(cm.exception.kind, 'damaged')
        self.assertClean(self.dest())


if __name__ == '__main__':
    unittest.main()
