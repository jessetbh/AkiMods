"""Tests for aki-conv.py's pure helpers (synthetic data only). Run: python tests/test_aki_conv.py"""
import importlib.util
import io
import json
import os
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('aki_conv', os.path.join(os.path.dirname(HERE), 'aki_conv.py'))
aki_conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aki_conv)
BS = chr(92)

CHT = '\n'.join([
    '[4E4B0640-1B49BCFB-C:45]',
    'Name=WWF No Mercy',
    'Cheat0="Infinite' + BS + 'Money",8109940A FFFF',
    'Cheat1="TNAvsROH' + BS + 'Arenas' + BS + 'ROH No Mercy",D00A2621 0001,810500E8 0933',
    'Cheat1_N=ROH 2002 Arena replaces No Mercy',
    'Cheat2="TNAvsROH' + BS + 'Other' + BS + 'Pick",80100000 ????',
    '[6D8DF08E-D008C3CF-C:50]',
    'Name=WWF No Mercy (E)',
    'Cheat0="TNAvsROH' + BS + 'Arenas' + BS + 'PAL",810500E8 0001',
])


class Cht(unittest.TestCase):
    def test_sections_cheats_and_notes(self):
        s = aki_conv.parse_cht(CHT)
        self.assertEqual(sorted(s), ['4E4B0640-1B49BCFB-C:45', '6D8DF08E-D008C3CF-C:50'])
        us = s['4E4B0640-1B49BCFB-C:45']
        self.assertEqual(us['name'], 'WWF No Mercy')
        self.assertEqual(us['cheats'][1]['codes'], [(0xD00A2621, '0001'), (0x810500E8, '0933')])
        self.assertEqual(us['cheats'][1]['note'], 'ROH 2002 Arena replaces No Mercy')

    def test_mod_cheats_by_folder_name(self):
        us = aki_conv.parse_cht(CHT)['4E4B0640-1B49BCFB-C:45']
        names = [c['name'] for c in aki_conv.mod_cheats(us, 'TNA vs ROH')]
        self.assertEqual(len(names), 2)
        self.assertTrue(all(n.startswith('TNAvsROH') for n in names))

    def test_known_sections_are_revisions(self):
        self.assertEqual(aki_conv.CHT_SECTIONS['4E4B0640-1B49BCFB-C:45'], 'nomercy-rev0')
        self.assertEqual(aki_conv.CHT_SECTIONS['6C80F13B-427EDEAA-C:45'], 'nomercy-rev1')

    def test_codes_txt_skips_choice_codes(self):
        us = aki_conv.parse_cht(CHT)['4E4B0640-1B49BCFB-C:45']
        text = aki_conv.codes_txt(aki_conv.mod_cheats(us, 'TNA vs ROH'), 'nomercy-rev0', 'TNA vs ROH')
        self.assertIn('# rom: nomercy-rev0', text)
        self.assertIn('[TNAvsROH' + BS + 'Arenas' + BS + 'ROH No Mercy]', text)
        self.assertIn('810500E8 0933', text)
        self.assertIn('# skipped (needs a choice value)', text)
        empty = aki_conv.codes_txt([{'name': 'Empty', 'codes': [], 'note': ''}], 'nomercy-rev0', 't')
        self.assertNotIn('[Empty]', empty)


class Translate(unittest.TestCase):
    RANGES = [(0x000000, 0x003EA4, 0), (0x005000, 0x0A0000, 0x90), (0x0A0000, 0x400000, 0xA0)]

    def test_addresses_move_by_range(self):
        self.assertEqual(aki_conv.translate_address(0x001000, self.RANGES), 0x001000)
        self.assertEqual(aki_conv.translate_address(0x0500E8, self.RANGES), 0x050178)
        self.assertEqual(aki_conv.translate_address(0x0A2621, self.RANGES), 0x0A26C1)
        self.assertIsNone(aki_conv.translate_address(0x004000, self.RANGES))  # the changed region: no mapping

    def test_codes_text_keeps_types_and_drops_whole_units(self):
        text = '[x]\nD00A2621 0001\n810500E8 0933\n80004000 0001\n50000402 0002\n'
        out, done, bad = aki_conv.translate_codes(text, self.RANGES)
        self.assertIn('D00A26C1 0001', out)
        self.assertIn('81050178 0933', out)
        self.assertIn('# dropped (no mapping): 80004000 0001', out)
        self.assertIn('50000402 0002', out)  # repeater: a count, not an address
        self.assertEqual((done, len(bad)), (3, 1))

    def test_a_conditional_over_an_unmapped_code_is_dropped_with_it(self):
        # Keeping the conditional alone would make it govern the next code (the engine's rule too).
        out, done, bad = aki_conv.translate_codes('D00A2621 0001\n80004000 0001\n810500E8 0933\n', self.RANGES)
        self.assertIn('# dropped (no mapping): D00A2621 0001', out)
        self.assertIn('81050178 0933', out)
        self.assertEqual((done, len(bad)), (1, 2))


