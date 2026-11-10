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
import shutil
import sys
import types
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))   # make_patch_mod for the stand-in derelink
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
            tex = aki['components'].get('textures')
            if tex:
                # The game reads a conversion's textures from its own folder (dir) or conversions\_packages\ (package);
                # the launcher installs a texture-pack package to mods\, so a conversion never names one.
                test.assertNotIn('package', tex)
                test.assertTrue([n for n in names if n.startswith(folder + tex['dir'] + '/')])
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


# A synthetic Rev 0 -> Rev 1 derelink map: main moved -0x90 up to 0x80100000, the heap above in place, expansion RAM
# identity. 0x80180000 isn't covered (a code there can't be translated).
REV0_MAP = {'main': [[0x80000400, 0x80100000, -0x90, 'text'], [0x80200000, 0x80400000, 0, 'heap']], 'overlays': {},
            'identity': [[0x80400000, 0x80800000]]}
REV0_KEY = '4E4B0640-1B49BCFB-C:45'    # No Mercy (U) v1.0's Project64 section


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
        if argv[0] == 'map':        # derelink map: the hack ROM's RAM ranges -> the port's
            open(args['--hack'], 'rb').close()
            with open(args['--out'], 'w') as f:
                json.dump(REV0_MAP, f)
            print('wrote', args['--out'])
            return
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

    def test_derelink_recipe_overrides_different_base(self):
        # 9 Underground: World Tour rev 1 code on the rev 0 port. The survey calls it another release, but the catalog
        # recipe for this exact ROM says derelink, as the tested pack was built; without the recipe it stays refused.
        survey = types.SimpleNamespace(Stock=lambda **kw: None, survey_rom=lambda name, rom, stock: {
            'tier': 'different-base', 'game': None, 'reasons': ['only 35% of the port functions found']})
        hack = bytearray(STOCK)
        hack[0x2000] ^= 5
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        entry = {'id': 'u9', 'name': 'U9', 'game': 'nomercy', 'tier': 'experimental', 'converter': 'derelink',
                 'inputs': {'sha1': [sha1]}, 'outputs': {'sha1': [sha1]}}
        fakes = {'aki_hack': survey, 'derelink': fake_derelink()}
        r = self.build_one([self.write('in/u9.z64', bytes(hack))], fakes=fakes, catalog=self.catalog([entry]))
        self.assertEqual((r['status'], r['converter'], r['survey_tier'], r['aki_tier']),
                         ('converted', 'derelink', 'different-base', 'experimental'))
        for other in ({'converter': 'make_patch_mod'}, {'inputs': {}}):
            r = self.build_one([self.write('in/u9.z64', bytes(hack))], fakes=fakes,
                               catalog=self.catalog([dict(entry, **other)]))
            self.assertEqual(r['status'], 'failed', other)
            self.assertIn('different-base', r['why'])

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
        # aki-roadmap#268: the entry's id and name, as an input SHA-1 match gives, and a tier that agrees with aki_tier.
        self.assertEqual((r['aki_tier'], r['tier'], r['catalog'], r['id'], r['name']),
                         ('experimental', 'experimental', 'other', 'other', 'Other'))
        m, aki, _ = check_package(self, r['package'], 'conversion')     # the patch's mod id follows the new id
        self.assertEqual((m['id'], m['display_name'], aki['name'], os.path.basename(r['package'])),
                         ('other', 'Other', 'Other', 'other-1.0.0.zip'))
        # An id the ledger holds (another conversion called "other") stays unique.
        ledger = self.write('ledger.json', json.dumps({'entries': {'K': {'id': 'other'}}}).encode())
        shutil.rmtree(self.out)
        r = self.build_one([self.write('in/x.z64', bytes(hack))], catalog=cat, ledger=ledger)
        self.assertEqual((r['id'], r['aki_tier']), ('other-2', 'experimental'))
        check_package(self, r['package'], 'conversion')

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

    def test_have_it_carries_the_built_package(self):
        # aki-roadmap#272: a launcher reinstalls a removed conversion from the package the ledger names.
        hack = bytearray(STOCK)
        hack[0x2000] ^= 9
        path = self.write('in/h.z64', bytes(hack))
        ledger = os.path.join(self.dir, 'ledger.json')
        first = self.build_one([path], ledger=ledger)
        again = self.build_one([path], ledger=ledger)
        self.assertEqual((again['status'], again['package'], again['packages'], again['id']),
                         ('have-it', first['package'], [first['package']], first['id']))

    def test_a_deleted_package_is_converted_again_under_its_id(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 9
        path = self.write('in/h.z64', bytes(hack))
        ledger = os.path.join(self.dir, 'ledger.json')
        first = self.build_one([path], ledger=ledger)
        os.remove(first['package'])
        again = self.build_one([path], ledger=ledger)
        self.assertEqual((again['status'], again['id']), ('converted', first['id']))
        self.assertTrue(os.path.isfile(again['package']))
        self.assertEqual(self.build_one([path], ledger=ledger)['status'], 'have-it')

    def test_a_plan_never_drops_the_packages_a_build_recorded(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 9
        path = self.write('in/h.z64', bytes(hack))
        ledger = os.path.join(self.dir, 'ledger.json')
        first = self.build_one([path], ledger=ledger)
        os.remove(first['package'])
        items, _ = self.run_job([path], ledger=ledger, record=True)       # plan --record: ready, not have-it
        self.assertEqual(items[0]['status'], 'ready')
        entry = list(json.load(open(ledger))['entries'].values())[0]
        self.assertEqual(entry['package'], first['package'])
        self.assertEqual(self.build_one([path], ledger=ledger)['status'], 'converted')

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

    def test_nested_archive_keeps_the_pack_whole(self):
        # aki-roadmap#268 (TNA vs ROH): a small zip among a wrestler's shirt textures split the pack into one package
        # per wrestler folder and lost the loose images of the folders above it (Raven\Attire 3).
        rice = 'in/TNA/Pack/%s/WWF No Mercy#%08X#2#1#0C292BE7_ciByRGBA.png'
        n = 0
        for folder, count in (('Abyss', 6), ('Raven/Attire 3', 3), ('Raven/Attire 3/T-Shirts/New look', 2),
                              ('Raven/Attire 3/T-Shirts/clockwork', 4)):
            for _ in range(count):
                self.write(rice % (folder, n), png(n))
                n += 1
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w') as z:
            for i in range(4):
                z.writestr('WWF No Mercy#%08X#2#1#00000000_ciByRGBA.png' % (100 + i), png(i))
        self.write('in/TNA/Pack/Raven/Attire 3/T-Shirts/New look/New_Look.zip', inner.getvalue())
        self.write('in/TNA/Pack/Raven/Attire 3/Thumbs.db', b'x')
        items, _ = self.build([os.path.join(self.dir, 'in', 'TNA')])
        packs = [i for i in items if i.get('kind') == 'texture-pack']
        self.assertEqual(len(packs), 1, items)
        self.assertEqual((packs[0]['status'], packs[0]['images']), ('converted', n))
        with zipfile.ZipFile(packs[0]['package']) as z:
            self.assertEqual(sum(x.endswith('.png') for x in z.namelist()), n)   # every image, at every level
        rest = [i for i in items if i is not packs[0]]
        self.assertEqual([(os.path.basename(i['item']), i['status']) for i in rest], [('New_Look.zip', 'unsupported')])
        self.assertIn('inside the texture pack', rest[0]['why'])
        # A zip among the pack's images that is a pack itself becomes its own item.
        with zipfile.ZipFile(inner, 'a') as z:
            z.writestr('WWF No Mercy#%08X#2#1#00000000_ciByRGBA.png' % 200, png(9))
        self.write('in/TNA/Pack/Raven/Attire 3/T-Shirts/New look/New_Look.zip', inner.getvalue())
        shutil.rmtree(self.out)
        items, _ = self.build([os.path.join(self.dir, 'in', 'TNA')])
        self.assertEqual(sorted((i['kind'], i['status'], i.get('images')) for i in items),
                         [('texture-pack', 'converted', 5), ('texture-pack', 'converted', n)])
        # Zipped, the inner archive is named in a note; an archive outside the images still makes a folder of items.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for i in range(6):
                z.writestr('P/a/WWF No Mercy#%08X#2#1#00000000_ciByRGBA.png' % i, png(i))
            z.writestr('P/a/x.zip', inner.getvalue())
        items, _ = self.run_job([self.write('in/zp.zip', buf.getvalue())])
        self.assertEqual(sorted((i.get('kind'), i['status']) for i in items),
                         [('archive', 'unsupported'), ('texture-pack', 'ready')])
        self.assertFalse(ai.is_texture_folder(['hacks/h.zip'] + ['tex/WWF No Mercy#%08X#2#1_all.png' % i
                                                                  for i in range(6)]))

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

    def test_bundle_builds_one_conversion_with_its_textures_inside(self):
        r = self.build_one([self.bundle()])
        self.assertEqual((r['status'], r['converter'], r['game'], r['id']), ('converted', 'import-pj64', 'nomercy', 'my-mod'),
                         r)
        self.assertEqual(r['packages'], [r['package']])
        m, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertNotIn('requires', m)
        self.assertEqual(aki['components']['textures'], {'dir': 'textures', 'naming': 'rice', 'optional': False})
        self.assertEqual(aki['survey']['converter'], 'import-pj64')
        with zipfile.ZipFile(r['package']) as z:
            codes = z.read('conversion/codes.txt').decode()
            names = z.namelist()
            rt64 = json.loads(z.read('conversion/textures/rt64.json'))
        self.assertEqual(rt64['configuration']['autoPath'], 'rice')
        self.assertEqual(len([n for n in names if n.startswith('conversion/textures/') and n.endswith('.png')]), 6)
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

    def test_wrapped_zipped_bundle_with_one_cheat_folder_under_another_name(self):
        # WWF Legends: Challenge 64's shape (aki-roadmap#265): 'Ch64 - Public v2.zip' holds 'Ch64 - Public/' with the
        # Project64 folder beside a read-me tree and a launcher exe; the cheats sit in one folder named 'WWF Legends',
        # not like the mod. One conversion with those cheats; the read-me tree isn't unpacked (its paths can pass 260).
        key = '00000000-00000000-C:45'
        cht = '\n'.join(['[%s]' % key, 'Name=WWF No Mercy', 'Cheat0="WWF Legends' + BS + 'Arena",810500E8 0933', ''])
        deep = 'Ch64 - Public/Help Files & Setup/' + '/'.join(['Troubleshooting Folder Number %d' % i for i in range(12)]) + '/x.reg'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('Ch64 - Public/Project64/Config/Project64.cht', cht.encode('latin-1'))
            z.writestr('Ch64 - Public/Project64/Project64.exe', b'x')
            for i in range(6):
                z.writestr('Ch64 - Public/Project64/Plugin/hires_texture/WWF NO MERCY/WWF NO MERCY#%08X#0#2_all.png' % i, png(i))
            z.writestr('Ch64 - Public/Run The Mod.exe', b'x')
            z.writestr(deep, b'x')
        r = self.build_one([self.write('in/Ch64 - Public v2.zip', buf.getvalue())])
        self.assertEqual((r['status'], r['converter'], r['game']), ('converted', 'import-pj64', 'nomercy'), r)
        with zipfile.ZipFile(r['package']) as z:
            codes = z.read('conversion/codes.txt').decode()
            names = z.namelist()
        self.assertIn('810500E8 0933', codes)
        self.assertEqual(len([n for n in names if n.startswith('conversion/textures/') and n.endswith('.png')]), 6)
        self.assertFalse(any('Help Files' in n or n.endswith('.reg') for n in names))
        # The unpack itself leaves the read-me tree out (its paths can pass 260 characters): only Project64\ is written.
        src = t.ai.Source('in/Ch64 - Public v2.zip', 'folder', names=[], path=self.write('in/c2.zip', buf.getvalue()))
        src.zip_path = src.path
        b = t.ai.Builder({'out': os.path.join(self.dir, 'out2'), 'work': os.path.join(self.dir, 'work2')}, t.ai.Roms(), {})
        folder, unpacked = b.bundle_folder(src)
        self.assertTrue(folder.replace('\\', '/').endswith('/Project64'), folder)
        self.assertFalse(os.path.exists(os.path.join(unpacked, 'Help Files & Setup')))
        self.assertTrue(os.path.exists(os.path.join(folder, 'Project64.exe')))

    def test_bundle_for_no_known_game_needs_one(self):
        self.write('in/Mystery/Project64.cht', b'[12345678-9ABCDEF0-C:45]\nName=Something\n')
        for i in range(6):
            self.write('in/Mystery/Textures/X#%08X#0#2_all.png' % i, png(i))
        r = self.build_one([os.path.join(self.dir, 'in', 'Mystery')])
        self.assertEqual(r['status'], 'needs-game')


def hts_file(path, records):
    """A GLideN64 .hts (old layout) with RGBA8 records [(checksum, w, h, rgba)]: synthetic, as test_aki_conv's."""
    import struct
    body, index, pos = b'', [], 12
    for chk, w, h, rgba in records:
        rec = struct.pack('<IIIHHB', w, h, 0x8058, 0x1908, 0x1401, 1) + struct.pack('<I', len(rgba)) + rgba
        index.append(struct.pack('<Qq', chk, pos))
        body += rec
        pos += len(rec)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(struct.pack('<iq', 0x40220000, pos) + body + struct.pack('<i', len(index)) + b''.join(index))


class TexturesSave(BuildBase):
    catalog = HackRoutes.catalog
    """A textures + save mod (N64Modderz64's WWF No Mercy MOD, aki-roadmap#172): one conversion with the save as its
    seed save and the textures inside, matched to the catalog by its content hash."""

    def mod(self, root='in/WWF MOD', fla=None):
        px = bytes([255, 0, 0, 255, 0, 255, 0, 128])
        hts_file(os.path.join(self.dir, root, 'Cache', 'WWF No Mercy_HIRESTEXTURES.hts'),
                 [(0x0000000011111111, 2, 1, px), (0x2222222233333333, 2, 1, px)])
        import test_aki_save as ts   # its synthetic No Mercy flash, in Project64's .fla word order
        self.write(root + '/Save/WWF No Mercy-0123/WWF No Mercy.fla', ts.ws32(ts.nm_flash(fla or 1)))
        return os.path.join(self.dir, *root.split('/'))

    def test_builds_one_conversion_with_a_seed_save_and_textures(self):
        r = self.build_one([self.mod()])
        self.assertEqual((r['status'], r['converter'], r['game'], r['aki_tier']),
                         ('converted', 'import-pj64', 'nomercy', 'untested'), r)
        m, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual([(s['kind'], s['mode']) for s in aki['components']['saves']], [('flash', 'seed')])
        self.assertEqual(aki['components']['textures']['naming'], 'rice')
        self.assertEqual(aki['survey']['content_sha1'], r['content_sha1'])
        with zipfile.ZipFile(r['package']) as z:
            pngs = [n for n in z.namelist() if n.startswith('conversion/textures/') and n.endswith('.png')]
        self.assertEqual(len(pngs), 2)

    def test_catalog_matches_by_content(self):
        first = self.build_one([self.mod()])
        os.remove(first['package'])
        cat = self.catalog([{'id': 'n64m', 'name': 'Mod', 'game': 'nomercy', 'tier': 'verified', 'note': 'N',
                             'outputs': {'content_sha1': [first['content_sha1']]}}])
        r = self.build_one([self.mod()], catalog=cat)
        self.assertEqual((r['aki_tier'], r['note']), ('verified', 'N'))
        # The entry names it, not the archive's folder (aki-roadmap#172).
        self.assertEqual((r['id'], r['name']), ('n64m', 'Mod'))
        self.assertEqual(os.path.basename(r['package']), 'n64m-1.0.0.zip')
        m, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual((m['id'], m['display_name'], aki['id'], aki['name']), ('n64m', 'Mod', 'n64m', 'Mod'))
        self.assertNotIn('WWF MOD', aki['description'])
        with zipfile.ZipFile(r['package']) as z:
            readme = z.read('conversion/README.md').decode('utf-8')
        self.assertIn('Mod', readme)
        self.assertNotIn('WWF MOD', readme)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', 'build')))   # import-pj64's temp folder went

    def test_untested_build_keeps_the_folder_name(self):
        first = self.build_one([self.mod()])
        self.assertEqual((first['id'], first['name']), ('wwf-mod', 'WWF MOD'))
        cat = self.catalog([{'id': 'n64m', 'name': 'Mod', 'game': 'nomercy', 'tier': 'verified',
                             'outputs': {'content_sha1': [first['content_sha1']]}}])
        r = self.build_one([self.mod('in2/WWF MOD', fla=2)], catalog=cat)
        self.assertEqual((r['aki_tier'], r['id'], r['name']), ('untested', 'wwf-mod-2', 'WWF MOD'))

    def test_other_save_is_untested(self):
        first = self.build_one([self.mod()])
        cat = self.catalog([{'id': 'n64m', 'name': 'Mod', 'game': 'nomercy', 'tier': 'verified',
                             'outputs': {'content_sha1': [first['content_sha1']]}}])
        r = self.build_one([self.mod('in2/WWF MOD', fla=2)], catalog=cat)
        self.assertNotEqual(r['content_sha1'], first['content_sha1'])
        self.assertEqual(r['aki_tier'], 'untested')

    def test_content_hash_ignores_rt64_json_and_follows_every_image(self):
        a = {'conversion/saves/s.bin': b'1', 'conversion/textures/x.png': b'p', 'conversion/textures/rt64.json': b'{}'}
        b = dict(a, **{'conversion/textures/rt64.json': b'{"other": 1}'})
        c = dict(a, **{'conversion/textures/x.png': b'q'})
        self.assertEqual(ai.Builder.content_sha1(a), ai.Builder.content_sha1(b))
        self.assertNotEqual(ai.Builder.content_sha1(a), ai.Builder.content_sha1(c))
        self.assertIsNone(ai.Builder.content_sha1({'conversion/codes.txt': b'x'}))

    def test_content_hash_is_of_the_png_image_not_its_deflate_bytes(self):
        # aki-roadmap#273: the same image written by two zlib builds (here: two levels) has the same content hash, so the
        # tier doesn't follow the launcher's Python. Another image, palette or size does change it.
        import struct
        import zlib

        def png_file(raw, level, w=2, h=2, plte=b''):
            def chunk(k, b):
                return struct.pack('>I', len(b)) + k + b + struct.pack('>I', zlib.crc32(k + b) & 0xFFFFFFFF)
            ihdr = struct.pack('>IIBBBBB', w, h, 8, 3 if plte else 6, 0, 0, 0)
            return (ai.PNG_SIGNATURE + chunk(b'IHDR', ihdr) + (chunk(b'PLTE', plte) if plte else b'') +
                    chunk(b'tEXt', b'Software\0level %d' % level) + chunk(b'IDAT', zlib.compress(raw, level)) +
                    chunk(b'IEND', b''))

        raw = bytes(range(18)) * 2        # 2 rows: filter byte + 2 RGBA pixels, twice
        fast, best = png_file(raw, 1), png_file(raw, 9)
        self.assertNotEqual(fast, best)
        self.assertEqual(ai.png_content_sha1(fast), ai.png_content_sha1(best))
        self.assertNotEqual(ai.png_content_sha1(fast), ai.png_content_sha1(png_file(bytes(36), 9)))
        self.assertNotEqual(ai.png_content_sha1(fast), ai.png_content_sha1(png_file(raw, 9, w=1, h=4)))
        pal = bytes(range(6))
        self.assertNotEqual(ai.png_content_sha1(png_file(bytes(6), 9, plte=pal)),
                            ai.png_content_sha1(png_file(bytes(6), 9, plte=pal[::-1])))
        a = {'conversion/textures/x.png': fast, 'conversion/saves/s.bin': b'1'}
        self.assertEqual(ai.Builder.content_sha1(a), ai.Builder.content_sha1(dict(a, **{'conversion/textures/x.png': best})))
        # Not a PNG we can read (truncated, or no image data): its file bytes count, as before.
        self.assertIsNone(ai.png_content_sha1(fast[:40]))
        self.assertIsNone(ai.png_content_sha1(b'\x89PNG\r\n\x1a\n' + b'x' * 32))
        self.assertNotEqual(ai.Builder.content_sha1({'conversion/textures/x.png': fast[:40]}),
                            ai.Builder.content_sha1({'conversion/textures/x.png': fast[:41]}))

    def test_codes_entry_wanting_content_needs_it(self):
        bundle = Pj64Bundles.bundle(self)
        first = self.build_one([bundle])
        os.remove(first['package'])
        cat = self.catalog([{'id': 'mymod', 'name': 'My Mod', 'game': 'nomercy', 'tier': 'verified',
                             'outputs': {'codes_sha1': [first['codes_sha1']], 'content_sha1': ['0' * 40]}}])
        self.assertEqual(self.build_one([bundle], catalog=cat)['aki_tier'], 'untested')


class Rev0Codes(BuildBase):
    """aki-roadmap#228: a bundle's codes for No Mercy Rev 0 are translated to the port's Rev 1 through the derelink
    map (a shipped one, else made from a stock Rev 0 ROM), with the hand-made packs' --map-range."""

    def setUp(self):
        super().setUp()
        # The real maps/nomercy-rev0.json ships: these tests start without one, as a batch without it would.
        saved, ai.MAPS_DIR = ai.MAPS_DIR, os.path.join(self.dir, 'no-maps')
        self.addCleanup(setattr, ai, 'MAPS_DIR', saved)

    def bundle(self, root='in/My Mod', rom=None):
        mod = root.rsplit('/', 1)[-1].replace(' ', '')     # the cheats' folder is the mod's name
        self.write(root + '/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy',
            'Cheat0="' + mod + BS + 'Arena",810500E8 0933',     # main: -0x90
            'Cheat1="' + mod + BS + 'Cave",81300000 1234',      # high RAM: the -0x1000 override
            'Cheat2="' + mod + BS + 'Gone",81180000 0001', ''    # nothing maps it: dropped
        ]).encode('latin-1'))
        if rom:
            self.write(root + '/Roms/No Mercy (U) (V1.0) (Not Patched).z64', rom)
        return os.path.join(self.dir, *root.split('/'))

    def codes(self, r):
        with zipfile.ZipFile(r['package']) as z:
            return z.read('conversion/codes.txt').decode(), json.loads(z.read('conversion/aki.json'))

    def test_codes_are_translated_with_a_map_from_the_players_rev0_rom(self):
        self.write('more/nm10.z64', t.REV0)
        dl = fake_derelink()
        r = self.build_one([self.bundle()], fakes={'derelink': dl}, rom_dirs=[os.path.join(self.dir, 'more')])
        self.assertEqual(r['status'], 'converted', r)
        self.assertEqual(r['codes'], 'codes translated from Rev 0 (2 of 3 lines; 1 dropped); values inside the code they '
                                     'write not translated (no derelink layout matches the port ROM)')
        tr = r['codes_translation']
        self.assertEqual((tr['from'], tr['to'], tr['translated'], tr['lines'], tr['translated_lines'], tr['dropped_lines']),
                         ('nomercy-rev0', 'nomercy-rev1', True, 3, 2, 1))
        self.assertEqual(tr['map'], 'derelink map from your WWF No Mercy (USA) 1.0 ROM')
        self.assertEqual(tr['map_ranges'], ['200800,400000,-1000'])
        codes, aki = self.codes(r)
        self.assertIn('# rom: nomercy-rev1', codes)
        self.assertIn('81050058 0933', codes)
        self.assertIn('812FF000 1234', codes)
        self.assertIn('# dropped (no mapping): 81180000 0001', codes)
        self.assertEqual(aki['components']['codes']['rom'], 'nomercy-rev1')
        self.assertEqual([c[0] for c in dl.calls], ['map'])
        self.assertEqual(dl.calls[0][dl.calls[0].index('--base') + 1], self.stock_path)
        self.assertFalse(os.path.exists(dl.calls[0][dl.calls[0].index('--hack') + 1]))   # the Rev 0 ROM copy is gone
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', 'build')))

    def test_without_a_rev0_rom_the_codes_stay_off_and_say_why(self):
        dl = fake_derelink()
        r = self.build_one([self.bundle()], fakes={'derelink': dl})
        self.assertEqual(r['status'], 'converted', r)
        self.assertTrue(r['codes'].startswith('codes for Rev 0 not translated: needs a stock WWF No Mercy (USA) 1.0 ROM '
                                              'to make the Rev 0 -> Rev 1 map'), r['codes'])
        self.assertEqual(r['codes_translation']['translated'], False)
        codes, aki = self.codes(r)
        self.assertIn('# rom: nomercy-rev0', codes)
        self.assertIn('810500E8 0933', codes)
        self.assertEqual(aki['components']['codes']['rom'], 'nomercy-rev0')
        self.assertEqual(dl.calls, [])

    def test_the_bundles_own_stock_rev0_rom_makes_the_map(self):
        # Showdown 64 ships the unpatched cartridge beside its patch.
        dl = fake_derelink()
        r = self.build_one([self.bundle(rom=t.REV0)], fakes={'derelink': dl})
        self.assertEqual(r['codes_translation']['map'], "derelink map from the bundle's WWF No Mercy (USA) 1.0 ROM")
        self.assertIn('812FF000 1234', self.codes(r)[0])
        # A hacked ROM in the bundle is not that dump.
        dl = fake_derelink()
        r = self.build_one([self.bundle('in/Other Mod', rom=t.fake_rom(revision=0, salt=9))], fakes={'derelink': dl})
        self.assertFalse(r['codes_translation']['translated'])
        self.assertEqual(dl.calls, [])

    def test_cheats_for_several_games_take_the_game_of_the_bundles_rom(self):
        # Showdown 64 (aki-roadmap#267): its Project64.cht names No Mercy, VPW64 and WM2000; its Roms\ folder holds the
        # stock No Mercy 1.0 cartridge its .rup patches. That ROM decides; without it the bundle needs a game.
        conv = ai.load_module('aki_conv')
        wm = bytearray(t.fake_rom(code='NWXE', title='WRESTLEMANIA 2000', salt=3))
        wm[0x10:0x18] = bytes.fromhex('1122334455667788')
        b = ai.Builder({'out': self.out, 'work': os.path.join(self.dir, 'work')}, ai.Roms(), {})
        b.roms.by_game = {'nomercy': (self.stock_path, t.STOCK), 'wm2k': ('wm2k.z64', bytes(wm))}
        root = 'in/Show Mod'
        self.write(root + '/Config/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy', 'Cheat0="ShowMod' + BS + 'Arena",810500E8 0933',
            '[%s]' % conv.cht_key(bytes(wm)), 'Name=WWF WrestleMania 2000', 'Cheat0="Other' + BS + 'Thing",81000000 0001',
            '']).encode('latin-1'))
        folder = os.path.join(self.dir, *root.split('/'))
        with self.assertRaises(ai.NeedsGame):
            b.bundle_game(folder, conv)
        self.write(root + '/Roms/No Mercy (U) (V1.0) (Not Patched).z64', t.REV0)
        self.assertEqual(b.bundle_game(folder, conv), 'nomercy')
        # A hint still decides when the bundle's ROMs don't (two games' ROMs in it).
        self.write(root + '/Roms/WM2000.z64', bytes(wm))
        b.game_hint = 'wm2k'
        self.assertEqual(b.bundle_game(folder, conv), 'wm2k')

    def test_a_shipped_map_needs_no_rom(self):
        maps = os.path.join(self.dir, 'maps')
        os.makedirs(maps)
        with open(os.path.join(maps, 'nomercy-rev0.json'), 'w') as f:
            json.dump(REV0_MAP, f)
        saved, ai.MAPS_DIR = ai.MAPS_DIR, maps
        try:
            dl = fake_derelink()
            r = self.build_one([self.bundle()], fakes={'derelink': dl})
        finally:
            ai.MAPS_DIR = saved
        self.assertEqual(r['codes_translation']['map'], 'maps/nomercy-rev0.json')
        self.assertIn('81050058 0933', self.codes(r)[0])
        self.assertEqual(dl.calls, [])

    def test_a_versioned_bundle_folder_finds_the_cheat_folder(self):
        # Showdown 64 V6.0.4\Config\Project64.cht keeps its cheats under "Showdown 64\..." (real bundle, 2026-11-08).
        root = 'in/Show Mod V6.0.4'
        self.write(root + '/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy',
            'Cheat0="Show Mod' + BS + 'Arena",810500E8 0933', 'Cheat1="Optional' + BS + 'Other",810500E8 0001', ''
        ]).encode('latin-1'))
        self.write('more/nm10.z64', t.REV0)
        r = self.build_one([os.path.join(self.dir, *root.split('/'))], fakes={'derelink': fake_derelink()},
                           rom_dirs=[os.path.join(self.dir, 'more')])
        self.assertEqual(r['status'], 'converted', r)
        self.assertTrue(r['codes'].startswith('codes translated from Rev 0 (1 of 1 lines'), r['codes'])
        codes = self.codes(r)[0]
        self.assertIn('[Show Mod' + BS + 'Arena]', codes)
        self.assertNotIn('Optional', codes)

    def test_no_matching_cheat_folder_says_so(self):
        root = 'in/Quiet Mod'
        self.write(root + '/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy', 'Cheat0="Elsewhere' + BS + 'Arena",810500E8 0933',
            'Cheat1="Another' + BS + 'Thing",8109940A FFFF', ''      # two folders, neither the mod's (aki-roadmap#265:
        ]).encode('latin-1'))                                          # a single folder is taken as the mod's)
        for i in range(6):                                                  # something else to import
            self.write(root + '/Textures/WWF NO MERCY#%08X#0#2_all.png' % i, png(i))
        r = self.build_one([os.path.join(self.dir, *root.split('/'))], fakes={'derelink': fake_derelink()})
        self.assertEqual(r['status'], 'converted', r)
        self.assertEqual(r['codes'], "no codes taken: no cheat folder is named like the mod ('Quiet Mod'); "
                                     "its folders: Another, Elsewhere")
        # Cheats alone, none taken: the failure says why.
        shutil.rmtree(os.path.join(self.dir, 'in', 'Quiet Mod', 'Textures'))
        r = self.build_one([os.path.join(self.dir, *root.split('/'))], fakes={'derelink': fake_derelink()})
        self.assertEqual(r['status'], 'failed', r)
        self.assertIn("(no cheat folder is named like the mod ('Quiet Mod'); its folders: Another, Elsewhere)", r['why'])

    def test_without_version(self):
        for name, bare in (('Showdown 64 V6.0.4', 'Showdown 64'), ('AEW 64 (v1.2)', 'AEW 64'), ('Mod 1.0.5', 'Mod'),
                           ('TNA No Mercy 2015', 'TNA No Mercy 2015'), ('Showdown 64', 'Showdown 64'),
                           ('Revolution', 'Revolution'), ('ABCv3', 'ABCv3')):
            self.assertEqual(ai.without_version(name), bare)

    def test_the_shipped_rev0_map_holds_address_ranges_only(self):
        # maps/nomercy-rev0.json ships with the launcher: [lo, hi, shift, kind] ranges, nothing from the ROM itself.
        path = os.path.join(os.path.dirname(ai.__file__), 'maps', 'nomercy-rev0.json')
        raw = json.load(open(path))
        self.assertEqual(set(raw), {'main', 'overlays', 'identity'})
        ranges = raw['main'] + [r for v in raw['overlays'].values() for r in v]
        self.assertGreater(len(ranges), 1000)
        for lo, hi, shift, kind in ranges:
            self.assertTrue(0x80000000 <= lo < hi <= 0x80800000 and -0x400000 < shift < 0x400000, (lo, hi, shift))
            self.assertIn(kind, ('text', 'entry', 'data', 'heap', 'save'))
        for lo, hi in raw['identity']:
            self.assertTrue(0x80000000 <= lo < hi <= 0x80800000)
        self.assertLess(os.path.getsize(path), 512 << 10)

    def test_the_map_is_made_once_per_batch(self):
        self.write('more/nm10.z64', t.REV0)
        dl = fake_derelink()
        real = ai.Builder.mod
        ai.Builder.mod = lambda builder, name: dl if name == 'derelink' else real(builder, name)
        try:
            items, _ = self.build([self.bundle('in/A Mod'), self.bundle('in/B Mod')], rom_dirs=[os.path.join(self.dir, 'more')])
        finally:
            ai.Builder.mod = real
        self.assertEqual([i['codes_translation']['translated'] for i in items], [True, True])
        self.assertEqual(len(dl.calls), 1)

    def test_a_failed_map_leaves_the_codes_off(self):
        self.write('more/nm10.z64', t.REV0)
        r = self.build_one([self.bundle()], fakes={'derelink': fake_derelink(fail='base ROM matches no layout')},
                           rom_dirs=[os.path.join(self.dir, 'more')])
        self.assertEqual(r['status'], 'converted', r)
        self.assertIn('not translated: derelink map: base ROM matches no layout', r['codes'])

    def test_a_map_that_raises_leaves_the_codes_off_once(self):
        # Not only sys.exit: any exception in derelink's analysis leaves the codes off (cached), never fails the bundle.
        self.write('more/nm10.z64', t.REV0)
        calls = []

        def boom(argv):
            calls.append(argv)
            raise IndexError('odd ROM')
        dl = types.SimpleNamespace(main=boom)
        real = ai.Builder.mod
        ai.Builder.mod = lambda builder, name: dl if name == 'derelink' else real(builder, name)
        try:
            items, _ = self.build([self.bundle('in/A Mod'), self.bundle('in/B Mod')], rom_dirs=[os.path.join(self.dir, 'more')])
        finally:
            ai.Builder.mod = real
        self.assertEqual([i['status'] for i in items], ['converted', 'converted'])
        self.assertIn('not translated: derelink map: IndexError: odd ROM', items[0]['codes'])
        self.assertEqual(len(calls), 1)
        self.assertFalse(os.path.exists(calls[0][calls[0].index('--hack') + 1]))

    def test_only_the_mods_own_section_decides(self):
        # A full Project64.cht: stock Rev 0 cheats, the mod's own in the port's (Rev 1) section. No map is made.
        self.write('more/nm10.z64', t.REV0)
        self.write('in/My Mod/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy', 'Cheat0="Stock' + BS + 'Money",8109940A FFFF',
            '[00000000-00000000-C:45]', 'Name=WWF No Mercy', 'Cheat0="MyMod' + BS + 'Arena",810500E8 0933', '']).encode())
        dl = fake_derelink()
        r = self.build_one([os.path.join(self.dir, 'in', 'My Mod')], fakes={'derelink': dl},
                           rom_dirs=[os.path.join(self.dir, 'more')])
        self.assertEqual(r['codes'], "codes for the port's revision (1 lines)")
        self.assertEqual(dl.calls, [])

    def test_a_bad_catalog_map_range_is_refused(self):
        self.write('more/nm10.z64', t.REV0)
        conv = ai.load_module('aki_conv')
        real = ai.Builder.catalog_entry
        dl = fake_derelink()
        for bad in ('200800,400000,-1000', ['200800,400000'], None):
            ai.Builder.catalog_entry = lambda builder, res: {'map_range': bad}
            try:
                r = self.build_one([self.bundle('in/Mod %d' % len(str(bad)))], fakes={'derelink': dl, 'aki_conv': conv},
                                   rom_dirs=[os.path.join(self.dir, 'more')])
            finally:
                ai.Builder.catalog_entry = real
            self.assertEqual(r['status'], 'converted', r)
            self.assertIn("not translated: the catalog's map_range", r['codes'])
        self.assertEqual(dl.calls, [])

    def test_values_are_translated_when_a_layout_matches(self):
        # The port ROM here is synthetic, so no shipped layout matches it: stand one in, as the real port ROM has.
        self.write('more/nm10.z64', t.REV0)
        conv = ai.load_module('aki_conv')
        conv.find_layout = lambda derelink, rom: {'sections': [
            {'name': 'main', 'rom': 0x1000, 'text': 0x80000400, 'data': 0x80030000}]}
        conv.bake_codes = lambda *a, **k: (None, {})        # nothing to bake into a patch here
        j = lambda tgt: 0x08000000 | ((tgt & 0x0FFFFFFF) >> 2)
        hook, back = j(0x80300074), j(0x80021C88)   # into a cave in high RAM, and the way back
        self.write('in/Hook Mod/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy',
            'Cheat0="HookMod' + BS + 'Hook",81021C80 %04X,81021C82 %04X,81300074 %04X,81300076 %04X' % (
                hook >> 16, hook & 0xFFFF, back >> 16, back & 0xFFFF), '']).encode())
        r = self.build_one([os.path.join(self.dir, 'in', 'Hook Mod')], fakes={'derelink': fake_derelink(), 'aki_conv': conv},
                           rom_dirs=[os.path.join(self.dir, 'more')])
        self.assertEqual(r['codes'], 'codes translated from Rev 0 (4 of 4 lines; 0 dropped); 3 values inside the code '
                                     'they write translated', r)
        self.assertEqual(r['codes_translation']['values'],
                         {'values_changed': 3, 'unmapped': 0, 'lui_conflicts': 0, 'stepped_bases': 0})
        codes = self.codes(r)[0]
        moved = j(0x802FF074)       # the hook follows the cave the -0x1000 override moved
        self.assertIn('81021BF0 %04X' % (moved >> 16), codes)
        self.assertIn('81021BF2 %04X' % (moved & 0xFFFF), codes)
        self.assertIn('812FF076 %04X' % (j(0x80021BF8) & 0xFFFF), codes)   # the way back follows main text

    def test_port_revision_codes_say_so(self):
        r = self.build_one([Pj64Bundles.bundle(self)])
        self.assertEqual(r['codes'], "codes for the port's revision (1 lines)")
        self.assertNotIn('codes_translation', r)


