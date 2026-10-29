"""Tests for stage.ps1's model-pack check (aki-roadmap#130): every bundled <id>.akimodel goes through
`aki-model.py validate` before anything is staged, and staging stops on errors. Each test builds a throwaway depot in
a temp folder: this repo's stage.ps1 and games.psd1, one mod with packs, a game checkout, and an AkiTools whose
aki-model.py is a stub (or runs the real ..\\AkiTools one, when it is next door). Windows only (PowerShell).

    python -m unittest discover -s tests
"""
import os, shutil, subprocess, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_TOOLS = os.path.join(os.path.dirname(REPO), 'AkiTools')
SHIPPED = os.path.join(REPO, 'mods', 'modelimport', 'packs')

# Records its arguments; a pack whose folder name starts with "broken" is an error, like validate's own output.
STUB = r'''import os, sys
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calls.txt'), 'a', encoding='utf-8') as fh:
    fh.write('\t'.join(sys.argv[1:]) + '\n')
bad = [p for p in sys.argv[2:] if os.path.basename(p).startswith('broken')]
for p in bad:
    print('%s: ERROR model.bin is 3 bytes' % p)
    print('    fix: rebuild it')
for p in sys.argv[2:]:
    if not p.startswith('--') and p not in ('nomercy', 'vpw2'):
        print('%s: %s' % (p, 'FAILED' if p in bad else 'OK'))
sys.exit(1 if bad else 0)
'''
PROXY = r'''import runpy, sys
sys.argv[0] = %r
runpy.run_path(sys.argv[0], run_name='__main__')
'''