class DerelinkMap(unittest.TestCase):
    MAP = {'main': [[0x80000400, 0x80060000, -0x90, 'text'], [0x80060000, 0x80070000, -0xA0, 'data']],
           'overlays': {'ovl_b': [[0x800DA000, 0x800E0000, -0x1500, 'data']],
                        'ovl_c': [[0x800DA000, 0x800E0000, -0x1500, 'data'], [0x800E0000, 0x800E1000, -0x1500, 'data']],
                        'ovl_d': [[0x800E0000, 0x800E1000, -0x1400, 'data']]},
           'identity': [[0x80400000, 0x80800000], [0x80000000, 0x80000400]]}

    def test_main_identity_and_agreeing_overlays(self):
        r = aki_conv.map_from_derelink(self.MAP)
        t = lambda a: aki_conv.translate_address(a, r)
        self.assertEqual(t(0x001000), 0x001000 - 0x90)
        self.assertEqual(t(0x061000), 0x061000 - 0xA0)
        self.assertEqual(t(0x0DB000), 0x0DB000 - 0x1500)   # b and c agree
        self.assertIsNone(t(0x0E0800))                     # c and d disagree: no mapping
        self.assertEqual(t(0x400100), 0x400100)            # Expansion RAM doesn't move
        self.assertIsNone(t(0x063000))                     # the save-buffer bss is unsafe