class ImportPolish(BuildBase):
    """aki-roadmap#268: one conversion per hack in a batch, plan agreeing with build."""

    def hack(self, offset, val=7):
        rom = bytearray(STOCK)
        rom[offset] ^= val
        return bytes(rom)

    def zip_of(self, rel, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)
        return self.write(rel, buf.getvalue())

    def test_same_output_twice_builds_one_conversion(self):
        # Badd Blood: the hack ROM and its patch (applied to the stock ROM) build the same ROM.
        rom = self.write('in/Hack.z64', self.hack(0x2000))
        patch = self.write('in/hack.xdelta', t.make_patch(STOCK, [(0x2000, self.hack(0x2000)[0x2000])]))
        ledger = os.path.join(self.dir, 'ledger.json')
        items, _ = self.build([rom, patch], ledger=ledger)
        by = {os.path.basename(i['item']): i for i in items}
        self.assertEqual(by['Hack.z64']['status'], 'converted')
        self.assertEqual((by['hack.xdelta']['status'], by['hack.xdelta']['duplicate_of'], by['hack.xdelta']['id']),
                         ('have-it', rom, 'hack'))
        self.assertEqual(os.listdir(os.path.join(self.out, 'nomercy')), ['hack-1.0.0.zip'])
        items, _ = self.build([patch], ledger=ledger)        # the ledger knows the second input too
        self.assertEqual(items[0]['status'], 'have-it')

    def catalog_for(self, rom, **entry):
        sha1 = hashlib.sha1(rom).hexdigest().upper()
        e = dict(id='deluxe', name='Deluxe v1.1', game='nomercy', tier='experimental',
                 inputs={'sha1': [sha1]}, outputs={'sha1': [sha1]}, **entry)
        return self.write('catalog.json', json.dumps({'entries': [e]}).encode())

    def test_sibling_patch_of_a_catalog_hack_is_an_alternative(self):
        # VPW64 Deluxe v1.1: two xdeltas of one hack in one zip; the catalog knows one build.
        known, other = self.hack(0x2000), self.hack(0x2004)
        z = self.zip_of('in/deluxe.zip', {
            'Deluxe_Emulator.xdelta': t.make_patch(STOCK, [(0x2004, other[0x2004])]),   # first in the zip
            'Deluxe_Summercart.xdelta': t.make_patch(STOCK, [(0x2000, known[0x2000])])})
        items, _ = self.build([z], catalog=self.catalog_for(known))
        by = {i['item'].split('!')[-1]: i for i in items}
        self.assertEqual((by['Deluxe_Summercart.xdelta']['status'], by['Deluxe_Summercart.xdelta']['id'],
                          by['Deluxe_Summercart.xdelta']['aki_tier']), ('converted', 'deluxe', 'experimental'))
        alt = by['Deluxe_Emulator.xdelta']
        self.assertEqual((alt['status'], alt['alternative_of']), ('have-it', z + '!Deluxe_Summercart.xdelta'))
        self.assertIn('another build of Deluxe v1.1', alt['why'])
        self.assertEqual(os.listdir(os.path.join(self.out, 'nomercy')), ['deluxe-1.0.0.zip'])
        # Alone (not in that zip) it builds, Untested.
        r = self.build_one([self.write('in/Deluxe_Emulator.xdelta', t.make_patch(STOCK, [(0x2004, other[0x2004])]))],
                           catalog=self.catalog_for(known))
        self.assertEqual((r['status'], r['aki_tier']), ('converted', 'untested'))
        # Without a catalog match in the zip, both build.
        shutil.rmtree(self.out)
        items, _ = self.build([z], catalog='')
        self.assertEqual(sorted(i['status'] for i in items), ['converted', 'converted'])

    def test_plan_says_needs_rom_as_build_does(self):
        rom = self.write('in/Hack.z64', self.hack(0x2000))
        plan = self.one([rom], roms={})
        build = self.build_one([rom], roms={})
        self.assertEqual((plan['status'], plan['needs']), (build['status'], build['needs']))
        self.assertEqual(plan['needs'], 'the WWF No Mercy ROM the port uses (the conversion is built against it)')


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


