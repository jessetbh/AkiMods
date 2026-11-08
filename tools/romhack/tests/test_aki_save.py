"""Tests for aki-save.py on synthetic save images (no real saves): python tests\\test_aki_save.py"""
import datetime
import importlib.util
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'aki_save.py')
spec = importlib.util.spec_from_file_location('aki_save', TOOL)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def run(*args, ok=True):
    r = subprocess.run([sys.executable, TOOL] + [str(a) for a in args], capture_output=True, text=True)
    if ok and r.returncode != 0:
        raise AssertionError('aki-save %s failed: %s' % (' '.join(map(str, args)), r.stderr))
    if not ok and r.returncode == 0:
        raise AssertionError('aki-save %s should have failed:\n%s' % (' '.join(map(str, args)), r.stdout))
    return r


def ws32(b):
    return b''.join(b[i:i + 4][::-1] for i in range(0, len(b), 4))


def nm_flash(seed=1, header_sum_ok=True):
    """Synthetic No Mercy flash: erased (0xFF) chip, random data pages and a header page on page 40 (layout per
    aki-roadmap#51: u16 link, u16 type 0x4000, u32 XOR page sum, tags at +0x10 and +0x20)."""
    rnd = random.Random(seed)
    d = bytearray(b'\xff' * S.FLASH)
    for p in range(1, 40):
        d[p * 0x80:(p + 1) * 0x80] = bytes(rnd.randrange(256) for _ in range(0x80))
    h = bytearray(0x80)
    h[0:8] = struct.pack('>HHI', 39, 0x4000, 0)
    h[0x08:0x10] = struct.pack('>II', 0, 7)
    h[0x10:0x18] = b'WWF2 AKI'
    h[0x18:0x1C] = struct.pack('>I', 0x00080721)
    h[0x20:0x28] = b'NO MERCY'
    h[4:8] = struct.pack('>I', S.nm_page_sum(bytes(h)) ^ (0 if header_sum_ok else 1))
    d[40 * 0x80:41 * 0x80] = h
    return bytes(d)


