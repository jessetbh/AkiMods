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
        r = self.one([self.write('in/zelda.z64', fake_rom(code='NZLE', title='ZELDA'))])
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

    def test_rar_and_7z_without_tool(self):
        rar = self.write('in/a.rar', b'Rar!\x1a\x07\x00')
        sz = self.write('in/b.7z', b"7z\xbc\xaf\x27\x1c\x00\x04")
        items, _ = self.run_job([rar, sz], sevenzip=os.path.join(self.dir, 'missing.exe'))
        self.assertEqual({i['status'] for i in items}, {'unsupported'})

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
        items, _ = self.run_job([a, b], ledger=ledger)
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