class OverlayGuard(unittest.TestCase):
    """Two overlays share a slot at 0x80100000 and moved by different amounts between revisions (synthetic data)."""
    def setUp(self):
        rom = bytearray(0x3000)
        # ovl_x code at ROM 0x1000, ovl_y at 0x2000, both loaded at 0x80100000 (text 0x800 bytes, then data).
        for i in range(0, 0x800, 4):
            rom[0x1000 + i:0x1004 + i] = (0x27BDFFE0).to_bytes(4, 'big')   # same in both...
            rom[0x2000 + i:0x2004 + i] = (0x27BDFFE0).to_bytes(4, 'big')
        rom[0x1010:0x1012] = b'\x3c\x01'                                    # ...except at +0x10
        rom[0x2010:0x2012] = b'\x24\x02'
        self.rom = bytes(rom)
        self.layout = {'sections': [
            {'name': 'main', 'rom': 0x100, 'text': 0x80000400, 'data': 0x80000800, 'slot': None},
            {'name': 'ovl_x', 'rom': 0x1000, 'text': 0x80100000, 'data': 0x80100800, 'slot': 0},
            {'name': 'ovl_y', 'rom': 0x2000, 'text': 0x80100000, 'data': 0x80100800, 'slot': 0}]}
        self.map = {'main': [[0x80000400, 0x80080000, -0x20, 'data']],
                    'overlays': {'ovl_x': [[0x80100000, 0x80110000, -0x30, 'data']],
                                 'ovl_y': [[0x80100000, 0x80110000, -0x10, 'data']]}}

    def test_contested_unit_is_split_behind_guards(self):
        ranges = aki_conv.map_from_derelink(self.map)
        guard = aki_conv.overlay_guard(self.layout, self.rom)
        self.assertEqual(guard(('ovl_x',), ('ovl_y',)), (0x80100010, 0x3C01))
        self.assertEqual(guard(('ovl_y',), ('ovl_x',)), (0x80100010, 0x2402))
        text = 'D0108000 0001\n81108004 1234\n81010000 0005\n'
        out, done, bad = aki_conv.translate_codes(text, ranges, aki_conv.overlay_ranges(self.map), guard)
        self.assertEqual(bad, [])
        self.assertEqual(done, 3)
        self.assertEqual(aki_conv.translate_codes.last['units_split'], 1)
        lines = [l for l in out.splitlines() if l]
        self.assertEqual(lines, ['D1100010 3C01', 'D0107FD0 0001', '81107FD4 1234',
                                 'D1100010 2402', 'D0107FF0 0001', '81107FF4 1234',
                                 '8100FFE0 0005'])

    def test_split_units_take_the_map_range_overrides(self):
        # Showdown 64 (aki-roadmap#228): a high-RAM write behind an overlay-RAM conditional. The plain map's high RAM
        # doesn't move; --map-range 200800,400000,-1000 must reach both guarded versions, as it does unsplit codes.
        m = dict(self.map, main=self.map['main'] + [[0x80200800, 0x80400000, 0, 'heap']])
        overrides = aki_conv.parse_map_ranges(['200800,400000,-1000'])
        ranges = overrides + aki_conv.map_from_derelink(m)
        guard = aki_conv.overlay_guard(self.layout, self.rom)
        text = 'D0108000 0001\n81300000 1234\n81300010 0005\n'
        out, done, bad = aki_conv.translate_codes(
            text, ranges, aki_conv.overlays_with_overrides(aki_conv.overlay_ranges(m), overrides), guard)
        self.assertEqual((done, bad), (3, []))
        self.assertEqual([l for l in out.splitlines() if l],
                         ['D1100010 3C01', 'D0107FD0 0001', '812FF000 1234',
                          'D1100010 2402', 'D0107FF0 0001', '812FF000 1234',
                          '812FF010 0005'])
        self.assertIs(aki_conv.overlays_with_overrides(None, overrides), None)

    def test_without_guards_the_unit_is_dropped(self):
        ranges = aki_conv.map_from_derelink(self.map)
        out, done, bad = aki_conv.translate_codes('81108004 1234\n', ranges)
        self.assertEqual(done, 0)
        self.assertEqual(len(bad), 1)

    def test_one_guard_gives_the_other_group_not_equal(self):
        guard = lambda ins, outs: (0x80100010, 0x3C01) if ins == ('ovl_x',) else None
        out, done, bad = aki_conv.translate_codes('81108004 1234\n', aki_conv.map_from_derelink(self.map),
                                                  aki_conv.overlay_ranges(self.map), guard)
        self.assertEqual([l for l in out.splitlines() if l],
                         ['D1100010 3C01', '81107FD4 1234', 'D3100010 3C01', '81107FF4 1234'])

    def test_patched_rom_must_keep_the_guard(self):
        patched = bytearray(self.rom)
        patched[0x1010:0x1012] = b'\x00\x01'
        guard = aki_conv.overlay_guard(self.layout, self.rom, bytes(patched))
        self.assertIsNone(guard(('ovl_x',), ('ovl_y',)))


