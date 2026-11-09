"""Tests for pyfetch.ps1 (aki-roadmap#228): the real script under Windows PowerShell, with a fake downloader: a
synthetic "embeddable Python" zip (a fake python.exe and stdlib zip, no real Python) served as a file:// URL with
its own SHA-256, and a closed local port standing in for no network. Nothing touches python.org. Skipped where
there is no powershell.exe:

    python -m unittest discover -s tools/romhack/tests
"""
import hashlib
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tools/romhack
SCRIPT = os.path.join(ROOT, 'pyfetch.ps1')
POWERSHELL = shutil.which('powershell') or shutil.which('powershell.exe')
VERSION = '3.12.10'


def fake_embeddable(names=('python.exe', 'python312.zip', 'python312._pth')):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, b'MZ not really python' if n.endswith('.exe') else b'synthetic ' + n.encode())
    return buf.getvalue()


def closed_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@unittest.skipUnless(POWERSHELL and os.name == 'nt', 'needs Windows PowerShell')
class PyFetch(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='pyfetch-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = os.path.join(self.dir, 'python')

    def serve(self, blob, name='embed.zip'):
        """A 'download': the zip on disk as a file:// URL, and its SHA-256."""
        p = os.path.join(self.dir, name)
        with open(p, 'wb') as f:
            f.write(blob)
        return pathlib.Path(p).as_uri(), hashlib.sha256(blob).hexdigest()

    def run_fetch(self, *args, root=None):
        cmd = [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT,
               '-Root', root or self.root, '-SkipRunCheck'] + list(args)
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=120)
        lines = r.stdout.decode('utf-8', 'replace').splitlines()
        self.assertEqual(len(lines), 1, (lines, r.stderr.decode('utf-8', 'replace')))
        out = json.loads(lines[0])
        self.assertEqual(r.returncode, 0 if out['ok'] else 1, out)
        return out

    def leftovers(self):
        return [n for n in os.listdir(self.root) if n != VERSION] if os.path.isdir(self.root) else []

    def test_installs_then_reuses_without_network(self):
        url, sha = self.serve(fake_embeddable())
        out = self.run_fetch('-Url', url, '-Sha256', sha)
        exe = os.path.join(self.root, VERSION, 'python.exe')
        self.assertEqual(out, {'ok': True, 'python': exe, 'version': VERSION, 'fetched': True})
        self.assertTrue(os.path.isfile(exe))
        self.assertTrue(os.path.isfile(os.path.join(self.root, VERSION, '.aki-complete')))
        self.assertEqual(self.leftovers(), [])
        # Second call: no download at all (the URL now goes nowhere).
        out = self.run_fetch('-Url', 'http://127.0.0.1:%d/gone.zip' % closed_port(), '-Sha256', sha)
        self.assertEqual((out['ok'], out['fetched'], out['python']), (True, False, exe))

    def test_no_network(self):
        out = self.run_fetch('-Url', 'http://127.0.0.1:%d/python.zip' % closed_port(), '-TimeoutSec', '10')
        self.assertEqual(out['error'], 'no-network')
        self.assertIn('internet connection', out['message'])
        self.assertEqual(self.leftovers(), [])

    def test_download_missing(self):
        out = self.run_fetch('-Url', pathlib.Path(os.path.join(self.dir, 'nope.zip')).as_uri())
        self.assertEqual(out['error'], 'download-failed')
        self.assertEqual(self.leftovers(), [])

    def test_bad_hash_installs_nothing(self):
        url, _ = self.serve(fake_embeddable())
        out = self.run_fetch('-Url', url, '-Sha256', '0' * 64)
        self.assertEqual(out['error'], 'bad-hash')
        self.assertIn('damaged', out['message'])
        self.assertFalse(os.path.exists(os.path.join(self.root, VERSION)))
        self.assertEqual(self.leftovers(), [])

    def test_pinned_hash_is_used_by_default(self):
        # Without -Sha256 the pinned python.org hash applies, so a fake zip is refused.
        url, _ = self.serve(fake_embeddable())
        self.assertEqual(self.run_fetch('-Url', url)['error'], 'bad-hash')
        with open(SCRIPT, encoding='utf-8') as f:
            text = f.read()
        self.assertIn("$PYTHON_VERSION = '%s'" % VERSION, text)
        self.assertIn('python-%s-embed-amd64.zip' % VERSION, text)

    def test_not_a_python_package(self):
        url, sha = self.serve(fake_embeddable(names=('readme.txt',)))
        out = self.run_fetch('-Url', url, '-Sha256', sha)
        self.assertEqual(out['error'], 'unpack-failed')
        self.assertIn('python.exe', out['message'])
        self.assertFalse(os.path.exists(os.path.join(self.root, VERSION)))
        self.assertEqual(self.leftovers(), [])

    def test_corrupt_zip(self):
        url, sha = self.serve(b'PK\x03\x04' + b'\x00' * 200)
        out = self.run_fetch('-Url', url, '-Sha256', sha)
        self.assertEqual(out['error'], 'unpack-failed')
        self.assertEqual(self.leftovers(), [])

    def test_half_finished_unpack_is_redone(self):
        # A folder without the completion marker (not made by pyfetch's rename) and a temp folder from a run that
        # died 2 hours ago: both cleared, Python installed. A recent temp folder may be another run: kept.
        dest = os.path.join(self.root, VERSION)
        os.makedirs(dest)
        with open(os.path.join(dest, 'python.exe'), 'wb') as f:
            f.write(b'half')
        stale = os.path.join(self.root, VERSION + '.part-1-2')
        fresh = os.path.join(self.root, VERSION + '.part-3-4')
        os.makedirs(stale)
        os.makedirs(fresh)
        old = time.time() - 2 * 3600
        os.utime(stale, (old, old))
        url, sha = self.serve(fake_embeddable())
        out = self.run_fetch('-Url', url, '-Sha256', sha)
        self.assertEqual((out['ok'], out['fetched']), (True, True))
        self.assertTrue(os.path.isfile(os.path.join(dest, '.aki-complete')))
        with open(os.path.join(dest, 'python.exe'), 'rb') as f:
            self.assertNotEqual(f.read(), b'half')
        self.assertEqual(self.leftovers(), [os.path.basename(fresh)])

    def test_non_ascii_root_survives_the_json(self):
        root = os.path.join(self.dir, 'Jos\u00e9 \u5c71\u7530', 'python')
        url, sha = self.serve(fake_embeddable())
        out = self.run_fetch('-Url', url, '-Sha256', sha, root=root)
        self.assertEqual(out['python'], os.path.join(root, VERSION, 'python.exe'))
        self.assertTrue(os.path.isfile(out['python']))

    def test_check_only(self):
        out = self.run_fetch('-NoDownload')
        self.assertEqual(out['error'], 'not-installed')
        self.assertFalse(os.path.exists(self.root))
        url, sha = self.serve(fake_embeddable())
        self.run_fetch('-Url', url, '-Sha256', sha)
        self.assertEqual(self.run_fetch('-NoDownload')['fetched'], False)

    def test_root_under_a_file(self):
        blocker = os.path.join(self.dir, 'blocker')
        with open(blocker, 'wb') as f:
            f.write(b'x')
        out = self.run_fetch(root=os.path.join(blocker, 'python'))
        self.assertEqual(out['error'], 'not-writable')
        self.assertIn('read-only', out['message'])

    def test_read_only_folder(self):
        os.makedirs(self.root)
        user = subprocess.run(['whoami'], stdout=subprocess.PIPE).stdout.decode().strip()
        r = subprocess.run(['icacls', self.root, '/deny', '%s:(OI)(CI)(W)' % user], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        if r.returncode != 0:
            self.skipTest('icacls could not deny write: %s' % r.stdout.decode('utf-8', 'replace'))
        self.addCleanup(subprocess.run, ['icacls', self.root, '/remove:d', user], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT)
        url, sha = self.serve(fake_embeddable())
        out = self.run_fetch('-Url', url, '-Sha256', sha)
        self.assertEqual(out['error'], 'not-writable')
        self.assertIn(self.root, out['message'])


if __name__ == '__main__':
    unittest.main()
