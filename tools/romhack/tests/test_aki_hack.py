"""Tests for aki-hack.py's pure helpers (synthetic data only; no ROMs). Run: python tests/test_aki_hack.py"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('aki_hack', os.path.join(os.path.dirname(HERE), 'aki_hack.py'))
aki_hack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aki_hack)


class FakeStock:
    def available(self):
        return []


def be(words):
    return b''.join(w.to_bytes(4, 'big') for w in words)


class ByteOrder(unittest.TestCase):
    def test_v64_and_n64_become_z64(self):
        z64 = b'\x80\x37\x12\x40' + bytes(range(4, 16))
        v64 = bytearray(z64)
        v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
        n64 = b''.join(z64[i:i + 4][::-1] for i in range(0, len(z64), 4))
        self.assertEqual(aki_hack.to_z64(bytes(v64)), (z64, 'v64'))
        self.assertEqual(aki_hack.to_z64(n64), (z64, 'n64'))
        self.assertEqual(aki_hack.to_z64(z64), (z64, 'z64'))
        self.assertEqual(aki_hack.to_z64(b'PK\x03\x04'), (None, None))


class Immediates(unittest.TestCase):
    def test_moved_code_differs_only_in_immediates(self):
        self.assertTrue(aki_hack.immediate_only(0x0C001000, 0x0C001040))   # jal to a moved function
        self.assertTrue(aki_hack.immediate_only(0x3C048010, 0x3C048011))   # lui a0 of a moved address
        self.assertFalse(aki_hack.immediate_only(0x3C048010, 0x3C058010))  # lui into another register
        self.assertFalse(aki_hack.immediate_only(0x00851021, 0x00851023))  # addu -> subu (R-type change)

    def test_masked_words_hide_only_immediates(self):
        a = be([0x0C001000, 0x3C048010, 0x00851021])
        b = be([0x0C001040, 0x3C048011, 0x00851021])
        c = be([0x0C001040, 0x3C048011, 0x00851023])
        self.assertEqual(aki_hack.masked_words(a), aki_hack.masked_words(b))
        self.assertNotEqual(aki_hack.masked_words(a), aki_hack.masked_words(c))

    def test_relink_probe_sees_a_constant_shift(self):
        funcs = [be([0x27BDFFE8 + i, 0xAFBF0014, 0x0C000000 + i, 0x00851021 + (i << 11)] * 4) for i in range(8)]
        base = b''.join(funcs)
        hack = b'\0' * 0x70 + b''.join(f[:8] + (int.from_bytes(f[8:12], 'big') + 4).to_bytes(4, 'big') + f[12:]
                                        for f in funcs)
        sections = [('main', 0, 0x80000400, len(base), [('f%d' % i, i * 64, 64) for i in range(8)])]
        p = aki_hack.relink_probe(base, hack, sections, sig_words=12)
        self.assertEqual((p['same'], p['moved'], p['missing']), (0, 8, 0))
        self.assertEqual(p['top_shifts'][0], '+0x70 x8')


class Codes(unittest.TestCase):
    def test_types_and_unknown_revision(self):
        text = b'# roster\n810BD910 FFFF\nD0012345 0001\n80012346 0002\n50000402 0000\nnot a code\n'
        r = aki_hack.survey_codes('unlock.txt', text, FakeStock())
        self.assertEqual(r['codes'], 4)
        self.assertEqual(r['types']['16-bit write'], 1)
        self.assertEqual(r['types']['if 8-bit equal'], 1)
        self.assertEqual(r['types']['repeat (serial)'], 1)
        self.assertTrue(r['rom_revision'].startswith('unknown'))

    def test_not_a_code_list(self):
        self.assertIsNone(aki_hack.survey_codes('readme.txt', b'hello\nworld\n', FakeStock()))


class Saves(unittest.TestCase):
    def flash(self, sig_order):
        img = bytearray(131072)
        sig = aki_hack.byte_orders(b'WWF2 AKI')[sig_order]
        img[0x88:0x88 + len(sig)] = sig
        return bytes(img)

    def test_no_mercy_flash_byte_orders(self):
        for order in ('raw (big-endian)', 'word-swapped (Project64)', 'halfword-swapped'):
            r = aki_hack.survey_save('nm.fla', self.flash(order))
            self.assertEqual((r['save_kind'], r['game'], r['byte_order']), ('flash', 'nomercy', order))

    def test_word_swap_of_signature(self):
        self.assertEqual(aki_hack.byte_orders(b'WWF2 AKI')['word-swapped (Project64)'], b'2FWWIKA ')


class Textures(unittest.TestCase):
    def test_naming(self):
        self.assertEqual(aki_hack.survey_textures('p', ['rt64.json', '0123456789abcdef.png'])['naming'], 'rt64')
        self.assertEqual(aki_hack.survey_textures('p', ['WWF NO MERCY#1A2B3C4D#0#2_all.png'])['naming'], 'rice/gliden64')
        self.assertEqual(aki_hack.survey_textures('p', ['0123456789abcdef0123456789abcdef.png'])['naming'], 'jabo')
        self.assertIsNone(aki_hack.survey_textures('p', ['readme.txt']))


if __name__ == '__main__':
    unittest.main()