class CoresidentOverlays(unittest.TestCase):
    """aki-roadmap#246: slot A holds ovl_a or ovl_e, slot B ovl_b or ovl_c, and ovl_e is big enough to cover both slots
    (No Mercy's layout). ovl_a is loaded with ovl_b or ovl_c (the menus), so a unit can have its condition in slot B and
    its write in slot A: no single overlay places it (synthetic data)."""
    FILL = {'ovl_a': 0x0C030000, 'ovl_e': 0x3C010000, 'ovl_b': 0x27BD0000, 'ovl_c': 0x24020000}

    def setUp(self):
        secs = [('ovl_a', 0x1000, 0x80100000, 0x80100800, 0x80101000, 0x80100000),
                ('ovl_e', 0x2000, 0x80100000, 0x80102800, 0x80103000, 0x80100000),
                ('ovl_b', 0x5000, 0x80102000, 0x80102800, 0x80103000, 0x80102000),
                ('ovl_c', 0x6000, 0x80102000, 0x80102800, 0x80103000, 0x80102000)]
        rom = bytearray(0x7000)
        self.sections = [{'name': 'main', 'rom': 0x100, 'text': 0x80000400, 'data': 0x80000800, 'slot': None}]
        for name, off, text, data, end, slot in secs:
            for i in range(0, data - text, 4):
                rom[off + i:off + i + 4] = (self.FILL[name] | i & 0xFFFF).to_bytes(4, 'big')
            self.sections.append({'name': name, 'rom': off, 'text': text, 'data': data, 'end': end, 'slot': slot})
        self.rom = bytes(rom)
        self.overlays = {'ovl_a': [[0x80100000, 0x80101000, -0x30, 'data']],
                         'ovl_e': [[0x80100000, 0x80103000, -0x10, 'data']],
                         'ovl_b': [[0x80102000, 0x80103000, -0x20, 'data']],
                         'ovl_c': [[0x80102000, 0x80103000, -0x40, 'data']]}

    def translate(self, names, text):
        m = {'main': [[0x80000400, 0x80080000, 0, 'data']], 'overlays': {n: self.overlays[n] for n in names}}
        layout = {'sections': [s for s in self.sections if s['slot'] is None or s['name'] in names]}
        guard = aki_conv.overlay_guard(layout, self.rom)
        out, done, bad = aki_conv.translate_codes(text, aki_conv.map_from_derelink(m), aki_conv.overlay_ranges(m), guard)
        return [l for l in out.splitlines() if l], done, bad

    def test_a_condition_in_one_overlay_and_a_write_in_another(self):
        # The Legends menu text: a condition on ovl_b's RAM, the write on ovl_a's string. ovl_a + ovl_b is one version
        # (behind "ovl_a is loaded"), ovl_e alone the other.
        lines, done, bad = self.translate(('ovl_a', 'ovl_e', 'ovl_b'), 'D0102100 0001\n81100104 1234\n')
        self.assertEqual((done, bad), (2, []))
        self.assertEqual(lines, ['D1100000 0C03', 'D01020E0 0001', '811000D4 1234',
                                 'D1100000 3C01', 'D01020F0 0001', '811000F4 1234'])

    def test_overlays_sharing_a_slot_that_disagree_get_their_own_guards(self):
        # ovl_b and ovl_c place the condition differently: each version says which of them is loaded.
        lines, done, bad = self.translate(('ovl_a', 'ovl_e', 'ovl_b', 'ovl_c'), 'D0102100 0001\n81100104 1234\n')
        self.assertEqual(bad, [])
        self.assertEqual(lines, ['D1102000 27BD', 'D01020E0 0001', '811000D4 1234',
                                 'D1102000 2402', 'D01020C0 0001', '811000D4 1234',
                                 'D1100000 3C01', 'D01020F0 0001', '811000F4 1234'])

    def test_single_overlay_units_keep_their_one_guard(self):
        lines, done, bad = self.translate(('ovl_a', 'ovl_e', 'ovl_b'), '81100104 1234\n')
        self.assertEqual(lines, ['D1100000 0C03', '811000D4 1234', 'D1100000 3C01', '811000F4 1234'])

    def test_coresident_sets(self):
        co = aki_conv.overlay_guard({'sections': self.sections}, self.rom).coresident
        self.assertEqual(aki_conv.coresident_sets(['ovl_a', 'ovl_b', 'ovl_c', 'ovl_e'], co),
                         [frozenset({'ovl_a', 'ovl_b'}), frozenset({'ovl_a', 'ovl_c'}), frozenset({'ovl_e'})])


