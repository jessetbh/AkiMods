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

    def test_catalog_tier_and_converter_override(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 3
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        cat = self.write('catalog.json', json.dumps({'entries': [{'id': 'known-hack', 'name': 'Known Hack', 'game': 'nomercy',
            'tier': 'verified', 'converter': 'derelink', 'inputs': {'sha1': [sha1]}}]}).encode())
        r = self.build_one([self.write('in/k.z64', bytes(hack))], fakes={'derelink': fake_derelink()}, catalog=cat)
        self.assertEqual((r['status'], r['id'], r['converter'], r['aki_tier']),
                         ('converted', 'known-hack', 'derelink', 'verified'))
        m, aki, _ = check_package(self, r['package'], 'conversion')
        self.assertEqual(aki['survey']['catalog'], 'known-hack')
        self.assertNotIn('short_description', m)

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


class Standin(unittest.TestCase):
    def setUp(self):
        self.pf = ai.load_module('patchfmt_standin')

    def test_bps_round_trip_and_wrong_source(self):
        import make_patch_mod as mpm
        src = t.fake_rom()
        dst = bytearray(src)
        dst[0x2000:0x2004] = b'HACK'
        patch = mpm.bps_encode(src, bytes(dst))
        self.assertEqual(self.pf.detect(patch), 'bps')
        self.assertEqual(self.pf.apply(patch, src)[0], bytes(dst))
        with self.assertRaises(self.pf.PatchError) as e:
            self.pf.apply(patch, t.REV0)
        self.assertEqual(e.exception.source_crc32, ai.zipcrc(src))

    def test_ips_records_rle_and_truncate(self):
        src = bytes(64)
        patch = b'PATCH' + (4).to_bytes(3, 'big') + (2).to_bytes(2, 'big') + b'AB' + \
            (10).to_bytes(3, 'big') + (0).to_bytes(2, 'big') + (3).to_bytes(2, 'big') + b'Z' + b'EOF' + (32).to_bytes(3, 'big')
        out, _ = self.pf.apply(patch, src)
        self.assertEqual(len(out), 32)
        self.assertEqual(out[4:6] + out[10:13], b'ABZZZ')
        with self.assertRaises(self.pf.PatchError):
            self.pf.apply(b'PATCH' + b'\x00\x00', src)

    def test_ups(self):
        import zlib
        src = bytes(range(16))
        dst = bytearray(src)
        dst[3] ^= 0x40
        body = b'UPS1' + bytes([0x80 | 16]) + bytes([0x80 | 16]) + bytes([0x80 | 3]) + bytes([0x40, 0])
        body += (zlib.crc32(src) & 0xFFFFFFFF).to_bytes(4, 'little') + (zlib.crc32(bytes(dst)) & 0xFFFFFFFF).to_bytes(4, 'little')
        body += (zlib.crc32(body) & 0xFFFFFFFF).to_bytes(4, 'little')
        self.assertEqual(self.pf.apply(body, src)[0], bytes(dst))

    def test_other_formats_are_refused_by_name(self):
        with self.assertRaises(self.pf.PatchError) as e:
            self.pf.apply(b'\xd6\xc3\xc4\x00rest', bytes(16))
        self.assertIn('xdelta', str(e.exception))

    def test_hostile_bps(self):
        import make_patch_mod as mpm
        src = t.fake_rom()
        dst = bytearray(src)
        dst[0x2000:0x2010] = b'ABCD' * 4
        patch = bytearray(mpm.bps_encode(src, bytes(dst)))
        for bad in (bytes(patch[:10]), bytes(patch[:-1]) + bytes([patch[-1] ^ 1]), b'BPS1' + b'\x00' * 3):
            with self.assertRaises(self.pf.PatchError):
                self.pf.apply(bad, src)

    def bps(self, src, actions, tgt_size):
        """A hand-made BPS: actions are (kind, length, extra bytes)."""
        num = lambda n: t.ai.load_module('make_patch_mod').bps_number(n)
        body = bytearray(b'BPS1' + num(len(src)) + num(tgt_size) + num(0))
        for kind, length, extra in actions:
            body += num(((length - 1) << 2) | kind) + extra
        return body

    def finish(self, body, src, target):
        import zlib
        body += (zlib.crc32(src) & 0xFFFFFFFF).to_bytes(4, 'little') + (zlib.crc32(target) & 0xFFFFFFFF).to_bytes(4, 'little')
        body += (zlib.crc32(bytes(body)) & 0xFFFFFFFF).to_bytes(4, 'little')
        return bytes(body)

    def test_bps_overlapping_target_copy_repeats(self):
        num = lambda n: t.ai.load_module('make_patch_mod').bps_number(n)
        src = bytes(4)
        target = b'AB' + b'AB' * 50             # TargetRead "AB", then TargetCopy from 0 for 100 bytes
        body = self.bps(src, [(1, 2, b'AB'), (3, 100, num(0))], len(target))
        self.assertEqual(self.pf.apply(self.finish(body, src, target), src)[0], target)

    def test_bps_copies_outside_are_refused(self):
        num = lambda n: t.ai.load_module('make_patch_mod').bps_number(n)
        src = bytes(8)
        body = self.bps(src, [(2, 4, num(100 << 1))], 4)      # SourceCopy from offset 100 of an 8-byte ROM
        with self.assertRaises(self.pf.PatchError):
            self.pf.apply(self.finish(body, src, bytes(4)), src)
        body = self.bps(src, [(3, 4, num(0))], 4)             # TargetCopy before any output
        with self.assertRaises(self.pf.PatchError):
            self.pf.apply(self.finish(body, src, bytes(4)), src)

    def test_importer_uses_it_without_patchfmt(self):
        self.assertIn(ai.load_patchfmt().__name__, ('patchfmt', 'patchfmt_standin'))


if __name__ == '__main__':
    unittest.main()
