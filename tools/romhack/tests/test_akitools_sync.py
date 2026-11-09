"""The converter tools here are copies of AkiTools files (aki-roadmap#228). akitools-sync.json records the git blob of
each AkiTools file at the last sync; this fails when a depot AkiTools checkout has committed past one of them, so a
change made in AkiTools only is noticed. Without an AkiTools checkout (players, a lone AkiMods clone) it skips.
Run: python tests/test_akitools_sync.py   (AKI_TOOLS=<AkiTools checkout> picks the checkout; default <depot>/AkiTools)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SYNC = os.path.join(TOOLS, 'akitools-sync.json')


def git(repo, *args):
    """git's stdout, or None when git fails or isn't installed."""
    try:
        res = subprocess.run(['git', '-C', repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return None
    return res.stdout.decode('utf-8', 'replace') if res.returncode == 0 else None


def akitools_checkout():
    path = os.environ.get('AKI_TOOLS')
    if not path:
        depot = os.environ.get('AKI_DEPOT') or os.path.dirname(os.path.dirname(os.path.dirname(TOOLS)))
        path = os.path.join(depot, 'AkiTools')
    return path if os.path.isdir(os.path.join(path, '.git')) or os.path.isfile(os.path.join(path, '.git')) else None


def check(repo, path, blob):
    """('same', []) when the checkout's HEAD has `blob` at `path`; ('drift', [commits since]) when HEAD's history of the
    path holds `blob` and later commits changed it; ('unknown', []) when it doesn't (an older or unrelated checkout)."""
    head = git(repo, 'rev-parse', 'HEAD:' + path)
    if head is None:
        return 'unknown', []
    if head.strip() == blob:
        return 'same', []
    log = git(repo, 'log', '--raw', '--no-abbrev', '--format=@%H %s', 'HEAD', '--', path) or ''
    newer, commit = [], None
    for line in log.splitlines():
        if line.startswith('@'):
            commit = line[1:8] + line[41:]   # short hash (--no-abbrev also lengthens %h) and subject
        elif line.startswith(':'):
            parts = line.split()
            if len(parts) >= 4 and parts[3] == blob:
                return 'drift', newer
            if commit and (not newer or newer[-1] != commit):
                newer.append(commit)
    return 'unknown', []


class Drift(unittest.TestCase):
    def test_copies_match_akitools(self):
        repo = akitools_checkout()
        if repo is None:
            self.skipTest('no AkiTools checkout (set AKI_TOOLS or AKI_DEPOT)')
        sync = json.load(open(SYNC))
        drifted, unknown = [], []
        for path, entry in sorted(sync['files'].items()):
            self.assertTrue(os.path.isfile(os.path.join(TOOLS, entry['copy'])), entry['copy'])
            state, newer = check(repo, path, entry['blob'])
            if state == 'drift':
                drifted.append('%s -> %s: %s' % (path, entry['copy'], '; '.join(newer)))
            elif state == 'unknown':
                unknown.append(path)
        if drifted:
            self.fail('AkiTools changed these since the copies here were synced (AkiTools %s); port the changes to '
                      'tools/romhack and update akitools-sync.json:\n  %s' % (sync['akitools_commit'], '\n  '.join(drifted)))
        if len(unknown) == len(sync['files']):
            self.skipTest('the AkiTools checkout at %s is older than the last sync or on another line' % repo)

    def test_every_copy_is_listed(self):
        # A new AkiTools file copied here needs an entry, or its drift goes unseen.
        sync = json.load(open(SYNC))
        copies = {e['copy'] for e in sync['files'].values()}
        for name in ('aki_conv.py', 'aki_hack.py', 'aki_conv_bake.py', 'aki_conv_values.py'):
            self.assertIn(name, copies)
        for e in sync['files'].values():
            self.assertRegex(e['blob'], r'^[0-9a-f]{40}$')


@unittest.skipIf(shutil.which('git') is None, 'git not installed')
class Check(unittest.TestCase):
    """check() on a synthetic repository."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.git('init')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def git(self, *args):
        subprocess.run(['git', '-C', self.d, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.autocrlf=false']
                       + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def commit(self, text, msg):
        with open(os.path.join(self.d, 'aki-conv.py'), 'w', newline='\n') as f:
            f.write(text)
        self.git('add', 'aki-conv.py')
        self.git('commit', '-m', msg)
        return git(self.d, 'rev-parse', 'HEAD:aki-conv.py').strip()

    def test_same_drift_unknown(self):
        old = self.commit('one\n', 'first')
        self.assertEqual(check(self.d, 'aki-conv.py', old), ('same', []))
        self.commit('two\n', 'second change')
        self.commit('three\n', 'third change')
        state, newer = check(self.d, 'aki-conv.py', old)
        self.assertEqual(state, 'drift')
        self.assertEqual([c.split(' ', 1)[1] for c in newer], ['third change', 'second change'])
        # A blob the checkout never had: an older checkout, or another line of work. Not a failure.
        self.assertEqual(check(self.d, 'aki-conv.py', '0' * 40), ('unknown', []))
        self.assertEqual(check(self.d, 'missing.py', old), ('unknown', []))


if __name__ == '__main__':
    unittest.main()
