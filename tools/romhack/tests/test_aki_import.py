"""Tests for aki_import.py (aki-roadmap#228) on synthetic inputs: fake ROM images, a fake patch format standing in
for patchfmt.py, zips built in memory, temp folders. No ROM data, no game:

    python -m unittest discover -s tools/romhack/tests
"""
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tools/romhack
spec = importlib.util.spec_from_file_location('aki_import', os.path.join(ROOT, 'aki_import.py'))
ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)


def fake_rom(code='NW4E', revision=1, title='WWF No Mercy', size=0x4000, salt=0):
    rom = bytearray(size)
    rom[0:4] = b'\x80\x37\x12\x40'
    rom[0x20:0x34] = title.encode('latin-1').ljust(20, b' ')
    rom[0x3B:0x3F] = code.encode('latin-1')
    rom[0x3F] = revision
    for i in range(0x1000, size):
        rom[i] = (i * 7 + 3 + salt) & 0xFF
    return bytes(rom)


def v64(z64):
    b = bytearray(z64)
    b[0::2], b[1::2] = z64[1::2], z64[0::2]
    return bytes(b)


def n64(z64):
    b = bytearray(len(z64))
    for k in range(4):
        b[3 - k::4] = z64[k::4]
    return bytes(b)


# A stand-in for romhack/patchfmt.py with the agreed API: FAKE + source CRC32 + (offset, byte) pairs.
class PatchError(Exception):
    def __init__(self, msg, source_crc32=None):
        super().__init__(msg)
        self.source_crc32 = source_crc32


def fake_detect(data):
    return 'fake' if data.startswith(b'FAKE') else None


