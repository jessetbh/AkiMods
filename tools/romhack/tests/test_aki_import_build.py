"""Tests for aki_import.py's build step (aki-roadmap#228): routes that build #154 packages into Converted\\<game>\\.
Synthetic only: fake ROM images, a synthetic RecompiledFuncs table, stand-in derelink / survey modules where a real
run would need a game's ROM, temp folders. No ROM data, no hacks:

    python -m unittest discover -s tools/romhack/tests
"""
import hashlib
import io
import json
import os
import re
import sys
import types
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_aki_import as t  # noqa: E402  (fake ROMs, the fake patch format, Base)

ai = t.ai
STOCK = t.STOCK
BS = chr(92)

# One code section over 0x1000-0x1030 with two functions (the fake ROM's "game code"); the rest is data.
INL = '''static FuncEntry section_0_main_funcs[] = {
    { .func = func_80000400, .offset = 0x00000000, .rom_size = 0x00000010 },
    { .func = func_80000410, .offset = 0x00000010, .rom_size = 0x00000020 },
};
static SectionTableEntry section_table[] = {
    { .rom_addr = 0x00001000, .ram_addr = 0x80000400, .size = 0x00000030, .funcs = section_0_main_funcs, .num_funcs = 2 },
};
'''
ID_RE = re.compile(r'^[a-z0-9_.-]{1,48}$')
GAME_IDS = {'nomercy': 'wwfnomercy'}