class BundleParts(unittest.TestCase):
    def test_byte_orders(self):
        z = bytes([0x80, 0x37, 0x12, 0x40, 1, 2, 3, 4])
        self.assertEqual(aki_conv.to_z64(z), z)
        self.assertEqual(aki_conv.to_z64(bytes([0x37, 0x80, 0x40, 0x12, 2, 1, 4, 3])), z)
        self.assertEqual(aki_conv.to_z64(bytes([0x40, 0x12, 0x37, 0x80, 4, 3, 2, 1])), z)
        self.assertIsNone(aki_conv.to_z64(b'PK\x03\x04' + bytes(4)))

    def test_pak_images(self):
        pak = bytes(range(256)) * 128                       # 32 KB
        self.assertEqual(aki_conv.pak_image(pak), (pak, ''))
        big = pak + b'\xff' * (7 * 0x8000)                  # Project64's 256 KB file, seven fill blocks
        data, note = aki_conv.pak_image(big)
        self.assertEqual(data, pak)
        self.assertIn('256 KB', note)
        data, why = aki_conv.pak_image(pak + pak)           # a second real pak: not guessed at
        self.assertIsNone(data)
        self.assertIsNone(aki_conv.pak_image(b'x' * 1000)[0])

    def test_docs_skip_texture_and_emulator_folders(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            paths = ['readme.txt', 'Moves/Sting.txt', 'Plugin/hires/WWF/Sting/notes.txt', 'Plugin/hires/WWF/Sting/a.png',
                     'Lang/English.txt', 'Textures/Roster/list.txt', 'Textures/Roster/x.png',
                     'd3d syslog.txt', 'error log.txt', 'Changelog.txt']
            files = []
            for p in paths:
                full = os.path.join(d, *p.split('/'))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                open(full, 'w').close()
                files.append(full)
            kept, skipped = aki_conv.doc_files(files, d)
            self.assertEqual([os.path.relpath(p, d).replace(os.sep, '/') for p in kept],
                             ['Changelog.txt', 'readme.txt', 'Moves/Sting.txt'])
            self.assertEqual(skipped, 5)

    def test_a_stray_image_doesnt_make_the_root_a_texture_folder(self):
        root = os.path.join('C:' + os.sep, 'mod')
        tex = os.path.join(root, 'Plugin', 'hires_texture', 'Sting')
        files = [os.path.join(root, 'my settings.png'), os.path.join(root, 'Save', 'game.fla'),
                 os.path.join(tex, 'Codes + Memory Pak', 'pak.mpk')] + [os.path.join(tex, '%d.png' % i) for i in range(5)]
        dirs = aki_conv.texture_dirs(files)
        self.assertEqual(dirs, {tex})
        self.assertFalse(aki_conv.in_dirs(os.path.join(root, 'Save', 'game.fla'), dirs))
        self.assertTrue(aki_conv.in_dirs(os.path.join(tex, 'Codes + Memory Pak', 'pak.mpk'), dirs))
        self.assertFalse(aki_conv.in_dirs(os.path.join(root, 'Plugin', 'hires_texture', 'Sting2', 'x.mpk'), dirs))


def hts_file(path, records, old=True):
    """A GLideN64 .hts with RGBA8 records [(checksum, w, h, rgba, fmtsiz)] (synthetic test data)."""
    import struct
    body = b''
    index = []
    pos = 12 if old else 16
    for chk, w, h, rgba, fs in records:
        rec = struct.pack('<IIIHHB', w, h, 0x8058, 0x1908, 0x1401, 1)
        rec += (b'' if old else struct.pack('<H', fs)) + struct.pack('<I', len(rgba)) + rgba
        index.append(struct.pack('<Qq', chk, pos | (0 if old else fs << 48)))
        body += rec
        pos += len(rec)
    head = struct.pack('<iq', 0x40220000, pos) if old else struct.pack('<iiq', 0x08000000, 0x40220000, pos)
    with open(path, 'wb') as f:
        f.write(head + body + struct.pack('<i', len(index)) + b''.join(index))


class Hts(unittest.TestCase):
    def test_old_and_current_layouts_export_rice_names(self):
        import tempfile, zlib
        px = bytes([255, 0, 0, 255, 0, 255, 0, 128])  # 2x1
        with tempfile.TemporaryDirectory() as d:
            hts_file(os.path.join(d, 'old.hts'), [(0x0000000011111111, 2, 1, px, 0), (0x2222222233333333, 2, 1, px, 0)])
            got, skipped = aki_conv.hts_to_rice_pngs(os.path.join(d, 'old.hts'), os.path.join(d, 'o'), 'WWF No Mercy')
            self.assertEqual(skipped, 0)
            self.assertEqual(sorted(os.path.basename(p) for p in got),
                             ['WWF No Mercy#11111111#$#$_all.png', 'WWF No Mercy#33333333#$#$#22222222_all.png'])
            png = open(got[0], 'rb').read()
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(png[16:26], bytes([0, 0, 0, 2, 0, 0, 0, 1, 8, 6]))   # 2x1, 8-bit RGBA
            idat = png[png.index(b'IDAT') + 4:]
            self.assertEqual(zlib.decompressobj().decompress(idat), b'\0' + px)
            hts_file(os.path.join(d, 'new.hts'), [(0x0000000044444444, 2, 1, px, 0x0102)], old=False)
            got, _ = aki_conv.hts_to_rice_pngs(os.path.join(d, 'new.hts'), os.path.join(d, 'n'), 'G')
            self.assertEqual([os.path.basename(p) for p in got], ['G#44444444#2#1_all.png'])


class Pj64Selection(unittest.TestCase):
    def test_ticked_cheats_from_the_config(self):
        cfg = ('[Main Window]\nX=1\n[4E4B0640-1B49BCFB-C:45]\nCheat0=1\nCheat2=0\nCheat5=1\n[Other]\nCheat7=1\n'
               '[4E4B0640-1B49BCFB-C:45]\nCheat9=1\n')
        self.assertEqual(aki_conv.pj64_active_cheats(cfg, '4E4B0640-1B49BCFB-C:45'), {0, 5, 9})
        self.assertIsNone(aki_conv.pj64_active_cheats(cfg, 'DEADBEEF-00000000-C:45'))


class Package(unittest.TestCase):
    def test_thunderstore_shape(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'p.zip')
            aki_conv.write_package(path, {'name': 'TNA_vs_ROH', 'version_number': '1.1.0', 'website_url': '',
                                          'description': 'x', 'dependencies': []}, '# r', {'id': 'tna_vs_roh'},
                                   {'codes.txt': b'810500E8 0933\n'})
            with zipfile.ZipFile(path) as z:
                self.assertEqual(sorted(z.namelist()), ['README.md', 'aki.json', 'codes.txt', 'icon.png', 'manifest.json'])
                png = z.read('icon.png')
                self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(int.from_bytes(png[16:20], 'big'), 256)
        self.assertEqual(aki_conv.ts_name('TNA vs ROH'), 'TNA_vs_ROH')

    def test_links(self):
        self.assertEqual(aki_conv.parse_links(['Patcher page=https://example.org/p?a=b']),
                         [{'label': 'Patcher page', 'url': 'https://example.org/p?a=b'}])
        with self.assertRaises(SystemExit):
            aki_conv.parse_links(['no url here'])
        with self.assertRaises(SystemExit):
            aki_conv.parse_links(['Label=ftp://example.org'])

    def test_icon_override(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'p.zip')
            aki_conv.write_package(path, {'name': 'x', 'version_number': '1.0.0', 'website_url': '',
                                          'description': 'x', 'dependencies': []}, '# r', None,
                                   {'icon.png': b'author icon'})
            with zipfile.ZipFile(path) as z:
                self.assertEqual(z.read('icon.png'), b'author icon')


def ninja2(source, target, overflow_mode='A'):
    """A NINJA2 patch from source to target, written the way RomPatcher.js reads it (synthetic test data)."""
    import hashlib

    def vlv(v):
        b = v.to_bytes(max(1, (v.bit_length() + 7) // 8), 'little') if v else b''
        return bytes([len(b)]) + b

    out = bytearray(b'NINJA2'.ljust(0x800, b'\0'))
    out += b'\x01' + vlv(0) + b'\x00' + vlv(len(source)) + vlv(len(target))
    out += hashlib.md5(source).digest() + hashlib.md5(target).digest()
    if len(source) != len(target):
        extra = target[len(source):] if overflow_mode == 'A' else source[len(target):]
        out += overflow_mode.encode() + vlv(len(extra)) + extra
    base = source[:len(target)].ljust(len(target), b'\0')
    if overflow_mode == 'A' and len(target) > len(source):
        base = source + target[len(source):]
    i = 0
    while i < len(target):
        if base[i] == target[i]:
            i += 1
            continue
        j = i
        while j < len(target) and base[j] != target[j]:
            j += 1
        out += b'\x02' + vlv(i) + vlv(j - i) + bytes(x ^ y for x, y in zip(base[i:j], target[i:j]))
        i = j
    return bytes(out + b'\x00')


class Rup(unittest.TestCase):
    SOURCE = bytes(range(256)) * 4

    def test_apply_xor_records_and_appended_data(self):
        target = bytearray(self.SOURCE)
        target[10:14] = b'ABCD'
        target[900] = 0
        target = bytes(target) + b'appended data past the old end'
        patch = ninja2(self.SOURCE, target)
        self.assertEqual(aki_conv.apply_rup(self.SOURCE, patch), target)
        f = aki_conv.parse_rup(patch)[0]
        self.assertEqual(f['overflow_mode'], 'A')
        s = aki_conv.rup_summary(f)
        self.assertEqual(s['target_size'], len(target))
        self.assertEqual(s['base'], 'unknown')

    def test_wrong_rom_is_refused(self):
        patch = ninja2(self.SOURCE, self.SOURCE[:-1] + b'x')
        with self.assertRaises(ValueError):
            aki_conv.apply_rup(self.SOURCE[:-1] + b'y', patch)
        with self.assertRaises(ValueError):
            aki_conv.parse_rup(b'NINJA1' + bytes(0x800))

    def test_known_bases(self):
        self.assertEqual(aki_conv.KNOWN_ROM_MD5['04c492be7f89fc6f425238bd67629544'], 'nomercy-rev0')

    def test_watch_codes_join_the_pack_codes(self):
        # derelink's codes.txt (aki-roadmap#191) was left out of import-pj64 packs (aki-roadmap#207).
        import tempfile, types
        with tempfile.TemporaryDirectory() as d:
            a = types.SimpleNamespace(out=d, game='nomercy')
            open(os.path.join(d, 'codes.txt'), 'w').write('# watch\n[Conversion\\Hack code writes]\nD1000400 3C09\n810F295C 0000\n')
            aki = {'components': {}}
            pkg = {'codes.txt': b'[Mod\\A]\n8109940A FFFF\n'}
            self.assertEqual(aki_conv.add_watch_codes(a, aki, pkg), 2)
            self.assertEqual(pkg['codes.txt'].decode(), '[Mod\\A]\n8109940A FFFF\n\n# watch\n[Conversion\\Hack code writes]\nD1000400 3C09\n810F295C 0000\n')
            self.assertFalse(os.path.exists(os.path.join(d, 'codes.txt')))
            self.assertEqual(aki['components']['codes']['rom'], 'nomercy-rev1')
            self.assertEqual(aki_conv.add_watch_codes(a, aki, pkg), 0)

    def test_watch_codes_stay_out_of_codes_for_another_revision(self):
        # The watch list is at the port's (Rev 1) addresses; joined to untranslated Rev 0 codes it would be tagged Rev 0,
        # and translating the pack later would move it off the code it watches.
        import tempfile, types
        with tempfile.TemporaryDirectory() as d:
            a = types.SimpleNamespace(out=d, game='nomercy')
            watch = '[Conversion\\Hack code writes]\nD1000400 3C09\n'
            with open(os.path.join(d, 'codes.txt'), 'w') as f:
                f.write(watch)
            rev0 = '# rom: nomercy-rev0\n[Mod\\A]\n8109940A FFFF\n'
            aki = {'components': {'codes': {'file': 'codes.txt', 'format': 'aki-codes', 'rom': 'nomercy-rev0'}}}
            pkg = {'codes.txt': rev0.encode()}
            self.assertEqual(aki_conv.add_watch_codes(a, aki, pkg), 0)
            self.assertEqual(pkg['codes.txt'].decode(), rev0)
            self.assertEqual(aki['components']['codes']['rom'], 'nomercy-rev0')
            self.assertFalse(os.path.exists(os.path.join(d, 'codes.txt')))  # still never carried into the next build
            # import-pj64 passes its report part, which says why the watch codes are missing.
            with open(os.path.join(d, 'codes.txt'), 'w') as f:
                f.write(watch)
            summary = {}
            self.assertEqual(aki_conv.add_watch_codes(a, aki, pkg, summary), 0)
            self.assertIn('nomercy-rev0', summary['watch_codes_note'])
            # Codes already at the port's revision (translated with --map) take it.
            with open(os.path.join(d, 'codes.txt'), 'w') as f:
                f.write(watch)
            aki['components']['codes']['rom'] = 'nomercy-rev1'
            self.assertEqual(aki_conv.add_watch_codes(a, aki, pkg, {}), 1)
            self.assertEqual(pkg['codes.txt'].decode(), rev0 + '\n' + watch)

    def test_stock_rom_is_not_a_hack(self):
        # Showdown 64 ships the unpatched Rev 0 cartridge beside its .rup: the import took it as the hack (aki-roadmap#207).
        import hashlib
        rom = b'stock cartridge bytes'
        saved = dict(aki_conv.KNOWN_ROM_MD5)
        try:
            aki_conv.KNOWN_ROM_MD5[hashlib.md5(rom).hexdigest()] = 'nomercy-rev0'
            self.assertEqual(aki_conv.stock_rom(rom), 'nomercy-rev0')
            self.assertIsNone(aki_conv.stock_rom(rom + b'patched'))
        finally:
            aki_conv.KNOWN_ROM_MD5.clear()
            aki_conv.KNOWN_ROM_MD5.update(saved)

    def test_map_ranges(self):
        self.assertEqual(aki_conv.parse_map_ranges(['200800,400000,-1000', '10, 20 ,4']),
                         [(0x200800, 0x400000, -0x1000), (0x10, 0x20, 4)])
        with self.assertRaises(ValueError):
            aki_conv.parse_map_ranges(['200800,400000'])

    def test_values_follow_the_map_and_its_overrides(self):
        # aki-roadmap#228: import-pj64 runs translate-code-values after the addresses, with the same --map-range. A
        # hook into a cave whose addresses an override moved -0x1000 has to land on the cave where it now is.
        import hashlib, tempfile, types
        import aki_conv_values as v
        j = lambda t: 0x08000000 | ((t & 0x0FFFFFFF) >> 2)
        words = lambda addr, w: ['81%06X %04X' % (addr, w >> 16), '81%06X %04X' % (addr + 2, w & 0xFFFF)]
        port = bytes(0x300000)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'layouts'))
            with open(os.path.join(d, 'layouts', 'x.json'), 'w') as f:
                json.dump({'base_sha1': hashlib.sha1(port).hexdigest(), 'sections': [
                    {'name': 'main', 'rom': 0x1000, 'text': 0x80000400, 'data': 0x80030000}]}, f)
            open(os.path.join(d, 'derelink.py'), 'w').close()
            with open(os.path.join(d, 'port.z64'), 'wb') as f:
                f.write(port)
            with open(os.path.join(d, 'map.json'), 'w') as f:
                json.dump({'main': [[0x80000400, 0x80030000, -0x90, 'text'], [0x80200000, 0x80400000, 0, 'heap']],
                           'overlays': {}, 'identity': [[0x80400000, 0x80800000]]}, f)
            a = types.SimpleNamespace(map=os.path.join(d, 'map.json'), derelink=os.path.join(d, 'derelink.py'),
                                      port_rom=os.path.join(d, 'port.z64'))
            # Addresses already translated (hook 0x021C80 -> 0x021BF0, cave 0x300074 -> 0x2FF074); values not yet.
            text = '\n'.join(['# rom: nomercy-rev0', '[Mod\\Hook]'] + words(0x021BF0, j(0x80300074)) +
                             words(0x2FF074, j(0x80021C88))) + '\n'
            out, rep = aki_conv.translate_values_in(text, a, aki_conv.parse_map_ranges(['200800,400000,-1000']))
            self.assertIn(v.HEADER, out)
            self.assertIn('81021BF0 %04X' % (j(0x802FF074) >> 16), out)
            self.assertIn('81021BF2 %04X' % (j(0x802FF074) & 0xFFFF), out)    # the hook lands on the moved cave
            self.assertIn('812FF076 %04X' % (j(0x80021BF8) & 0xFFFF), out)    # and its way back follows main text
            self.assertGreaterEqual(rep['values_changed'], 2)
            self.assertEqual(aki_conv.translate_values_in(out, a)[1], {'off': 'already translated'})
            # Without a layout for the port's ROM (or a derelink map) the list comes back as it was, saying why.
            with open(a.port_rom, 'wb') as f:
                f.write(port + b'x')
            self.assertEqual(aki_conv.translate_values_in(text, a), (text, {'off': 'no derelink layout matches the port ROM'}))
            with open(a.map, 'w') as f:
                json.dump({'ranges': []}, f)
            self.assertEqual(aki_conv.translate_values_in(text, a)[1], {'off': 'the map is not a derelink map'})

    def test_cht_key_from_header(self):
        rom = bytearray(0x40)
        rom[0x10:0x18] = bytes.fromhex('4E4B06401B49BCFB')
        rom[0x3E] = 0x45
        self.assertEqual(aki_conv.cht_key(bytes(rom)), '4E4B0640-1B49BCFB-C:45')

    def test_jabo_names(self):
        self.assertTrue(aki_conv.JABO_NAME.match('0A7362AE0836847DBE924B8F2CB67647.png'))
        self.assertFalse(aki_conv.JABO_NAME.match('WWF No Mercy#0A7362AE#2#1_all.png'))
        self.assertFalse(aki_conv.JABO_NAME.match('0A7362AE0836847DBE924B8F2CB6764.png'))


if __name__ == '__main__':
    unittest.main()