def pj64_mpk_init():
    """A blank pak as Project64 2.x formats it (the first 0x110 bytes of its Initilize table, then 00 03 pairs)."""
    d = bytearray(b'\x00\x03' * (S.PAK // 2))
    ident = bytes.fromhex('ffffffff051a5f13' + '00' * 8 + 'ffffffffffffffff' + 'ffff01ff662599cd')
    d[0:0x20] = bytes([0x81] + list(range(1, 0x20)))
    for o in (0x20, 0x60, 0x80, 0xC0):
        d[o:o + 0x20] = ident
    d[0x40:0x60] = bytes(0x20)
    d[0xA0:0xC0] = bytes(0x20)
    d[0xE0:0x100] = bytes(0x20)
    d[0x100:0x110] = bytes.fromhex('00710003000300030003000300030003')
    return bytes(d)


def aki_sram(magic, seed=2):
    rnd = random.Random(seed)
    return struct.pack('>I', magic) * 8 + bytes(rnd.randrange(256) for _ in range(S.SRAM - 32))


def pak(note=None, seed=3):
    """A formatted libultra Controller Pak with an optional note (company, game code)."""
    rnd = random.Random(seed)
    d = bytearray(S.PAK)
    ident = bytearray(bytes(rnd.randrange(256) for _ in range(24)) + b'\x00\x01\x01\x00')
    s = sum((ident[i] << 8) | ident[i + 1] for i in range(0, 28, 2)) & 0xFFFF
    ident += struct.pack('>HH', s, (0xFFF2 - s) & 0xFFFF)
    for o in (0x20, 0x60, 0x80, 0xC0):
        d[o:o + 32] = ident
    if note:
        company, code = note
        d[0x300:0x308] = code + struct.pack('>HH', company, 0x0005)
        for i in range(0x500, 0x1500):
            d[i] = rnd.randrange(256)
    return bytes(d)


NM_NOTE = (0x3738, b'NW4E')
VPW2_NOTE = (0x4538, b'NA2J')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def get(self, path):
        with open(path, 'rb') as f:
            return f.read()


class ByteOrderTests(Base):
    def test_orders_round_trip(self):
        data = bytes(range(256)) * 16
        for o in S.ORDERS:
            self.assertEqual(S.reorder(S.reorder(data, o), o), data, o)
        self.assertEqual(S.reorder(b'\x12\x34\x56\x78', 'ws32'), b'\x78\x56\x34\x12')
        self.assertEqual(S.reorder(b'\x12\x34\x56\x78', 'bs16'), b'\x34\x12\x78\x56')
        self.assertEqual(S.reorder(b'\x12\x34\x56\x78', 'hs16'), b'\x56\x78\x12\x34')

    def test_odd_size_refused(self):
        with self.assertRaises(S.SaveError):
            S.reorder(b'\x00' * 6, 'ws32')


class DetectTests(Base):
    def test_port_flash_is_raw(self):
        info = S.detect(self.put('wwf.nomercy.us.bin', nm_flash()))
        self.assertEqual((info['kind'], info['order'], info['games']), ('flash', 'be', ['nomercy']))

    def test_pj64_fla_is_word_swapped(self):
        info = S.detect(self.put('NO MERCY.fla', ws32(nm_flash())))
        self.assertEqual((info['kind'], info['order'], info['confidence']), ('flash', 'ws32', 'signature'))
        self.assertEqual(info['raw'], nm_flash())

    def test_signature_beats_extension(self):
        # a raw (e.g. cart-dumped) flash named .fla is still detected as raw
        info = S.detect(self.put('dump.fla', nm_flash()))
        self.assertEqual(info['order'], 'be')

    def test_other_orders_by_signature(self):
        for o in ('bs16', 'hs16'):
            info = S.detect(self.put('x%s.sra' % o, S.reorder(aki_sram(0x19991101), o)))
            self.assertEqual((info['order'], info['games']), (o, ['vpw2']), o)

    def test_sram_games_by_magic(self):
        self.assertEqual(S.detect(self.put('a.sra', ws32(aki_sram(0x19990901))))['games'], ['wm2k'])
        self.assertEqual(S.detect(self.put('b.sra', ws32(aki_sram(0x19991101))))['games'], ['vpw2'])
        self.assertEqual(sorted(S.detect(self.put('c.sra', aki_sram(0x19971021)))['games']), ['revenge', 'vpw64'])

    def test_unknown_sra_uses_extension_prior(self):
        rnd = random.Random(9)
        info = S.detect(self.put('x.sra', bytes(rnd.randrange(256) for _ in range(S.SRAM))))
        self.assertEqual((info['order'], info['confidence']), ('ws32', 'extension'))
        info = S.detect(self.put('x.bin', bytes(rnd.randrange(256) for _ in range(S.SRAM))))
        self.assertEqual((info['kind'], info['order']), ('sram', 'be'))

    def test_pak_32k_and_notes(self):
        info = S.detect(self.put('NO MERCY_Cont_1.mpk', pak(NM_NOTE)))
        self.assertEqual((info['kind'], info['order'], info['games']), ('pak', 'be', ['nomercy']))
        self.assertTrue(info['formatted'])
        # a 32 KB .bin that is a pak (WT's main save) is told apart from SRAM by the pak ID block
        self.assertEqual(S.detect(self.put('wcw.bin', pak((0x3738, b'NWNE'))))['kind'], 'pak')

    def test_mupen_mpk_4_paks(self):
        data = pak() + pak(VPW2_NOTE) + bytes(S.PAK) + bytes(S.PAK)
        info = S.detect(self.put('VPW2.mpk', data))
        self.assertEqual(info['kind'], 'pak4')
        self.assertEqual([p['index'] for p in info['paks'] if p['notes']], [1])
        self.assertEqual(info['games'], ['vpw2'])

    def test_nm_header_checksum(self):
        self.assertEqual(S.nm_valid_headers(nm_flash()), 1)
        self.assertEqual(S.nm_valid_headers(nm_flash(header_sum_ok=False)), 0)
        self.assertEqual(S.detect(self.put('a.fla', ws32(nm_flash())))['nm_valid_headers'], 1)

    def test_pj64_2x_single_mpk_4_paks(self):
        # Project64 1.6/2.x: one <game>.mpk holding all four paks, each pre-formatted by PJ64; pak 0 has the note
        p0 = bytearray(pj64_mpk_init())
        p0[0x300:0x308] = b'NW4E' + struct.pack('>HH', 0x3738, 5)
        data = bytes(p0) + pj64_mpk_init() * 3
        info = S.detect(self.put('WWF No Mercy.mpk', data))
        self.assertEqual(info['kind'], 'pak4')
        self.assertTrue(all(p['formatted'] for p in info['paks']))
        files, _ = S.port_files(self.put('WWF No Mercy.mpk', data), 'nomercy')
        self.assertEqual(files[0][1], bytes(p0))

    def test_pj64_3x_cont_mpk(self):
        # Project64 3.x+: <game>_Cont_1.mpk, one 32 KB pak per controller, raw order
        info = S.detect(self.put('WWF No Mercy_Cont_1.mpk', pj64_mpk_init()))
        self.assertEqual((info['kind'], info['order'], info['formatted']), ('pak', 'be', True))

    def test_truncated_pj64_sra_is_padded(self):
        info = S.detect(self.put('t.sra', ws32(aki_sram(0x19991101))[:0x2000]))
        self.assertEqual((info['order'], info['games'], len(info['raw'])), ('ws32', ['vpw2'], S.SRAM))

    def test_eeprom(self):
        self.assertEqual(S.detect(self.put('x.eep', b'\x01' * 0x200))['kind'], 'eep4k')
        self.assertEqual(S.detect(self.put('x.eep', b'\x01' * 0x800))['kind'], 'eep16k')

    def test_wrong_size_refused(self):
        with self.assertRaises(S.SaveError):
            S.detect(self.put('x.bin', b'\0' * 1000))
        r = run('detect', self.put('y.sav', b'\0' * 0x9000), ok=False)
        self.assertIn('not a FlashRAM', r.stderr)

    def test_truncated_pj64_fla_is_padded(self):
        info = S.detect(self.put('t.fla', ws32(nm_flash())[:0x4000]))
        self.assertEqual(len(info['raw']), S.FLASH)
        self.assertEqual(info['raw'][:0x4000], nm_flash()[:0x4000])
        self.assertEqual(info['raw'][0x4000:], b'\xff' * (S.FLASH - 0x4000))
        self.assertTrue(any('padded' in n for n in info['notes']))

    def test_cli_json(self):
        out = json.loads(run('detect', self.put('a.fla', ws32(nm_flash())), '--json').stdout)
        self.assertEqual((out['kind'], out['order']), ('flash', 'ws32'))
        self.assertNotIn('raw', out)


def make_srm(flash=None, sram=None, paks=(None, None, None, None), eep=None):
    d = (eep or b'\xff' * S.EEP16K)
    for p in paks:
        d += p if p is not None else bytes(S.PAK)
    d += ws32(sram) if sram else b'\xff' * S.SRAM
    d += ws32(flash) if flash else b'\xff' * S.FLASH
    assert len(d) == S.SRM
    return d


class SrmTests(Base):
    def test_layout_size(self):
        self.assertEqual(sum(s for _, _, s in S.SRM_LAYOUT), S.SRM)
        self.assertEqual(S.SRM, 0x48800)

    def test_detect_sections(self):
        info = S.detect(self.put('NM.srm', make_srm(flash=nm_flash(), paks=(pak(NM_NOTE), None, None, None))))
        self.assertEqual(info['kind'], 'srm')
        used = {n for n, s in info['sections'].items() if s['used']}
        self.assertEqual(used, {'flash', 'pak0'})
        self.assertEqual(info['sections']['flash']['games'], ['nomercy'])

    def test_convert_srm_to_port_files(self):
        src = self.put('NM.srm', make_srm(flash=nm_flash(), paks=(pak(NM_NOTE), None, None, None)))
        out = os.path.join(self.d, 'out')
        os.makedirs(out)
        run('convert', src, '--game', 'nomercy', '-o', out)
        self.assertEqual(self.get(os.path.join(out, 'wwf.nomercy.us.bin')), nm_flash())
        self.assertEqual(self.get(os.path.join(out, 'wwf.nomercy.us.pak.bin')), pak(NM_NOTE))

    def test_srm_without_the_games_media_refused(self):
        src = self.put('W.srm', make_srm(sram=aki_sram(0x19990901)))
        r = run('convert', src, '--game', 'nomercy', '-o', os.path.join(self.d, 'x.bin'), ok=False)
        self.assertIn('nothing nomercy uses', r.stderr)

    def test_srm_vpw2_sram(self):
        src = self.put('V.srm', make_srm(sram=aki_sram(0x19991101)))
        files, _ = S.port_files(src, 'vpw2')
        self.assertEqual([(t, r) for t, r, _ in files], [('main', aki_sram(0x19991101))])


class ConvertTests(Base):
    def test_fla_to_port_and_back(self):
        src = self.put('NO MERCY.fla', ws32(nm_flash()))
        out = os.path.join(self.d, 'wwf.nomercy.us.bin')
        run('convert', src, '--game', 'nomercy', '-o', out)
        self.assertEqual(self.get(out), nm_flash())
        back = os.path.join(self.d, 'back.fla')
        run('convert', out, '--game', 'nomercy', '--to', 'pj64', '-o', back)
        self.assertEqual(self.get(back), ws32(nm_flash()))

    def test_refuses_existing_output(self):
        src = self.put('a.fla', ws32(nm_flash()))
        out = self.put('o.bin', b'x')
        run('convert', src, '--game', 'nomercy', '-o', out, ok=False)
        run('convert', src, '--game', 'nomercy', '-o', out, '--overwrite')

    def test_wrong_game_refused(self):
        src = self.put('vpw2.sra', ws32(aki_sram(0x19991101)))
        r = run('convert', src, '--game', 'wm2k', '-o', os.path.join(self.d, 'o.bin'), ok=False)
        self.assertIn('looks like vpw2', r.stderr)

    def test_wrong_media_refused(self):
        # No Mercy has no cart SRAM; Revenge has no separate pak image; nobody uses EEPROM
        r = run('convert', self.put('a.sra', ws32(aki_sram(0x19991101))), '--game', 'nomercy', '-o',
                os.path.join(self.d, 'o.bin'), ok=False)
        self.assertIn('does not use sram', r.stderr)
        r = run('convert', self.put('b.mpk', pak(NM_NOTE)), '--game', 'revenge', '-o',
                os.path.join(self.d, 'o.bin'), ok=False)
        self.assertIn('does not use pak', r.stderr)
        run('convert', self.put('c.eep', b'\x01' * 0x200), '--game', 'wm2k', '-o', os.path.join(self.d, 'o.bin'),
            ok=False)

    def test_nm_flash_without_valid_header_refused(self):
        src = self.put('bad.fla', ws32(nm_flash(header_sum_ok=False)))
        r = run('convert', src, '--game', 'nomercy', '-o', os.path.join(self.d, 'o.bin'), ok=False)
        self.assertIn('valid checksum', r.stderr)
        run('convert', src, '--game', 'nomercy', '-o', os.path.join(self.d, 'o.bin'), '--force')

    def test_blank_refused(self):
        r = run('convert', self.put('e.fla', b'\xff' * S.FLASH), '--game', 'nomercy', '-o',
                os.path.join(self.d, 'o.bin'), ok=False)
        self.assertIn('blank', r.stderr)

    def test_unformatted_pak_refused(self):
        rnd = random.Random(4)
        junk = bytes(rnd.randrange(256) for _ in range(S.PAK))
        files = None
        with self.assertRaises(S.SaveError):
            files = S.port_files(self.put('j.mpk', junk), 'nomercy')
        self.assertIsNone(files)

    def test_wt_pak_goes_to_main_file(self):
        files, _ = S.port_files(self.put('WCW_Cont_1.mpk', pak((0x3738, b'NWNE'))), 'wt')
        self.assertEqual(files[0][0], 'main')

    def test_pak_from_4_pak_mpk_by_index(self):
        data = pak(NM_NOTE, seed=5) + pak(NM_NOTE, seed=6) + bytes(S.PAK) + bytes(S.PAK)
        src = self.put('NM.mpk', data)
        self.assertEqual(S.port_files(src, 'nomercy')[0][0][1], pak(NM_NOTE, seed=5))
        self.assertEqual(S.port_files(src, 'nomercy', pak=1)[0][0][1], pak(NM_NOTE, seed=6))


class InstallTests(Base):
    WHEN = datetime.datetime(2026, 10, 19, 23, 30, 5)

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.d, 'saves')
        self.stock = self.put('saves/wwf.nomercy.us.bin', nm_flash(seed=77))
        self.stock_pak = self.put('saves/wwf.nomercy.us.pak.bin', pak(NM_NOTE, seed=78))

    def stock_untouched(self):
        self.assertEqual(self.get(self.stock), nm_flash(seed=77))
        self.assertEqual(self.get(self.stock_pak), pak(NM_NOTE, seed=78))

    def test_install_into_conversion_folder_only(self):
        fla = self.put('in/caw.fla', ws32(nm_flash(seed=10)))
        mpk = self.put('in/caw.mpk', pak(NM_NOTE, seed=11))
        res = S.install([fla, mpk], 'nomercy', 'nm_caw_pack', root=self.root, ignore_running=True, now=self.WHEN)
        folder = os.path.join(self.root, 'nm_caw_pack')
        self.assertEqual(self.get(os.path.join(folder, 'wwf.nomercy.us.bin')), nm_flash(seed=10))
        self.assertEqual(self.get(os.path.join(folder, 'wwf.nomercy.us.pak.bin')), pak(NM_NOTE, seed=11))
        self.assertEqual([f['action'] for f in res['files']], ['write', 'write'])
        self.stock_untouched()

    def test_replace_backs_up_with_game_naming(self):
        fla = self.put('in/a.fla', ws32(nm_flash(seed=10)))
        S.install([fla], 'nomercy', 'conv', root=self.root, ignore_running=True, now=self.WHEN)
        fla2 = self.put('in/b.fla', ws32(nm_flash(seed=12)))
        res = S.install([fla2], 'nomercy', 'conv', root=self.root, ignore_running=True, now=self.WHEN)
        r = res['files'][0]
        self.assertEqual(r['action'], 'replace')
        self.assertEqual(os.path.basename(r['backup']), 'wwf.nomercy.us.20261019-233005.bin')
        self.assertEqual(self.get(r['backup']), nm_flash(seed=10))
        self.assertEqual(self.get(r['file']), nm_flash(seed=12))
        # same second again: the backup gets a suffix, never overwrites the first one
        fla3 = self.put('in/c.fla', ws32(nm_flash(seed=13)))
        r3 = S.install([fla3], 'nomercy', 'conv', root=self.root, ignore_running=True, now=self.WHEN)['files'][0]
        self.assertNotEqual(r3['backup'], r['backup'])
        self.assertEqual(self.get(r['backup']), nm_flash(seed=10))
        self.assertEqual(self.get(r3['backup']), nm_flash(seed=12))
        self.stock_untouched()

    def test_pak_backup_name(self):
        self.assertEqual(S.backup_name('wwf.nomercy.us.pak.bin', self.WHEN), 'wwf.nomercy.us.pak.20261019-233005.bin')

    def test_if_missing_and_unchanged(self):
        fla = self.put('in/a.fla', ws32(nm_flash(seed=10)))
        S.install([fla], 'nomercy', 'conv', root=self.root, ignore_running=True)
        self.assertEqual(S.install([fla], 'nomercy', 'conv', root=self.root, ignore_running=True)['files'][0]
                         ['action'], 'unchanged')
        fla2 = self.put('in/b.fla', ws32(nm_flash(seed=12)))
        res = S.install([fla2], 'nomercy', 'conv', root=self.root, ignore_running=True, if_missing=True)
        self.assertEqual(res['files'][0]['action'], 'kept (exists)')
        self.assertEqual(self.get(res['files'][0]['file']), nm_flash(seed=10))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'conv', 'backups')))

    def test_dry_run_writes_nothing(self):
        fla = self.put('in/a.fla', ws32(nm_flash(seed=10)))
        res = S.install([fla], 'nomercy', 'conv', root=self.root, dry_run=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'conv')))
        self.assertEqual(res['files'][0]['file'], os.path.join(self.root, 'conv', 'wwf.nomercy.us.bin'))

    def test_bad_conversion_ids_refused(self):
        fla = self.put('in/a.fla', ws32(nm_flash(seed=10)))
        for cid in ('', '..', '../x', 'a/b', 'a\\b', 'backups', 'Backups', '.hidden', 'x' * 65, 'a..b'):
            with self.assertRaises(S.SaveError, msg=cid):
                S.install([fla], 'nomercy', cid, root=self.root, ignore_running=True)
        self.stock_untouched()
        self.assertEqual(sorted(os.listdir(self.root)), ['wwf.nomercy.us.bin', 'wwf.nomercy.us.pak.bin'])

    def test_failed_validation_writes_nothing(self):
        good = self.put('in/a.fla', ws32(nm_flash(seed=10)))
        bad = self.put('in/b.sra', ws32(aki_sram(0x19991101)))
        with self.assertRaises(S.SaveError):
            S.install([good, bad], 'nomercy', 'conv', root=self.root, ignore_running=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'conv')))

    def test_two_inputs_same_target_refused(self):
        a = self.put('in/a.fla', ws32(nm_flash(seed=10)))
        b = self.put('in/b.fla', ws32(nm_flash(seed=11)))
        with self.assertRaises(S.SaveError):
            S.install([a, b], 'nomercy', 'conv', root=self.root, ignore_running=True)

    def test_portable_and_appdata_roots(self):
        self.assertEqual(S.saves_root('vpw2', portable=self.d), os.path.join(os.path.abspath(self.d), 'saves'))
        self.assertEqual(S.saves_root('vpw2', localappdata='C:\\LAD'), os.path.join('C:\\LAD', 'Vpw2Recompiled',
                                                                                     'saves'))

    def test_cli_install_json(self):
        port = os.path.join(self.d, 'portable')
        os.makedirs(port)
        self.put('portable/portable.txt', b'')
        sra = self.put('in/v.sra', ws32(aki_sram(0x19991101)))
        out = json.loads(run('install', sra, '--game', 'vpw2', '--conversion', 'vpw2_roster', '--portable', port,
                             '--json', '--ignore-running').stdout)
        f = out['files'][0]
        self.assertEqual(f['file'], os.path.join(os.path.abspath(port), 'saves', 'vpw2_roster', 'vpw.vpw2.jp.bin'))
        self.assertEqual(self.get(f['file']), aki_sram(0x19991101))
        self.assertEqual(f['order'], 'ws32')