def fake_apply(patch, source):
    if not patch.startswith(b'FAKE'):
        raise PatchError('not a fake patch')
    want = int.from_bytes(patch[4:8], 'big')
    if zlib.crc32(source) & 0xFFFFFFFF != want:
        raise PatchError('wrong source', source_crc32=want)
    out = bytearray(source)
    for i in range(8, len(patch), 5):
        out[int.from_bytes(patch[i:i + 4], 'big')] = patch[i + 4]
    return bytes(out), {'format': 'fake', 'changes': (len(patch) - 8) // 5}


def make_patch(source, edits):
    p = bytearray(b'FAKE' + (zlib.crc32(source) & 0xFFFFFFFF).to_bytes(4, 'big'))
    for off, val in edits:
        p += off.to_bytes(4, 'big') + bytes([val])
    return bytes(p)


PATCHFMT = types.SimpleNamespace(detect=fake_detect, apply=fake_apply, PatchError=PatchError)
STOCK = fake_rom()
REV0 = fake_rom(revision=0, salt=1)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self._saved = ({k: dict(v) for k, v in ai.GAMES.items()}, dict(ai.OTHER_DUMPS_CRC32))
        ai.GAMES['nomercy']['sha1'] = hashlib.sha1(STOCK).hexdigest().upper()
        ai.OTHER_DUMPS_CRC32.clear()
        ai.OTHER_DUMPS_CRC32['%08X' % (zlib.crc32(REV0) & 0xFFFFFFFF)] = ('nomercy', 'WWF No Mercy (USA) 1.0')
        self.stock_path = self.write('roms/nomercy.z64', STOCK)

    def tearDown(self):
        games, crcs = self._saved
        for k, v in games.items():
            ai.GAMES[k] = v
        ai.OTHER_DUMPS_CRC32.clear()
        ai.OTHER_DUMPS_CRC32.update(crcs)
        self.tmp.cleanup()

    def write(self, rel, data):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def run_job(self, paths, **job):
        job.setdefault('roms', {'nomercy': self.stock_path})
        out = []
        ai.run_job(dict(job, paths=paths), out.append, patchfmt=job.pop('patchfmt', PATCHFMT))
        self.assertEqual(out[-1]['event'], 'done')
        return [r for r in out if r['event'] == 'item'], out[-1]

    def one(self, paths, **job):
        items, _ = self.run_job(paths, **job)
        self.assertEqual(len(items), 1, items)
        return items[0]


class Roms(Base):
    def test_stock_rom_in_every_byte_order(self):
        for conv, order in ((lambda b: b, 'z64'), (v64, 'v64'), (n64, 'n64')):
            r = self.one([self.write('in/nm.' + order, conv(STOCK))])
            self.assertEqual((r['status'], r['game'], r['order']), ('stock', 'nomercy', order))

    def test_hack_rom_is_ready_untested_with_id_and_name(self):
        hack = bytearray(STOCK)
        hack[0x2000] ^= 0xFF
        r = self.one([self.write('in/No_Mercy_Plus V3.z64', bytes(hack))])
        self.assertEqual((r['status'], r['game'], r['route'], r['tier']), ('ready', 'nomercy', 'survey', 'untested'))
        self.assertEqual((r['name'], r['id']), ('No Mercy Plus V3', 'no-mercy-plus-v3'))

    def test_other_region_and_other_game_are_unsupported(self):
        r = self.one([self.write('in/pal.z64', fake_rom(code='NW4P'))])
        self.assertEqual((r['status'], r['game']), ('unsupported', 'nomercy'))
        self.assertIn('PAL', r['why'])
        r = self.one([self.write('in/zelda.z64', fake_rom(code='NZLE', title='ZELDA', salt=5))])
        self.assertEqual(r['status'], 'unsupported')
        self.assertIn('not one of the six', r['why'])

    def test_other_stock_revision_is_stock(self):
        r = self.one([self.write('in/nm10.z64', REV0)])
        self.assertEqual(r['status'], 'stock')
        self.assertIn('1.0', r['why'])

    def test_catalog_match_gives_tier_and_recipe_id(self):
        hack = bytearray(STOCK)
        hack[0x3000] = 1
        sha1 = hashlib.sha1(bytes(hack)).hexdigest().upper()
        cat = self.write('catalog.json', json.dumps({'entries': [{'id': 'nm-plus-v3', 'name': 'No Mercy Plus V3',
            'game': 'nomercy', 'tier': 'verified', 'route': 'derelink', 'inputs': {'sha1': [sha1]}}]}).encode())
        r = self.one([self.write('in/whatever.z64', bytes(hack))], catalog=cat)
        self.assertEqual((r['status'], r['tier'], r['route'], r['id'], r['name']),
                         ('ready', 'verified', 'derelink', 'nm-plus-v3', 'No Mercy Plus V3'))


class Patches(Base):
    def test_patch_applies_to_port_rom(self):
        r = self.one([self.write('in/hack.bps', make_patch(STOCK, [(0x2000, 9)]))])
        self.assertEqual((r['kind'], r['format'], r['status'], r['game'], r['tier']),
                         ('patch', 'fake', 'ready', 'nomercy', 'untested'))
        self.assertEqual(r['base'], 'WWF No Mercy')
        self.assertEqual(r['patch_info']['changes'], 1)

    def test_patch_for_rev0_needs_that_rom_then_uses_it(self):
        patch = self.write('in/somehack.xdelta', make_patch(REV0, [(0x2000, 9)]))
        r = self.one([patch])
        self.assertEqual(r['status'], 'needs-rom')
        self.assertEqual(r['needs'], 'WWF No Mercy (USA) 1.0')
        rev0 = self.write('in/nm10.z64', REV0)
        items, _ = self.run_job([patch, rev0])
        by = {os.path.basename(i['item']): i for i in items}
        self.assertEqual(by['somehack.xdelta']['status'], 'ready')
        self.assertEqual(by['somehack.xdelta']['base'], 'WWF No Mercy (USA) 1.0')
        self.assertEqual(by['nm10.z64']['status'], 'stock')

    def test_patch_without_any_rom(self):
        r = self.one([self.write('in/x.bps', make_patch(REV0, []))], roms={})
        self.assertEqual(r['status'], 'needs-rom')

    def test_patch_that_restores_stock_is_stock(self):
        r = self.one([self.write('in/noop.bps', make_patch(STOCK, []))])
        self.assertEqual(r['status'], 'stock')

    def test_without_patch_engine_patches_are_recognised_but_fail(self):
        r = self.one([self.write('in/a.bps', b'BPS1' + bytes(20))], patchfmt=None, no_patchfmt=True)
        self.assertEqual((r['kind'], r['format'], r['status']), ('patch', 'bps', 'failed'))
        self.assertIn('patchfmt', r['why'])

    def test_bad_patch_file(self):
        r = self.one([self.write('in/a.ips', b'garbage')])
        self.assertEqual(r['status'], 'failed')


class Containers(Base):
    def zip_bytes(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)
        return buf.getvalue()

    def test_zip_members_are_items(self):
        hack = bytearray(STOCK)
        hack[0x2000] = 5
        z = self.write('in/pack.zip', self.zip_bytes({'readme.txt': b'hi', 'Hack v1.z64': bytes(hack),
                                                      'p/fix.bps': make_patch(STOCK, [(0x2001, 1)])}))
        items, _ = self.run_job([z])
        self.assertEqual(sorted(os.path.basename(i['item'].split('!')[-1]) for i in items), ['Hack v1.z64', 'fix.bps'])
        self.assertTrue(all(i['status'] == 'ready' for i in items), items)
        self.assertIn('hack-v1', [i['id'] for i in items])

    def test_zip_texture_pack_is_one_item(self):
        names = {'WWF NO MERCY/WWF NO MERCY#%08X#0#2_all.png' % i: b'png' for i in range(6)}
        r = self.one([self.write('in/hd.zip', self.zip_bytes(names))])
        self.assertEqual((r['kind'], r['naming'], r['images'], r['route']), ('texture-pack', 'rice', 6, 'wrap-rice'))
        self.assertEqual(r['rom_name'], 'WWF NO MERCY')
        self.assertEqual(r['game'], 'nomercy')   # matched against the port ROM's header name (case-insensitive)

    def test_package_and_nrm_install_as_is(self):
        r = self.one([self.write('in/a.zip', self.zip_bytes({'aki-package.json': b'{}'}))])
        self.assertEqual((r['kind'], r['route']), ('package', 'install'))
        r = self.one([self.write('in/m.nrm', self.zip_bytes({'mod.json': b'{}', 'patch.bps': b'x'}))])
        self.assertEqual((r['kind'], r['route']), ('nrm', 'install'))

    def test_pj64_bundle_folder_is_one_item(self):
        for rel in ('Project64.exe', 'Config/Project64.cfg', 'Config/Project64.cht', 'Save/NM.fla',
                    'Plugin/hires_texture/WWF NO MERCY/a#00000000#0#2_all.png'):
            self.write('in/Some Mod Bundle/' + rel, b'x')
        r = self.one([os.path.join(self.dir, 'in')])
        self.assertEqual((r['kind'], r['route'], r['name'], r['id']),
                         ('pj64-bundle', 'import-pj64', 'Some Mod Bundle', 'some-mod-bundle'))
        self.assertEqual(r['parts'], ['codes', 'save', 'textures'])

    def test_jabo_folder_asks_which_game(self):
        for i in range(5):
            self.write('in/jabo/%032x.png' % i, b'x')
        r = self.one([os.path.join(self.dir, 'in', 'jabo')])
        self.assertEqual((r['naming'], r['ask']), ('jabo', 'which game'))

    def textures_save_mod(self, root='in/WWF MOD', save='WWF No Mercy', extra=()):
        # N64Modderz64's shape (aki-roadmap#172): a GLideN64 .hts, a Project64 Save folder with a flash save, a pak and a
        # savestate, no Project64.cfg or cheats. Contents are placeholders: classification reads names only.
        self.write(root + '/Project64 - Plugin - GFX - GLideN64 - Cache/WWF No Mercy_HIRESTEXTURES.hts', b'x')
        for n in (save + '.fla', save + '_Cont_1.mpk', 'WWF No Mercy (U) (V1.0).pj.zip') + tuple(extra):
            self.write(root + '/Project64 - Save/WWF No Mercy-0123/' + n, b'x')
        return os.path.join(self.dir, *root.split('/'))

    def test_hts_names_its_rom(self):
        self.assertEqual(ai.texture_naming(['a/WWF No Mercy_HIRESTEXTURES.hts']), ('hts', 1, 'WWF No Mercy'))
        self.assertEqual(ai.texture_naming(['cache.hts'])[2], None)
        self.assertEqual(ai.texture_naming(['A_HIRESTEXTURES.hts', 'B_HIRESTEXTURES.hts'])[2], None)

    def test_textures_and_save_folder_is_one_conversion(self):
        r = self.one([os.path.dirname(self.textures_save_mod())])
        self.assertEqual((r['kind'], r['route'], r['bundle'], r['game'], r['rom_name'], r['name']),
                         ('pj64-bundle', 'import-pj64', 'textures-save', 'nomercy', 'WWF No Mercy', 'WWF MOD'))
        self.assertEqual(r['parts'], ['save', 'textures'])

    def test_zipped_textures_and_save_is_one_conversion(self):
        files = {'WWF MOD/Cache/WWF NO MERCY#%08X#0#2_all.png' % i: b'png' for i in range(6)}
        files['WWF MOD/Save/WWF NO MERCY.fla'] = b'x'
        r = self.one([self.write('in/mod.zip', self.zip_bytes(files))])
        self.assertEqual((r['kind'], r['bundle'], r['game']), ('pj64-bundle', 'textures-save', 'nomercy'))

    def test_save_for_another_rom_stays_apart(self):
        items, _ = self.run_job([self.textures_save_mod(save='Some Other Game')])
        self.assertEqual(sorted((i['kind'], i['status']) for i in items),
                         [('save', 'unsupported'), ('save', 'unsupported'), ('texture-pack', 'ready'),
                          ('unknown', 'unsupported')])   # the savestate placeholder

    def test_textures_and_save_beside_a_rom_stay_apart(self):
        path = self.textures_save_mod()
        self.write('in/WWF MOD/hack.z64', fake_rom(salt=9))
        items, _ = self.run_job([path])
        self.assertNotIn('pj64-bundle', [i.get('kind') for i in items])
        self.assertIn('rom', [i.get('kind') for i in items])

    def test_rar_and_7z_without_tool(self):
        rar = self.write('in/a.rar', b'Rar!\x1a\x07\x00')
        sz = self.write('in/b.7z', b"7z\xbc\xaf\x27\x1c\x00\x04")
        items, _ = self.run_job([rar, sz], sevenzip=os.path.join(self.dir, 'missing.exe'))
        self.assertEqual({i['status'] for i in items}, {'unsupported'})

    def test_rar_says_how_to_extract_it(self):
        # aki-roadmap#172: N64Modderz64's WWF No Mercy MOD ships as a .rar; the player is told what to do.
        rar = self.write('in/WWF MOD - Public Master V1.0.rar', b'Rar!\x1a\x07\x01\x00' + bytes(32))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('inner.rar', b'Rar!\x1a\x07\x00' + bytes(32))
            z.writestr('readme.txt', b'hello')
        outer = self.write('in/wrapped.zip', buf.getvalue())
        items, _ = self.run_job([rar, outer])
        rars = [i for i in items if i.get('kind') == 'archive']
        self.assertEqual(len(rars), 2, items)
        self.assertEqual(len(items), 2, items)          # readme.txt makes no item
        for i in rars:
            self.assertEqual(i['status'], 'unsupported')
            self.assertIn('7-Zip', i['why'])
            self.assertIn('import the folder', i['why'])

    def test_loose_save_and_cheats(self):
        items, _ = self.run_job([self.write('in/a.fla', bytes(16)), self.write('in/b.cht', b'[x]')])
        self.assertEqual(sorted(i['kind'] for i in items), ['codes', 'save'])


class LedgerAndIds(Base):
    def test_rerun_is_have_it_and_ids_stay_unique(self):
        ledger = os.path.join(self.dir, 'ledger.json')
        h1, h2 = bytearray(STOCK), bytearray(STOCK)
        h1[0x2000], h2[0x2000] = 1, 2
        a = self.write('in/a/Hack.z64', bytes(h1))
        b = self.write('in/b/Hack.z64', bytes(h2))
        items, _ = self.run_job([a, b], ledger=ledger, record=True)
        self.assertEqual(sorted(i['id'] for i in items), ['hack', 'hack-2'])
        items, done = self.run_job([a, b], ledger=ledger)
        self.assertEqual({i['status'] for i in items}, {'have-it'})
        self.assertEqual(done['counts'], {'have-it': 2})
        c = self.write('in/c/Hack.z64', bytes(STOCK[:0x2000]) + b'\x04' + bytes(STOCK[0x2001:]))
        self.assertEqual(self.one([c], ledger=ledger)['id'], 'hack-3')

    def test_same_input_twice_in_one_batch(self):
        a = self.write('in/a.z64', fake_rom(salt=5))
        b = self.write('in/b.v64', v64(fake_rom(salt=5)))
        items, _ = self.run_job([a, b])
        self.assertEqual([i['status'] for i in items], ['ready', 'have-it'])

    def test_one_bad_item_does_not_stop_the_batch(self):
        def boom(data):
            raise RuntimeError('bad')
        pf = types.SimpleNamespace(detect=fake_detect, apply=boom, PatchError=PatchError)
        items, _ = self.run_job([self.write('in/a.bps', make_patch(STOCK, [])), self.stock_path], patchfmt=pf)
        self.assertEqual(sorted(i['status'] for i in items), ['failed', 'stock'])

    def test_slug(self):
        self.assertEqual(ai.slug('  WWF: No Mercy+ (v2.1)!'), 'wwf-no-mercy-v2-1')
        self.assertEqual(ai.slug('***'), 'import')

    def test_plan_does_not_record(self):
        ledger = os.path.join(self.dir, 'ledger.json')
        a = self.write('in/a.z64', fake_rom(salt=9))
        self.run_job([a], ledger=ledger)
        self.assertEqual(self.one([a], ledger=ledger)['status'], 'ready')


# Review findings (hostile or odd input): each must fail alone, never stop the batch.
class Hostile(Base):
    def zip_bytes(self, files, method=zipfile.ZIP_STORED):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', method) as z:
            for name, data in files.items():
                z.writestr(name, data)
        return buf.getvalue()

    def test_odd_length_byte_swapped_files(self):
        items, _ = self.run_job([self.write('in/a.v64', b'\x37\x80\x40\x12\x00'),
                                 self.write('in/b.n64', b'\x40\x12\x37\x80\x00\x00'), self.stock_path])
        by = {os.path.basename(i['item']): i['status'] for i in items}
        self.assertEqual(by, {'a.v64': 'failed', 'b.n64': 'failed', 'nomercy.z64': 'stock'})

    def test_corrupt_zip_member_fails_alone(self):
        blob = bytearray(self.zip_bytes({'a.z64': fake_rom(salt=3), 'b.z64': fake_rom(salt=4)}))
        i = blob.find(fake_rom(salt=3)[0x1000:0x1100])
        blob[i + 10] ^= 0xFF   # CRC mismatch on read
        items, _ = self.run_job([self.write('in/c.zip', bytes(blob))])
        self.assertEqual(sorted(x['status'] for x in items), ['failed', 'ready'])

    def test_size_caps(self):
        old = ai.MAX_ITEM
        ai.MAX_ITEM = 0x3000
        try:
            items, _ = self.run_job([self.write('in/big.z64', fake_rom(salt=2)),
                                     self.write('in/big.zip', self.zip_bytes({'x.z64': fake_rom(salt=2)}))], roms={})
        finally:
            ai.MAX_ITEM = old
        self.assertEqual([x['status'] for x in items], ['unsupported', 'unsupported'], items)
        self.assertTrue(all('limit' in x['why'] for x in items))

    def test_zip_bomb_ratio_is_refused(self):
        bomb = self.zip_bytes({'x.z64': b'\x80\x37\x12\x40' + bytes(17 << 20)}, zipfile.ZIP_DEFLATED)
        r = self.one([self.write('in/bomb.zip', bomb)])
        self.assertEqual(r['status'], 'unsupported')
        self.assertIn('zip bomb', r['why'])

    def test_same_size_texture_packs_differ_by_content(self):
        for pack, byte in (('p1', b'a'), ('p2', b'b')):
            for i in range(5):
                self.write('in/%s/%032x.png' % (pack, i), byte)
        items, _ = self.run_job([os.path.join(self.dir, 'in', 'p1'), os.path.join(self.dir, 'in', 'p2')])
        self.assertEqual([i['status'] for i in items], ['ready', 'ready'])

    def test_patch_without_source_check_prefers_its_own_game(self):
        wt = fake_rom(code='NWNE', title='WCW vs nWo World Tour', salt=7)
        ai.GAMES['wt']['sha1'] = hashlib.sha1(wt).hexdigest().upper()
        wt_path = self.write('roms/wcw.z64', wt)

        def any_apply(patch, source):   # like IPS: no source check, changes one byte
            out = bytearray(source)
            out[0x2000] ^= 1
            return bytes(out), {}
        pf = types.SimpleNamespace(detect=lambda d: 'ips' if d.startswith(b'PATCH') else None, apply=any_apply,
                                   PatchError=PatchError)
        hack_is_nm = self.write('in/nm.ips', b'PATCH' + bytes(3) + b'EOF')
        items, _ = self.run_job([hack_is_nm], roms={'wt': wt_path, 'nomercy': self.stock_path}, patchfmt=pf)
        self.assertEqual(items[0]['status'], 'ready')
        self.assertEqual(items[0]['base'], 'WCW vs. nWo World Tour')   # the first base: both games match themselves

        def nm_header(patch, source):   # the result always carries No Mercy's header
            out = bytearray(source)
            out[0x3B:0x3F] = b'NW4E'
            out[0x2000] ^= 1
            return bytes(out), {}
        pf.apply = nm_header
        items, _ = self.run_job([hack_is_nm], roms={'wt': wt_path, 'nomercy': self.stock_path}, patchfmt=pf)
        self.assertEqual((items[0]['game'], items[0]['base']), ('nomercy', 'WWF No Mercy'))

    def test_non_patcherror_moves_to_next_base(self):
        calls = []

        def flaky(patch, source):
            calls.append(1)
            if len(calls) == 1:
                raise MemoryError('first base')
            return fake_apply(patch, source)
        pf = types.SimpleNamespace(detect=fake_detect, apply=flaky, PatchError=PatchError)
        rev0 = self.write('roms/nm10.z64', REV0)
        r = self.one([self.write('in/p.bps', make_patch(REV0, [(0x2000, 1)]))], rom_dirs=[os.path.dirname(rev0)],
                     roms={}, patchfmt=pf)
        self.assertEqual((r['status'], r['base']), ('ready', 'WWF No Mercy (USA) 1.0'))

    def test_bundle_wrapped_in_a_distribution_folder(self):
        # WWF Legends: Challenge 64's shape (aki-roadmap#265): the Project64 folder sits beside read-me files and a
        # launcher exe inside the archive's top folder. It is one bundle, not a pile of files (its ROM was built alone).
        files = {'Ch64 - Public/Project64/Project64.exe': b'x', 'Ch64 - Public/Project64/Config/Project64.cfg': b'x',
                 'Ch64 - Public/Project64/Config/Project64.cht': b'x', 'Ch64 - Public/Project64/Roms/Hack.z64': fake_rom(salt=9),
                 'Ch64 - Public/Help Files & Setup/Read Me.txt': b'x', 'Ch64 - Public/Run The Mod.exe': b'x'}
        r = self.one([self.write('in/ch.zip', self.zip_bytes(files))])
        self.assertEqual((r['kind'], r['route']), ('pj64-bundle', 'import-pj64'))
        # Unzipped, the same.
        for rel, data in files.items():
            self.write('in2/' + rel, data)
        r = self.one([os.path.join(self.dir, 'in2', 'Ch64 - Public')])
        self.assertEqual((r['kind'], r['route'], r['name']), ('pj64-bundle', 'import-pj64', 'Ch64 - Public'))
        self.assertEqual(ai.bundle_root(list(files), zipped=True), 'Project64/')
        self.assertEqual(ai.bundle_root([k.split('/', 1)[1] for k in files], zipped=True), 'Project64/')
        self.assertEqual(ai.bundle_root([k.split('/', 1)[1] for k in files]), 'Project64/')  # files of its own beside it
        self.assertIsNone(ai.bundle_root(['Wrapped/Project64.exe', 'Wrapped/Save/a.fla']))     # folders only: walked

    def test_two_wrapped_bundles_are_not_one(self):
        names = ['A/Project64.exe', 'A/Config/Project64.cfg', 'B/Project64.exe', 'B/Config/Project64.cfg']
        self.assertIsNone(ai.bundle_root(names, zipped=True))
        self.assertIsNone(ai.bundle_root(['top/x/y/Project64.exe'], zipped=True))   # two levels down: not a bundle
        self.assertEqual(ai.bundle_root(['Project64.exe', 'Save/a.fla']), '')

    def test_bundle_zipped_under_one_root_folder(self):
        r = self.one([self.write('in/b.zip', self.zip_bytes({'My Mod/Project64.exe': b'x',
                                                             'My Mod/Config/Project64.cfg': b'x',
                                                             'My Mod/Save/a.fla': b'x'}))])
        self.assertEqual(r['kind'], 'pj64-bundle')

    def test_mod_json_deep_in_a_zip_is_not_a_mod(self):
        items, _ = self.run_job([self.write('in/m.zip', self.zip_bytes({'a/b/mod.json': b'{}',
                                                                        'hack.z64': fake_rom(salt=6)}))])
        self.assertEqual([i['kind'] for i in items], ['rom'])

    def test_links_are_not_followed(self):
        target = os.path.join(self.dir, 'in', 'real')
        os.makedirs(target)
        self.write('in/real/a.z64', fake_rom(salt=8))
        link = os.path.join(self.dir, 'in', 'real', 'loop')
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('no symlink privilege')
        items, _ = self.run_job([target])
        # The ROM once (the loop isn't walked) and the link as its own item, saying to pick what it points to.
        # (Creating a symlink needs a privilege most accounts lack, so elsewhere this test skips.)
        self.assertEqual(sorted((os.path.basename(i['item']), i['status']) for i in items),
                         [('a.z64', 'ready'), ('loop', 'unsupported')])
        self.assertIn('link', [i for i in items if i['status'] == 'unsupported'][0]['why'])

    def test_bytes_are_released_after_each_item(self):
        srcs = []
        real = ai.classify

        def spy(src, *a, **kw):
            srcs.append(src)
            return real(src, *a, **kw)
        ai.classify = spy
        try:
            self.run_job([self.write('in/a.z64', fake_rom(salt=1)), self.stock_path])
        finally:
            ai.classify = real
        self.assertTrue(srcs and all(s._data is None for s in srcs))

    def test_7z_with_a_fake_7zr(self):
        # A stand-in 7zr: copies the "archive"'s payload (after the 7z magic) to <out>/inner.z64.
        script = self.write('tools/fake7z.py', (
            'import sys, os\n'
            'if sys.argv[1] == "l":\n'   # unpack.py lists first: one entry, with its real size
            '    n = os.path.getsize(sys.argv[-1]) - 6\n'
            '    print("--\\n----------\\nPath = inner.z64\\nSize = %d\\nFolder = -\\n" % n)\n'
            '    sys.exit(0)\n'
            'out =[a[2:] for a in sys.argv if a.startswith("-o")][0]\n'
            'data = open(sys.argv[-1], "rb").read()[6:]\n'
            'open(os.path.join(out, "inner.z64"), "wb").write(data)\n').encode())
        exe = self.write('tools/fake7zr.cmd', ('@"%s" -I "%s" %%*\r\n' % (sys.executable, script)).encode())
        arc = self.write('in/a.7z', b"7z\xbc\xaf\x27\x1c" + fake_rom(salt=11))
        work = os.path.join(self.dir, 'work')
        items, _ = self.run_job([arc], sevenzip=exe, work=work)
        self.assertEqual(len(items), 1, items)
        self.assertEqual((items[0]['status'], items[0]['item']), ('ready', arc + '!inner.z64'))
        self.assertTrue(any(f.startswith('7z-') for f in os.listdir(work)))
        items, _ = self.run_job([arc], sevenzip=exe)   # no --work: the temp unpack folder is removed
        self.assertEqual(items[0]['status'], 'ready')
        # Through unpack.py: the size cap holds for 7z archives too.
        items, _ = self.run_job([arc], sevenzip=exe, work=os.path.join(self.dir, 'work2'), max_unpack=1024)
        self.assertEqual((items[0]['status'], items[0]['item']), ('unsupported', arc), items)
        self.assertIn('more than', items[0]['why'])


class Worker(Base):
    def test_worker_is_one_process_streaming_json_lines(self):
        job = {'paths': [self.stock_path, self.write('in/x.z64', fake_rom(code='NZLE'))], 'no_patchfmt': True}
        r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'aki_import.py'), 'worker'],
                           input=json.dumps(job).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        lines = [json.loads(l) for l in r.stdout.decode().splitlines()]
        self.assertEqual([l['event'] for l in lines], ['item', 'item', 'done'])
        # The real SHA-1 table: a synthetic "No Mercy" is a hack of it, the other header isn't an AKI game.
        self.assertEqual(sorted(l['status'] for l in lines[:2]), ['ready', 'unsupported'])


if __name__ == '__main__':
    unittest.main()