class CatalogBundles(BuildBase):
    """aki-roadmap#265: the catalog knows a Project64 bundle by the hack ROM inside it (inputs.sha1). The build then
    takes the entry's id, name and settings, and runs the entry's codes_fixups on codes.txt (WWF Legends: the menu and
    block heap guards, the cave tables out of the framebuffers, the hooks with the Rev 0 low half: without them that
    build crashed at boot)."""

    def setUp(self):
        super().setUp()
        saved, ai.MAPS_DIR = ai.MAPS_DIR, os.path.join(self.dir, 'no-maps')
        self.addCleanup(setattr, ai, 'MAPS_DIR', saved)
        # A stand-in fix next to the tests' own temp folder, registered for these tests only.
        fixups = os.path.join(self.dir, 'fixups')
        os.makedirs(fixups)
        with open(os.path.join(fixups, 'tfix.py'), 'w') as f:
            f.write('def tag(text):\n'
                    '    if "0BAD" in text:\n'
                    '        raise ValueError("not the list this fix knows")\n'
                    '    return text + "# tagged by tfix\\n", "1 line"\n'
                    'def again(text):\n'
                    '    return text + "# and again\\n", ""\n')
        saved_dir, ai.FIXUPS_DIR = ai.FIXUPS_DIR, fixups
        self.addCleanup(setattr, ai, 'FIXUPS_DIR', saved_dir)
        ai.CODES_FIXUPS['tfix'] = ('tfix', ('tag', 'again'))
        self.addCleanup(ai.CODES_FIXUPS.pop, 'tfix', None)
        self.rom = t.fake_rom(revision=0, salt=9)
        self.rom_sha1 = hashlib.sha1(self.rom).hexdigest().upper()

    def bundle(self, root='in/Ch64 - Public', cheat='810500E8 0933', more=()):
        # The distribution's shape: read-me pictures and a launcher exe beside its Project64 folder.
        cheats = ['Cheat0="WWF Legends' + BS + 'Arena",' + cheat] + \
            ['Cheat%d="WWF Legends%s%s",%s' % (n + 1, BS, name, codes) for n, (name, codes) in enumerate(more)]
        self.write(root + '/Project64/Config/Project64.cht', '\n'.join([
            '[%s]' % REV0_KEY, 'Name=WWF No Mercy'] + cheats + ['']).encode('latin-1'))
        self.write(root + '/Project64/Project64.exe', b'x')
        self.write(root + '/Project64/Roms/WWF Legends - Challenge 64.z64', self.rom)
        self.write(root + '/Cheat Code Settings.jpg', b'x')
        self.write(root + '/Help Files & Setup/Settings/Check Your Paths.jpg', b'x')
        self.write(root + '/Help Files & Setup/Read Me.txt', b'x')
        self.write(root + '/Run WWF Legends - Challenge 64.exe', b'x')
        return os.path.join(self.dir, *root.split('/'))

    def entry(self, **kw):
        e = {'id': 'legends_c64', 'name': 'WWF Legends: Challenge 64', 'game': 'nomercy', 'tier': 'experimental',
             'converter': 'import-pj64', 'inputs': {'sha1': [self.rom_sha1]}, 'codes_fixups': ['tfix']}
        e.update(kw)
        return HackRoutes.catalog(self, [e])

    def codes(self, r):
        with zipfile.ZipFile(r['package']) as z:
            return z.read('conversion/codes.txt').decode(), json.loads(z.read('conversion/aki.json'))

    def test_the_bundle_is_known_by_its_rom_and_gets_the_entrys_fixes(self):
        dl = fake_derelink()
        r = self.build_one([self.bundle()], fakes={'derelink': dl}, catalog=self.entry())
        self.assertEqual((r['status'], r['id'], r['name'], r['game']),
                         ('converted', 'legends_c64', 'WWF Legends: Challenge 64', 'nomercy'), r)
        self.assertEqual(r['codes_fixups'], ['tfix'])
        codes, aki = self.codes(r)
        self.assertTrue(codes.endswith('# tagged by tfix\n# and again\n'), codes[-80:])   # both steps, in order
        self.assertIn('810500E8 0933', codes)
        self.assertEqual(aki['survey']['codes_fixups'], ['tfix'])
        self.assertEqual(aki['survey']['catalog'], 'legends_c64')
        self.assertEqual(os.path.basename(r['package']), 'legends_c64-1.0.0.zip')
        # plan says the same id and name before anything is built
        items, _ = self.run_job([self.bundle()], catalog=self.entry())
        self.assertEqual([(i['kind'], i['id'], i['catalog']) for i in items],
                         [('pj64-bundle', 'legends_c64', 'legends_c64')])

    def test_zipped_bundle_is_known_by_its_rom_too(self):
        folder = self.bundle('src/Ch64 - Public')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for d, _, fs in os.walk(folder):
                for f in fs:
                    p = os.path.join(d, f)
                    z.write(p, 'Ch64 - Public/' + os.path.relpath(p, folder).replace(os.sep, '/'))
        r = self.build_one([self.write('in/Ch64 - Public v2.zip', buf.getvalue())], fakes={'derelink': fake_derelink()},
                           catalog=self.entry())
        self.assertEqual((r['status'], r['id'], r['codes_fixups']), ('converted', 'legends_c64', ['tfix']), r)

    def test_without_the_catalog_the_read_me_pictures_still_keep_it_one_bundle(self):
        r = self.build_one([self.bundle()], fakes={'derelink': fake_derelink()}, catalog='')
        self.assertEqual((r['status'], r['kind'], r['id']), ('converted', 'pj64-bundle', 'ch64-public'), r)
        self.assertNotIn('codes_fixups', r)

    def test_a_fix_that_refuses_the_list_fails_the_build(self):
        r = self.build_one([self.bundle(cheat='810500E8 0BAD')], fakes={'derelink': fake_derelink()},
                           catalog=self.entry())
        self.assertEqual(r['status'], 'failed', r)
        self.assertIn('codes fix tfix: not the list this fix knows', r['why'])
        shutil.rmtree(os.path.join(self.dir, 'in'))
        r = self.build_one([self.bundle()], fakes={'derelink': fake_derelink()},
                           catalog=self.entry(codes_fixups=['tfix', 'no-such-fix']))
        self.assertEqual(r['status'], 'failed', r)
        self.assertIn("unknown codes fix 'no-such-fix'", r['why'])
        self.assertFalse(os.listdir(os.path.join(self.out, 'nomercy')) if os.path.isdir(os.path.join(self.out, 'nomercy'))
                         else [])

    def test_the_entrys_codes_to_leave_out_are_taken_out_after_its_fixes(self):
        """aki-roadmap#300: the tested Legends pack leaves out its main-menu Selection Pointers (Rev 0 pointer values the
        translation can't place); kept on, the main menu stops. The build takes it out after the fixes, notes why."""
        sel = 'Main Menu' + BS + 'Selection Pointers'
        leave = [{'section': 'WWF Legends' + BS + sel, 'why': 'writes Rev 0 pointer values the translation cannot place'}]
        r = self.build_one([self.bundle(more=[(sel, '812B75FE D3E0'), ('Ring', '810500EC 0934')])],
                           fakes={'derelink': fake_derelink()}, catalog=self.entry(codes_leave_out=leave))
        self.assertEqual((r['status'], r['codes_fixups']), ('converted', ['tfix']), r)
        self.assertEqual(r['codes_left_out'], ['WWF Legends' + BS + sel])
        codes, aki = self.codes(r)
        self.assertNotIn('812B75FE', codes)
        self.assertNotIn('\n[WWF Legends' + BS + sel + ']', codes)      # only the note names it
        self.assertIn('# left out (writes Rev 0 pointer values the translation cannot place):\n#   [WWF Legends' +
                      BS + sel + ']\n', codes)
        self.assertIn('810500E8 0933', codes)       # the other cheats stay
        self.assertIn('810500EC 0934', codes)
        self.assertIn('# tagged by tfix', codes)    # the fixes still ran
        self.assertEqual(aki['survey']['codes_left_out'], ['WWF Legends' + BS + sel])
        self.assertEqual(r['codes_sha1'], hashlib.sha1(codes.encode()).hexdigest().upper())

    def test_a_section_to_leave_out_that_the_list_lacks_fails_the_build(self):
        leave = [{'section': 'WWF Legends' + BS + 'No Such Cheat', 'why': 'x'}]
        r = self.build_one([self.bundle()], fakes={'derelink': fake_derelink()},
                           catalog=self.entry(codes_leave_out=leave))
        self.assertEqual(r['status'], 'failed', r)
        self.assertIn('codes to leave out not in the list: WWF Legends' + BS + 'No Such Cheat', r['why'])

    def test_leave_out_sections_notes_them_above_the_hack_code_writes_block(self):
        text = '\n'.join(['# head', '', '[A]', '# default: on', '81000000 0001', '', '[B]', '# default: on',
                          'D0000001 0002', '81000002 0003', '', '[C]', '81000004 0005', '', '',
                          '# Watches code the hack rewrites itself (aki-roadmap#191).', '# rom: nomercy-rev1', '',
                          ai.HACK_WRITES_SECTION, '# default: on', 'D1000400 3C09', '810F295C 0000', ''])
        out, missing = ai.leave_out_sections(text, [{'section': 'B', 'why': 'w'}, {'section': 'Z', 'why': 'w'}])
        self.assertEqual(missing, ['Z'])
        self.assertNotIn('D0000001 0002', out)
        self.assertIn('[A]\n# default: on\n81000000 0001\n\n[C]\n81000004 0005\n\n\n'
                      '# left out (w):\n#   [B]\n\n\n# Watches code', out)
        self.assertTrue(out.endswith('D1000400 3C09\n810F295C 0000\n'))
        # no watch block: the note goes at the end; CRLF lists keep CRLF; nothing found leaves the text alone
        out, _ = ai.leave_out_sections('[A]\r\n81000000 0001\r\n\r\n[B]\r\n81000002 0003\r\n',
                                       [{'section': 'B', 'why': 'w'}])
        self.assertEqual(out, '[A]\r\n81000000 0001\r\n\r\n# left out (w):\r\n#   [B]\r\n\r\n')
        self.assertEqual(ai.leave_out_sections(text, [{'section': 'Z'}]), (text, ['Z']))

    def test_the_shipped_legends_entry_names_registered_fixes(self):
        cat = json.load(open(ai.DEFAULT_CATALOG, encoding='utf-8'))
        legends = [e for e in cat['entries'] if e['id'] == 'legends_c64'][0]
        self.assertEqual(legends['codes_fixups'], ['legends_portrait_guards', 'legends_block_guards',
                                                   'legends_fb_relocate', 'legends_unpark'])
        for n in legends['codes_fixups']:
            module, steps = ai.CODES_FIXUPS[n]
            mod = ai.load_module(module, os.path.join(os.path.dirname(ai.DEFAULT_CATALOG), 'fixups', module + '.py'))
            for s in steps:
                self.assertTrue(callable(getattr(mod, s)), (n, s))
        self.assertEqual(legends['bake_exclude'], ['Wrestler Info Icon\\Mapping'])
        # aki-roadmap#300: the main-menu Selection Pointers stay out, as in the tested pack (kept, the menu stops)
        self.assertEqual([i['section'] for i in legends['codes_leave_out']],
                         ['WWF Legends\\Interface\\Main Menu\\Main Screen\\Navigation\\Selection Pointers'])
        self.assertNotIn('A2E83248CC1DC5A07DABBB68BA481C61C8003D7E', legends['outputs']['codes_sha1'])
        # The fixes refuse a list that isn't Legends': a synthetic list fails, it isn't silently changed.
        with self.assertRaises(ValueError):
            ai.load_module('legends_park_hooks', os.path.join(os.path.dirname(ai.DEFAULT_CATALOG), 'fixups',
                                                              'legends_park_hooks.py')).park('# rom: nomercy-rev1\n')


if __name__ == '__main__':
    unittest.main()