def check_package(test, path, kind):
    """The #154 rules the launcher's install_package checks (packages.cpp parse_manifest / check_package)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        m = json.loads(z.read('aki-package.json'))
        test.assertEqual((m['format'], m['kind']), (1, kind))
        test.assertRegex(m['id'], ID_RE)
        test.assertRegex(m['version'], r'^\d+\.\d+\.\d+$')
        test.assertIn(m['game_id'], ('wcwnwoworldtour', 'wcwnworevenge', 'wwfwm2k', 'vpw64', 'wwfnomercy', 'vpw2'))
        test.assertTrue(m['display_name'] and m['authors'])
        test.assertFalse([n for n in names if n.lower().endswith(('.z64', '.v64', '.n64'))], 'a package never holds a ROM')
        folder = m['payload']['folder']
        test.assertTrue([n for n in names if n.startswith(folder)])
        if kind == 'conversion':
            test.assertTrue(re.match(r'^[0-9a-f]{40}$', m['base_rom']['sha1'][0]))
            aki = json.loads(z.read(folder + 'aki.json'))
            test.assertEqual((aki['format'], aki['format_version'], aki['id']), ('aki-conversion', 1, m['id']))
            patch = aki['components'].get('patch')
            if patch:
                nrm = zipfile.ZipFile(io.BytesIO(z.read(folder + patch['file'])))
                mod = json.loads(nrm.read('mod.json'))
                test.assertEqual(mod['id'], m['id'])     # the patch .nrm's mod id is the conversion id
                test.assertEqual(patch['mod_id'], m['id'])
            return m, aki, z.read(folder + patch['file']) if patch else None
        if kind == 'texture-pack':
            test.assertIn(folder + 'rt64.json', names)
        return m, None, None


class BuildBase(t.Base):
    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.dir, 'Converted')
        self.repo = os.path.join(self.dir, 'repo')
        self.write('repo/RecompiledFuncs/recomp_overlays.inl', INL.encode())

    def build(self, paths, **job):
        job.setdefault('repos', {'nomercy': self.repo})
        job.update(build=True, out=self.out, work=os.path.join(self.dir, 'work'))
        return self.run_job(paths, **job)

    def build_one(self, paths, fakes=None, **job):
        if fakes:
            real = ai.Builder.mod

            def mod(builder, name):
                return fakes[name] if name in fakes else real(builder, name)
            ai.Builder.mod = mod
            try:
                items, _ = self.build(paths, **job)
            finally:
                ai.Builder.mod = real
        else:
            items, _ = self.build(paths, **job)
        self.assertEqual(len(items), 1, items)
        return items[0]


def fake_derelink(codes=None, fail=None, layout=True):
    """A stand-in derelink: main(argv) writes a patch .nrm (mod.json + a BPS from make_patch_mod) and, optionally,
    the code watch table next to it."""
    import make_patch_mod as mpm
    calls = []

    def main(argv):
        calls.append(argv)
        if fail:
            sys.exit(fail)
        args = dict(zip(argv[1::2], argv[2::2]))
        base = open(args['--base'], 'rb').read()
        hack = open(args['--hack'], 'rb').read()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('mod.json', json.dumps({'id': args['--id'], 'game_id': 'wwfnomercy', 'version': '1.0.0'}))
            z.writestr('patch.bps', mpm.bps_encode(base, hack))
            z.writestr('relink.json', '{}')
        open(args['--out'], 'wb').write(buf.getvalue())
        if codes:
            open(os.path.join(os.path.dirname(args['--out']), 'codes.txt'), 'w').write(codes)
        print('wrote', args['--out'])
    return types.SimpleNamespace(main=main, find_layout=lambda rom: 'x.json' if layout else None,
                                 CODE_WATCH_FILE='codes.txt', calls=calls)


class HackRoutes(BuildBase):
    def test_in_place_data_hack_builds_with_make_patch_mod(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 0x5A                    # data, outside the code section
        r = self.build_one([self.write('in/My Hack.z64', bytes(hack))])
        self.assertEqual((r['status'], r['converter'], r['survey_tier'], r['aki_tier']),
                         ('converted', 'make_patch_mod', 'patch-data', 'untested'), r)
        self.assertEqual(r['package'], os.path.join(self.out, 'nomercy', 'my-hack-1.0.0.zip'))
        m, aki, nrm = check_package(self, r['package'], 'conversion')
        self.assertEqual(m['base_rom']['sha1'], [hashlib.sha1(STOCK).hexdigest()])
        self.assertIn('Untested', m['short_description'])
        self.assertEqual((aki['survey']['aki_tier'], aki['survey']['tier'], aki['survey']['converter']),
                         ('untested', 'patch-data', 'make_patch_mod'))
        import make_patch_mod as mpm
        patch = zipfile.ZipFile(io.BytesIO(nrm)).read('patch.bps')
        self.assertEqual(mpm.bps_apply(STOCK, patch), bytes(hack))   # the patch rebuilds the hack from the stock ROM
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', 'build')))

    def test_patch_file_builds_from_the_applied_rom(self):
        r = self.build_one([self.write('in/hack.bps', t.make_patch(STOCK, [(0x2100, 7)]))])
        self.assertEqual((r['status'], r['kind'], r['converter']), ('converted', 'patch', 'make_patch_mod'))
        check_package(self, r['package'], 'conversion')

    def test_without_recompiled_tables_derelink_builds_it(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        dl = fake_derelink(codes='# rom: nomercy-rev1\n')
        r = self.build_one([self.write('in/x.z64', bytes(hack))], fakes={'derelink': dl}, repos={})
        self.assertEqual((r['status'], r['converter'], r['survey_tier']), ('converted', 'derelink', 'unknown'))
        _, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual(aki['components']['codes'], {'file': 'codes.txt', 'format': 'aki-codes', 'rom': 'nomercy-rev1'})
        self.assertEqual(dl.calls[0][dl.calls[0].index('--id') + 1], 'x')
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', 'build', 'derelink-x')))  # hack ROM removed

    def test_moved_code_tier_goes_to_derelink(self):
        survey = types.SimpleNamespace(Stock=lambda **kw: None,
                                       survey_rom=lambda name, rom, stock: {'tier': 'relinked', 'game': 'nomercy',
                                                                            'reasons': ['moved']})
        dl = fake_derelink()
        hack = bytearray(STOCK)
        hack[0x1004] ^= 1
        r = self.build_one([self.write('in/relinked.z64', bytes(hack))], fakes={'aki_hack': survey, 'derelink': dl})
        self.assertEqual((r['status'], r['converter'], r['survey_tier']), ('converted', 'derelink', 'relinked'))

    def test_make_patch_mod_refusal_falls_back_to_derelink(self):
        hack = bytearray(STOCK)
        hack[0x1004:0x1008] = (0x08000000 | (0x80001000 >> 2) & 0x3FFFFFF).to_bytes(4, 'big')  # j out of the function
        # The survey calls it in-place code, but make_patch_mod's own check finds the escaping jump and refuses.
        survey = types.SimpleNamespace(Stock=lambda **kw: None, survey_rom=lambda name, rom, stock: {
            'tier': 'patch-code', 'game': 'nomercy', 'reasons': []})
        dl = fake_derelink()
        r = self.build_one([self.write('in/escapes.z64', bytes(hack))], fakes={'derelink': dl, 'aki_hack': survey})
        self.assertEqual((r['status'], r['converter']), ('converted', 'derelink'), r)
        self.assertIn('full recompile', r['fallback'])

    def test_escaping_jump_is_restructured_and_goes_to_derelink(self):
        hack = bytearray(STOCK)
        hack[0x1004:0x1008] = (0x08000000 | (0x80001000 >> 2) & 0x3FFFFFF).to_bytes(4, 'big')
        r = self.build_one([self.write('in/escapes.z64', bytes(hack))], fakes={'derelink': fake_derelink()})
        self.assertEqual((r['status'], r['survey_tier'], r['converter']), ('converted', 'restructured', 'derelink'))

    def test_derelink_failure_is_a_failed_item(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        r = self.build_one([self.write('in/x.z64', bytes(hack))], fakes={'derelink': fake_derelink(fail='cannot place')},
                           repos={})
        self.assertEqual(r['status'], 'failed')
        self.assertIn('cannot place', r['why'])
        self.assertFalse(os.path.isdir(os.path.join(self.out, 'nomercy')))

    def test_no_layout_for_the_port_rom_fails(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        r = self.build_one([self.write('in/x.z64', bytes(hack))], fakes={'derelink': fake_derelink(layout=False)},
                           repos={})
        self.assertEqual(r['status'], 'failed')
        self.assertIn('no layout', r['why'])

    def test_different_base_is_refused(self):
        survey = types.SimpleNamespace(Stock=lambda **kw: None, survey_rom=lambda name, rom, stock: {
            'tier': 'different-base', 'game': 'nomercy', 'reasons': ['only 10% of the port functions found']})
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        r = self.build_one([self.write('in/x.z64', bytes(hack))], fakes={'aki_hack': survey})
        self.assertEqual(r['status'], 'failed')
        self.assertIn('different-base', r['why'])

    def test_port_rom_missing_is_needs_rom(self):
        rev0 = self.write('in/nm10.z64', t.REV0)
        patch = self.write('in/somehack.xdelta', t.make_patch(t.REV0, [(0x2000, 9)]))
        items, _ = self.build([patch, rev0], roms={})
        by = {os.path.basename(i['item']): i for i in items}
        self.assertEqual(by['somehack.xdelta']['status'], 'needs-rom')
        self.assertIn('WWF No Mercy ROM the port uses', by['somehack.xdelta']['needs'])

    def catalog(self, entries):
        return self.write('catalog.json', json.dumps({'entries': entries}).encode())

    def test_catalog_tier_needs_the_tested_output(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 3
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        # The stand-in derelink's patch makes exactly the hack ROM: that's the tested output here.
        cat = self.catalog([{'id': 'known-hack', 'name': 'Known Hack', 'game': 'nomercy', 'tier': 'verified',
                             'converter': 'derelink', 'known_issues': ['aki-roadmap#1'], 'note': 'Mind the gap.',
                             'inputs': {'sha1': [sha1]}, 'outputs': {'sha1': [sha1]}}])
        r = self.build_one([self.write('in/k.z64', bytes(hack))], fakes={'derelink': fake_derelink()}, catalog=cat)
        self.assertEqual((r['status'], r['id'], r['converter'], r['aki_tier'], r['aki_tier_label']),
                         ('converted', 'known-hack', 'derelink', 'verified', 'Verified'))
        m, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual((aki['survey']['catalog'], aki['survey']['output_sha1'], aki['survey']['known_issues']),
                         ('known-hack', sha1, ['aki-roadmap#1']))
        self.assertEqual(m['short_description'], 'Verified Mind the gap.')   # no tier texts in this catalog

    def test_known_input_built_differently_is_untested_with_a_note(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 3
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        cat = self.catalog([{'id': 'known-hack', 'name': 'Known Hack', 'game': 'nomercy', 'tier': 'verified',
                             'inputs': {'sha1': [sha1]}, 'outputs': {'sha1': ['0' * 40]}}])
        r = self.build_one([self.write('in/k.z64', bytes(hack))], catalog=cat)
        self.assertEqual((r['status'], r['id'], r['aki_tier']), ('converted', 'known-hack', 'untested'))
        self.assertIn('built differently', r['tier_note'])
        _, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual(aki['survey']['aki_tier_label'], 'Untested')
        self.assertNotIn('known_issues', aki['survey'])

    def test_output_match_alone_gives_the_tier(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 7
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        cat = self.catalog([{'id': 'other', 'name': 'Other', 'game': 'nomercy', 'tier': 'experimental',
                             'outputs': {'sha1': [sha1]}}])
        r = self.build_one([self.write('in/x.z64', bytes(hack))], catalog=cat)   # make_patch_mod: output = hack
        self.assertEqual((r['aki_tier'], r['id']), ('experimental', 'x'))

    def test_codes_only_conversion_matches_by_codes(self):
        bundle = Pj64Bundles.bundle(self)
        first = self.build_one([bundle])
        codes_sha1 = first['codes_sha1']
        cat = self.catalog([{'id': 'mymod', 'name': 'My Mod', 'game': 'nomercy', 'tier': 'verified',
                             'outputs': {'codes_sha1': [codes_sha1]}}])
        os.remove(first['package'])
        r = self.build_one([bundle], catalog=cat)
        self.assertEqual(r['aki_tier'], 'verified')

    def test_patch_with_other_codes_is_untested(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 3
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        cat = self.catalog([{'id': 'k', 'name': 'K', 'game': 'nomercy', 'tier': 'verified', 'converter': 'derelink',
                             'outputs': {'sha1': [sha1], 'codes_sha1': ['1' * 40]}}])
        r = self.build_one([self.write('in/k.z64', bytes(hack))], fakes={'derelink': fake_derelink(codes='x\n')},
                           catalog=cat)
        self.assertEqual(r['aki_tier'], 'untested')

    def test_shared_codes_hash_matches_nothing(self):
        bundle = Pj64Bundles.bundle(self)
        first = self.build_one([bundle])
        h = first['codes_sha1']
        cat = self.catalog([{'id': 'a', 'name': 'A', 'game': 'nomercy', 'tier': 'verified', 'outputs': {'codes_sha1': [h]}},
                            {'id': 'b', 'name': 'B', 'game': 'nomercy', 'tier': 'experimental', 'outputs': {'codes_sha1': [h]}}])
        os.remove(first['package'])
        self.assertEqual(self.build_one([bundle], catalog=cat)['aki_tier'], 'untested')

    def test_damaged_patch_fails_and_wrong_rom_names_the_base(self):
        import make_patch_mod as mpm
        pf = ai.load_patchfmt()
        good = mpm.bps_encode(t.REV0, bytes(t.REV0[:0x2000]) + b'X' + bytes(t.REV0[0x2001:]))
        r = self.one([self.write('in/w.bps', good)], patchfmt=pf)          # only the Rev 1 port ROM given
        self.assertEqual(r['status'], 'needs-rom')
        self.assertEqual(r['needs'], 'WWF No Mercy (USA) 1.0')               # from the BPS footer's source CRC
        bad = bytearray(good)
        bad[-1] ^= 0xFF                                                       # its own checksum broken
        r = self.one([self.write('in/d.bps', bytes(bad))], patchfmt=pf)
        self.assertEqual(r['status'], 'failed')

    def test_shipped_catalog_loads_and_holds_hashes_only(self):
        cat = ai.load_catalog()
        self.assertTrue(len(cat.outputs) >= 30 and len(cat) >= 5)
        raw = json.load(open(ai.DEFAULT_CATALOG, encoding='utf-8'))
        self.assertEqual(set(raw['tiers']), {'verified', 'experimental', 'unsupported', 'untested'})
        for e in raw['entries']:
            self.assertRegex(e['id'], ID_RE)
            self.assertIn(e['tier'], raw['tiers'])
            self.assertIn(e['game'], ai.GAMES)
            for hashes in list(e.get('inputs', {}).values()) + list(e['outputs'].values()):
                for h in hashes:
                    self.assertRegex(h, r'^[0-9A-F]{40}$')
        self.assertLess(os.path.getsize(ai.DEFAULT_CATALOG), 64 << 10)

    def test_catalog_off(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        self.assertEqual(self.build_one([self.write('in/a.z64', bytes(hack))], catalog='')['aki_tier'], 'untested')

    def test_ledger_records_the_package_and_a_rerun_is_have_it(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 9
        path = self.write('in/h.z64', bytes(hack))
        ledger = os.path.join(self.dir, 'ledger.json')
        r = self.build_one([path], ledger=ledger)
        entry = list(json.load(open(ledger))['entries'].values())[0]
        self.assertEqual((entry['status'], entry['package'], entry['converter']), ('converted', r['package'], 'make_patch_mod'))
        self.assertEqual(self.build_one([path], ledger=ledger)['status'], 'have-it')

    def test_rom_bytes_are_never_emitted(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        items, _ = self.build([self.write('in/a.z64', bytes(hack))])
        self.assertFalse([k for k in items[0] if k.startswith('_')])


def png(seed):
    return b'\x89PNG\r\n\x1a\n' + bytes([seed]) * 32


class TexturePacks(BuildBase):
    def test_rice_folder_becomes_a_texture_package(self):
        for i in range(6):
            self.write('in/HD Pack/WWF NO MERCY#%08X#0#2_all.png' % i, png(i))
        self.write('in/HD Pack/readme.txt', b'notes')
        r = self.build_one([os.path.join(self.dir, 'in', 'HD Pack')])
        # The pack names the ROM by its header name; the fake port ROM's header is "WWF No Mercy".
        self.assertEqual((r['status'], r['converter'], r['game'], r['images']), ('converted', 'texture-pack', 'nomercy', 6))
        m, _, _ = check_package(self, r['package'], 'texture-pack')
        with zipfile.ZipFile(r['package']) as z:
            cfg = json.loads(z.read('textures/rt64.json'))
            self.assertEqual(cfg['configuration']['autoPath'], 'rice')
            self.assertNotIn('textures/readme.txt', z.namelist())

    def test_jabo_pack_needs_a_game_then_builds(self):
        for i in range(5):
            self.write('in/jabo/%032x.png' % i, png(i))
        r = self.build_one([os.path.join(self.dir, 'in', 'jabo')])
        self.assertEqual(r['status'], 'needs-game')
        r = self.build_one([os.path.join(self.dir, 'in', 'jabo')], game='nomercy')
        self.assertEqual((r['status'], r['naming']), ('converted', 'jabo'))
        check_package(self, r['package'], 'texture-pack')

    def test_zipped_rt64_pack_keeps_its_rt64_json(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('Pack/rt64.json', '{"configuration": {"autoPath": "rt64"}, "textures": []}')
            for i in range(5):
                z.writestr('Pack/%016x.png' % i, png(i))
        r = self.build_one([self.write('in/pack.zip', buf.getvalue())], game='nomercy')
        self.assertEqual(r['status'], 'converted')
        with zipfile.ZipFile(r['package']) as z:
            self.assertEqual(json.loads(z.read('textures/rt64.json'))['configuration']['autoPath'], 'rt64')


class Pj64Bundles(BuildBase):
    def bundle(self, root='in/My Mod'):
        # The fake port ROM's Project64 section: CRC1-CRC2 from its header (zeros) and country 'E' (0x45).
        key = '00000000-00000000-C:45'
        self.write(root + '/Project64.cht', '\n'.join([
            '[%s]' % key, 'Name=WWF No Mercy',
            'Cheat0="MyMod' + BS + 'Arena",810500E8 0933',
            'Cheat1="Other' + BS + 'Thing",8109940A FFFF', '']).encode('latin-1'))
        for i in range(6):
            self.write(root + '/Textures/WWF NO MERCY#%08X#0#2_all.png' % i, png(i))
        return os.path.join(self.dir, *root.split('/'))

    def test_bundle_builds_a_conversion_and_its_texture_package(self):
        r = self.build_one([self.bundle()])
        self.assertEqual((r['status'], r['converter'], r['game'], r['id']), ('converted', 'import-pj64', 'nomercy', 'my-mod'),
                         r)
        self.assertEqual(len(r['packages']), 2)
        tex = [p for p in r['packages'] if 'textures' in os.path.basename(p)][0]
        check_package(self, tex, 'texture-pack')
        m, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual(m['requires']['packages'], ['my-mod-textures'])
        self.assertEqual(aki['components']['textures']['package'], 'my-mod-textures')
        self.assertEqual(aki['survey']['converter'], 'import-pj64')
        with zipfile.ZipFile(r['package']) as z:
            codes = z.read('conversion/codes.txt').decode()
        self.assertIn('810500E8 0933', codes)          # the mod's own cheat
        self.assertNotIn('8109940A', codes)            # not the database's other cheats

    def test_zipped_bundle_is_unpacked_and_removed(self):
        folder = self.bundle('src/My Mod')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for d, _, fs in os.walk(folder):
                for f in fs:
                    p = os.path.join(d, f)
                    z.write(p, 'My Mod/' + os.path.relpath(p, folder).replace(os.sep, '/'))
        r = self.build_one([self.write('in/My Mod.zip', buf.getvalue())])
        self.assertEqual((r['status'], r['game']), ('converted', 'nomercy'), r)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', 'build')))   # unpacked bundle removed

    def test_bundle_for_no_known_game_needs_one(self):
        self.write('in/Mystery/Project64.cht', b'[12345678-9ABCDEF0-C:45]\nName=Something\n')
        for i in range(6):
            self.write('in/Mystery/Textures/X#%08X#0#2_all.png' % i, png(i))
        r = self.build_one([os.path.join(self.dir, 'in', 'Mystery')])
        self.assertEqual(r['status'], 'needs-game')


class ReviewFixes(BuildBase):
    def test_long_names_get_distinct_ids_and_packages(self):
        name = 'A' * 60
        paths = []
        for i in range(2):
            hack = bytearray(STOCK)
            hack[0x2000 + i] ^= 1
            paths.append(self.write('in/%d/%s.z64' % (i, name), bytes(hack)))
        items, _ = self.build(paths)
        ids = [i['id'] for i in items]
        self.assertEqual(len(set(ids)), 2, ids)
        self.assertTrue(all(len(i) <= 48 for i in ids))
        self.assertEqual(len({i['package'] for i in items}), 2)

    def test_existing_package_from_another_input_is_not_overwritten(self):
        a, b = bytearray(STOCK), bytearray(STOCK)
        a[0x2000] ^= 1
        b[0x2000] ^= 2
        first = self.build_one([self.write('in/1/Hack.z64', bytes(a))])
        second = self.build_one([self.write('in/2/Hack.z64', bytes(b))])     # another batch, no ledger
        self.assertNotEqual(first['package'], second['package'])
        self.assertEqual(second['id'], 'hack-2')
        again = self.build_one([self.write('in/3/Hack.z64', bytes(a))])      # the same input: rebuilt in place
        self.assertEqual(again['package'], first['package'])

    def test_package_ids_follow_the_launcher_rules(self):
        self.assertEqual(ai.package_id('_hack'), 'hack')
        self.assertEqual(ai.package_id('con'), 'con-pkg')
        self.assertEqual(ai.package_id('nul.thing'), 'nul.thing-pkg')
        self.assertEqual(ai.package_id('...'), 'import')
        self.assertRegex(ai.package_id('x' * 80 + '.'), ID_RE)

    def test_bad_version_is_refused(self):
        with self.assertRaises(ai.ImportError_):
            self.build([], version='1.0')

    def test_catalog_make_patch_mod_without_tables_uses_derelink(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 5
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        cat = self.write('catalog.json', json.dumps({'entries': [{'id': 'k', 'name': 'K', 'game': 'nomercy',
            'tier': 'verified', 'converter': 'make_patch_mod', 'inputs': {'sha1': [sha1]}}]}).encode())
        r = self.build_one([self.write('in/k.z64', bytes(hack))], fakes={'derelink': fake_derelink()}, catalog=cat,
                           repos={})
        self.assertEqual((r['status'], r['converter']), ('converted', 'derelink'))

    def test_byte_swapped_port_rom_copy_is_removed(self):
        port = self.write('roms/nm.v64', t.v64(STOCK))
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        r = self.build_one([self.write('in/x.z64', bytes(hack))], roms={'nomercy': port},
                           fakes={'derelink': fake_derelink()}, repos={})
        self.assertEqual(r['status'], 'converted')
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', 'build')))

    def test_texture_zip_with_backslashes_and_an_uppercase_rt64_json(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('RT64.JSON', '{"configuration": {"autoPath": "rt64"}, "textures": []}')
            for i in range(5):
                z.writestr('sub\\%016x.png' % i, png(i))
        r = self.build_one([self.write('in/p.zip', buf.getvalue())], game='nomercy')
        self.assertEqual(r['status'], 'converted', r)
        with zipfile.ZipFile(r['package']) as z:
            names = z.namelist()
        self.assertIn('textures/rt64.json', names)
        self.assertFalse([n for n in names if '\\' in n])

    def test_failed_zip_write_leaves_no_tmp(self):
        path = os.path.join(self.out, 'nomercy', 'x-1.0.0.zip')
        with self.assertRaises(OSError):
            ai.zip_write(path, {'a.bin': os.path.join(self.dir, 'missing.bin')})
        self.assertEqual(os.listdir(os.path.dirname(path)), [])


class RenamedHeaders(BuildBase):
    def renamed(self):
        hack = bytearray(STOCK)
        hack[0x3B:0x3F] = b'NVFE'           # a hack that renamed its game code, as VPW2 Freem does
        hack[0x2000] ^= 1
        return bytes(hack)

    def test_patch_result_takes_its_base_game(self):
        r = self.one([self.write('in/f.bps', t.make_patch(STOCK, [(0x3B, ord('N')), (0x3C, ord('V')),
                                                                  (0x3D, ord('F')), (0x3E, ord('E'))]))])
        self.assertEqual((r['status'], r['game']), ('ready', 'nomercy'))
        self.assertIn('NVFE', r['header_note'])

    def test_rom_matched_by_code_similarity(self):
        r = self.one([self.write('in/f.z64', self.renamed())])
        self.assertEqual((r['status'], r['game']), ('ready', 'nomercy'))


class Layouts(BuildBase):
    def layout(self, rom=STOCK):
        lay = {'game': 'nomercy', 'base_sha1': hashlib.sha1(rom).hexdigest(), 'extra_code': [],
               'sections': [{'name': 'main', 'rom': 0x1000, 'text': 0x80000400, 'data': 0x80000430,
                             'bss': 0x80000430, 'end': 0x80000440, 'funcs': [[0, 0x10], [0x10, 0x20]], 'slot': None}]}
        d = os.path.join(self.dir, 'layouts')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'nomercy.json'), 'w') as f:
            json.dump(lay, f)
        return d

    def test_layout_sections_match_recompiled_tables(self):
        import make_patch_mod as mpm
        inl = mpm.load_sections(os.path.join(self.repo, 'RecompiledFuncs', 'recomp_overlays.inl'))
        lay = mpm.layout_sections(os.path.join(self.layout(), 'nomercy.json'))
        strip = lambda secs: [(rom, ram, size, [(o, z) for _, o, z in fs]) for _, rom, ram, size, fs in secs]
        self.assertEqual(strip(lay), strip(inl))

    def test_build_without_a_repo_uses_the_layout_for_make_patch_mod(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 0x11
        r = self.build_one([self.write('in/h.z64', bytes(hack))], repos={}, layouts=self.layout())
        self.assertEqual((r['status'], r['converter'], r['survey_tier']), ('converted', 'make_patch_mod', 'patch-data'))

    def test_make_patch_mod_cli_layout_auto_and_wrong_rom(self):
        import make_patch_mod as mpm
        d = self.layout()
        hack = bytearray(STOCK)
        hack[0x2000] ^= 1
        base, h = self.stock_path, self.write('h.z64', bytes(hack))
        out = os.path.join(self.dir, 'h.nrm')
        saved = mpm.LAYOUTS_DIR
        mpm.LAYOUTS_DIR = d
        try:
            with open(os.devnull, 'w') as null:
                stdout, sys.stdout = sys.stdout, null
                try:
                    mpm.main(['--base', base, '--hack', h, '--game-id', 'wwfnomercy', '--id', 'h', '--name', 'H',
                              '--layout', 'auto', '--out', out, '--repaired', os.path.join(self.dir, 'none.txt')])
                    self.assertTrue(os.path.isfile(out))
                    with self.assertRaises(SystemExit):
                        mpm.main(['--base', h, '--hack', h, '--game-id', 'wwfnomercy', '--id', 'h', '--name', 'H',
                                  '--layout', 'nomercy'])
                finally:
                    sys.stdout = stdout
        finally:
            mpm.LAYOUTS_DIR = saved

    def test_layout_lookup_is_strict(self):
        import make_patch_mod as mpm
        d = self.layout()
        with open(os.path.join(d, 'broken.json'), 'w') as f:
            f.write('{not json')
        with open(os.path.join(d, 'list.json'), 'w') as f:
            f.write('[1, 2]')
        self.assertEqual(os.path.basename(mpm.find_layout(STOCK, d)), 'nomercy.json')
        self.assertIsNone(mpm.find_layout(t.REV0, d))
        with self.assertRaises(mpm.PatchModError):
            mpm.resolve_layout('auto', t.REV0, d)
        with self.assertRaises(mpm.PatchModError):
            mpm.resolve_layout('nomercy', t.REV0, d)          # made for another ROM
        lay = json.load(open(os.path.join(d, 'nomercy.json')))
        del lay['base_sha1']
        nosha = os.path.join(self.dir, 'nosha.json')
        json.dump(lay, open(nosha, 'w'))
        with self.assertRaises(mpm.PatchModError):
            mpm.resolve_layout(nosha, STOCK, d)                 # unverifiable
        self.assertTrue(mpm.resolve_layout('nomercy', STOCK, d).endswith('nomercy.json'))

    def test_layout_extra_code_is_a_section(self):
        import make_patch_mod as mpm
        lay = {'sections': [], 'extra_code': [{'section': 'main', 'ram': 0x80002000, 'rom': 0x3000, 'size': 0x40,
                                               'funcs': [[0, 0x20], [0x20, 0x20]]}]}
        self.assertEqual(mpm.layout_sections(lay), [('main_extra_3000', 0x3000, 0x80002000, 0x40,
                                                     [('func_80002000', 0, 0x20), ('func_80002020', 0x20, 0x20)])])

    def test_repaired_names_map_to_layout_names(self):
        import make_patch_mod as mpm
        inl = os.path.join(self.repo, 'RecompiledFuncs', 'recomp_overlays.inl')
        self.assertEqual(mpm.repaired_by_vram({'func_80000410', 'unknown_sym'}, inl), {'func_80000410', 'unknown_sym'})
        named = INL.replace('func_80000410', 'game_main')
        p = self.write('named.inl', named.encode())
        self.assertEqual(mpm.repaired_by_vram({'game_main'}, p), {'func_80000410'})

    def test_masked_words_without_numpy(self):
        aki_hack = ai.load_module('aki_hack')
        data = bytes((i * 37 + 11) & 0xFF for i in range(4096))

        def ref(rom):
            out = bytearray()
            for i in range(0, len(rom) // 4 * 4, 4):
                w = int.from_bytes(rom[i:i + 4], 'big')
                op = w >> 26
                w = w & 0xFC000000 if op in (2, 3) else (w if op == 0 else w & 0xFFFF0000)
                out += w.to_bytes(4, 'big')
            return bytes(out)
        self.assertEqual(aki_hack.masked_words(data), ref(data))
        self.assertEqual(aki_hack.masked_words(data + b'\x01\x02'), ref(data))
        self.assertEqual(aki_hack.masked_words(data, limit=100), ref(data[:100]))


class RealPatchfmt(BuildBase):
    """The importer with patchfmt.py (not the test's fake format)."""

    def test_patchfmt_is_the_engine(self):
        self.assertEqual(ai.load_patchfmt().__name__, 'patchfmt')

    def test_bps_file_builds_with_patchfmt(self):
        import make_patch_mod as mpm
        hack = bytearray(STOCK)
        hack[0x2000:0x2004] = b'HACK'
        items, done = self.build([self.write('in/Real.bps', mpm.bps_encode(STOCK, bytes(hack)))],
                                 patchfmt=ai.load_patchfmt())
        self.assertEqual((items[0]['status'], items[0]['format'], items[0]['converter']),
                         ('converted', 'bps', 'make_patch_mod'), items[0])
        self.assertEqual(done['patch_engine_name'], 'patchfmt')
        _, _, nrm = check_package(self, items[0]['package'], 'conversion')
        self.assertEqual(mpm.bps_apply(STOCK, zipfile.ZipFile(io.BytesIO(nrm)).read('patch.bps')), bytes(hack))

    def test_rup_and_ppf_are_refused_by_name(self):
        for name, data in (('a.rup', b'NINJA2' + bytes(32)), ('b.ppf', b'PPF30' + bytes(32))):
            r = self.one([self.write('in/' + name, data)], patchfmt=ai.load_patchfmt())
            self.assertEqual(r['status'], 'unsupported', r)


if __name__ == '__main__':
    unittest.main()