@unittest.skipUnless(os.name == 'nt', 'stage.ps1 is PowerShell')
class StageValidateTest(unittest.TestCase):

    def setUp(self):
        self.depot = tempfile.mkdtemp(prefix='akimods-stage-')
        self.addCleanup(shutil.rmtree, self.depot, True)
        self.root = os.path.join(self.depot, 'AkiMods')
        os.makedirs(os.path.join(self.root, 'common'))
        for f in ('stage.ps1', os.path.join('common', 'games.psd1')):
            shutil.copyfile(os.path.join(REPO, f), os.path.join(self.root, f))
        self.game = os.path.join(self.depot, 'NoMercyRecomp')
        os.makedirs(self.game)
        self.mod('models', "@{ Bundle = $true; Packs = 'packs' }")
        self.packs = os.path.join(self.root, 'mods', 'models', 'packs', 'nomercy')

    def mod(self, name, psd1=None, game='nomercy'):
        if psd1 is not None:
            os.makedirs(os.path.join(self.root, 'mods', name))
            with open(os.path.join(self.root, 'mods', name, 'mod.psd1'), 'w') as fh:
                fh.write(psd1)
        built = os.path.join(self.root, 'build', name, game)
        os.makedirs(built)
        with open(os.path.join(built, name + '.nrm'), 'wb') as fh:
            fh.write(b'NRM')

    def fake_pack(self, game, pid):
        d = os.path.join(self.root, 'mods', 'models', 'packs', game, pid + '.akimodel')
        os.makedirs(d)
        with open(os.path.join(d, 'model.bin'), 'wb') as fh:
            fh.write(b'bin')
        return d

    def tools(self, script):
        t = os.path.join(self.depot, 'AkiTools')
        os.makedirs(os.path.join(t, 'model-templates', 'profiles'))
        for g in ('nomercy', 'vpw2'):
            open(os.path.join(t, 'model-templates', 'profiles', g + '.json'), 'w').close()
        with open(os.path.join(t, 'aki-model.py'), 'w') as fh:
            fh.write(script)
        return t

    def calls(self):
        p = os.path.join(self.depot, 'AkiTools', 'calls.txt')
        if not os.path.isfile(p):
            return []
        with open(p, encoding='utf-8') as fh:
            return [l.rstrip('\n').split('\t') for l in fh]

    def stage(self, *args, path_first=None, path=None):
        # PATH keeps this test's own Python first, so stage.ps1 finds one even where python isn't on PATH.
        first = path_first or os.path.dirname(sys.executable)
        env = dict(os.environ, PATH=path or first + os.pathsep + os.environ.get('PATH', ''))
        ps = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0',
                          'powershell.exe')
        r = subprocess.run([ps, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                            os.path.join(self.root, 'stage.ps1')] + list(args),
                           capture_output=True, text=True, env=env, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def staged(self):
        mods = os.path.join(self.game, 'assets', 'mods')
        return sorted(os.listdir(mods)) if os.path.isdir(mods) else []

    def test_good_packs_are_validated_then_staged(self):
        self.tools(STUB)
        a = self.fake_pack('nomercy', 'hero')
        b = self.fake_pack('nomercy', 'champ')
        # What staging won't copy isn't checked either: a game this mod isn't built for, a pack in a subfolder.
        self.fake_pack('vpw2', 'broken-vpw2')
        self.fake_pack(os.path.join('nomercy', 'old'), 'broken-old')
        rc, out = self.stage()
        self.assertEqual(0, rc, out)
        self.assertEqual([['validate', b, a, '--profile', 'nomercy']], self.calls())
        self.assertIn('model packs: 2 validated by aki-model', out)
        self.assertEqual(['models', 'models.nrm'], self.staged())
        self.assertEqual(['champ.akimodel', 'hero.akimodel'],
                         sorted(os.listdir(os.path.join(self.game, 'assets', 'mods', 'models'))))

    def test_a_game_without_a_profile_is_checked_with_a_warning(self):
        self.tools(STUB)
        os.makedirs(os.path.join(self.depot, 'Wm2kRecomp'))
        self.mod('models', game='wm2k')
        a = self.fake_pack('wm2k', 'hero')
        rc, out = self.stage()
        self.assertEqual(0, rc, out)
        self.assertEqual([['validate', a]], self.calls(), 'no --profile: the one model.bin names')
        self.assertIn('wm2k has no aki-model profile', out)

    def test_any_character_in_a_pack_path(self):
        self.tools(STUB)            # the stub prints each path, like validate: cp1252 output would crash on these
        a = self.fake_pack('nomercy', '\u65e5\u672c-h\u00e9ro')
        rc, out = self.stage()
        self.assertEqual(0, rc, out)
        self.assertEqual([['validate', a, '--profile', 'nomercy']], self.calls())

    def test_a_tool_failure_is_not_blamed_on_the_packs(self):
        self.tools('import sys\nsys.stderr.write("Traceback: boom\\n")\nsys.exit(3)\n')
        self.fake_pack('nomercy', 'hero')
        rc, out = self.stage()
        self.assertNotEqual(0, rc, out)
        self.assertIn('aki-model.py validate failed (exit 3) for the nomercy packs', out)
        self.assertNotIn('refused model packs', out)
        self.assertEqual([], self.staged())

    def test_a_python_that_does_not_start_is_like_no_python(self):
        self.tools(STUB)
        self.fake_pack('nomercy', 'broken-but-unchecked')
        fake = os.path.join(self.depot, 'fakebin')       # a Store alias without Python behind it, as python and py
        os.makedirs(fake)
        for n in ('python.cmd', 'py.cmd'):
            with open(os.path.join(fake, n), 'w') as fh:
                fh.write('@echo Python was not found 1>&2\r\n@exit /b 9009\r\n')
        # First on PATH, it's passed over for the next Python that starts...
        rc, out = self.stage(path_first=fake + os.pathsep + os.path.dirname(sys.executable))
        self.assertNotEqual(0, rc, out)
        self.assertEqual(1, len(self.calls()), out)
        self.assertIn('refused model packs', out)
        # ...and with no other one it's the same as no Python at all.
        system32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
        rc, out = self.stage(path=fake + os.pathsep + system32)
        self.assertEqual(0, rc, out)
        self.assertIn('no working Python', out)
        self.assertEqual(1, len(self.calls()))
        self.assertEqual(['models', 'models.nrm'], self.staged())

    def test_a_broken_pack_stops_the_staging(self):
        self.tools(STUB)
        self.fake_pack('nomercy', 'hero')
        bad = self.fake_pack('nomercy', 'broken-one')
        rc, out = self.stage()
        self.assertNotEqual(0, rc, out)
        self.assertIn('%s: ERROR model.bin is 3 bytes' % bad, out)
        self.assertIn('fix: rebuild it', out)
        self.assertIn('refused model packs for nomercy', out)
        self.assertNotIn('hero.akimodel: OK', out, 'only the errors are repeated')
        self.assertEqual([], self.staged(), 'nothing is copied, not even the .nrm')

    def test_no_akitools_next_door_is_a_warning(self):
        self.fake_pack('nomercy', 'broken-but-unchecked')
        rc, out = self.stage()
        self.assertEqual(0, rc, out)
        self.assertIn('model packs not validated: no', out)
        self.assertEqual(['models', 'models.nrm'], self.staged())

    def test_only_the_staged_mods_packs_are_checked(self):
        self.tools(STUB)
        self.fake_pack('nomercy', 'broken')
        self.mod('tool', '@{ Bundle = $false; Packs = \'packs\' }')
        os.makedirs(os.path.join(self.root, 'mods', 'tool', 'packs', 'nomercy', 'broken.akimodel'))
        rc, out = self.stage('tool')
        self.assertEqual(0, rc, out)                 # a mod that isn't bundled: neither staged nor validated
        self.assertEqual([], self.calls())
        self.mod('plain', '@{ Bundle = $true }')
        rc, out = self.stage('plain')
        self.assertEqual(0, rc, out)                 # no Packs: nothing to validate, no message
        self.assertEqual([], self.calls())
        self.assertNotIn('model packs', out)
        self.assertEqual(['plain.nrm'], self.staged())

    @unittest.skipUnless(os.path.isfile(os.path.join(REAL_TOOLS, 'aki-model.py')), 'needs ..\\AkiTools')
    def test_with_the_real_aki_model(self):
        # The shipped starter packs pass the real validate; a copy with a cut-short model.bin is refused.
        self.tools(PROXY % os.path.join(REAL_TOOLS, 'aki-model.py'))
        shipped = sorted(os.listdir(os.path.join(SHIPPED, 'nomercy')))
        pack = next(p for p in shipped if p.endswith('.akimodel'))
        shutil.copytree(os.path.join(SHIPPED, 'nomercy', pack), os.path.join(self.packs, pack))
        rc, out = self.stage()
        self.assertEqual(0, rc, out)
        self.assertIn('model packs: 1 validated by aki-model', out)
        bad = os.path.join(self.packs, 'cut.akimodel')
        shutil.copytree(os.path.join(SHIPPED, 'nomercy', pack), bad)
        with open(os.path.join(bad, 'model.bin'), 'r+b') as fh:
            fh.truncate(16)
        rc, out = self.stage()
        self.assertNotEqual(0, rc, out)
        self.assertIn(bad + ': ERROR', out)

    @unittest.skipUnless(os.path.isfile(os.path.join(REAL_TOOLS, 'aki-model.py')), 'needs ..\\AkiTools')
    def test_every_shipped_pack_validates(self):
        # A data check on the repo's own starter packs (stage.ps1 runs the same command on them).
        for game in sorted(os.listdir(SHIPPED)):
            d = os.path.join(SHIPPED, game)
            if not os.path.isdir(d):
                continue
            packs = [os.path.join(d, p) for p in sorted(os.listdir(d)) if p.endswith('.akimodel')]
            r = subprocess.run([sys.executable, os.path.join(REAL_TOOLS, 'aki-model.py'), 'validate', '--profile', game]
                               + packs, capture_output=True, text=True, timeout=300)
            self.assertEqual(0, r.returncode, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