class GamesTableTests(unittest.TestCase):
    def test_matches_ports(self):
        """GAMES game ids / save types agree with each port's main.cpp when the checkouts are next to AkiTools."""
        depot = os.path.dirname(ROOT)
        repos = {'wt': 'WcwNwoWorldTour', 'revenge': 'WcwRevengeRecomp', 'wm2k': 'Wm2kRecomp',
                 'vpw64': 'Vpw64Recomp', 'nomercy': 'NoMercyRecomp', 'vpw2': 'Vpw2Recomp'}
        checked = 0
        for g, repo in repos.items():
            path = os.path.join(depot, repo, 'src', 'main', 'main.cpp')
            if not os.path.isfile(path):
                continue
            with open(path, encoding='utf-8', errors='replace') as f:
                src = f.read()
            self.assertIn('game.game_id = u8"%s"' % S.GAMES[g]['game_id'], src, g)
            flash = 'SaveType::Flashram' in src
            self.assertEqual(flash, 'flash' in S.GAMES[g]['files'], g)
            sep = 'wcw_pak_separate_backing = true' in src
            self.assertEqual(sep, S.GAMES[g]['files'].get('pak') == 'pak', g)
            checked += 1
        if not checked:
            self.skipTest('no port checkouts next to AkiTools')


if __name__ == '__main__':
    unittest.main()
