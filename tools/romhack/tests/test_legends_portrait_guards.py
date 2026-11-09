"""Tests for tools/legends_portrait_guards.py (aki-roadmap#198). No ROM, no game."""
import importlib.util
import os
import struct
import unittest

spec = importlib.util.spec_from_file_location(
    'legends_portrait_guards', os.path.join(os.path.dirname(__file__), '..', 'fixups', 'legends_portrait_guards.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

LIST = """# rom: nomercy-rev1

[Portraits\\Position\\1B]
D104FF82 00FF
812BDF84 42CF

[Portraits\\Exhibition\\Stable 01]
D104FF82 00FF
D00A9585 0001
D02BD8A3 0000
802F0161 00FF
D104FF82 00FF
D20A9585 0001
802F0991 00FF

[Menu state]
D104FF82 00FF
8104FF86 00FF

[Menu screen]
D00A9578 0000
D00457A5 0006
812B7434 429A

[In a match]
D00457A5 000A
8131A988 1234

[Screen, main RAM]
D00457A5 0006
8104FF86 00FF

[Other guard]
D00A9578 0000
812B9EB8 0606
"""


def units(text, group):
    body = text.split(group)[1].split('[')[0]
    return [l for l in body.splitlines() if l]


class Guards(unittest.TestCase):
    def setUp(self):
        self.new, self.report = g.add_guards(LIST)

    def test_table_write_checks_its_own_record(self):
        # 2BDF84 is in the second record of table 1B (2BDF80), so the signature is at 2BDFA8.
        self.assertEqual(units(self.new, '[Portraits\\Position\\1B]'),
                         ['D104FF82 00FF', 'D115982C 802B', 'D115982E B4C0', 'D12BDFA8 8014', 'D12BDFAA BCE4', '812BDF84 42CF'])

    def test_texture_write_checks_the_record_its_guards_read(self):
        u = units(self.new, '[Portraits\\Exhibition\\Stable 01]')
        # D02BD8A3 reads the second record of table 1A (2BD898): signature at 2BD8C0.
        self.assertEqual(u[:8], ['D104FF82 00FF', 'D115982C 802B', 'D115982E B4C0', 'D12BD8C0 8014', 'D12BD8C2 BCE4', 'D00A9585 0001', 'D02BD8A3 0000', '802F0161 00FF'])

    def test_sibling_without_a_record_guard_uses_the_groups_record(self):
        u = units(self.new, '[Portraits\\Exhibition\\Stable 01]')
        self.assertEqual(u[8:], ['D104FF82 00FF', 'D115982C 802B', 'D115982E B4C0', 'D12BD8C0 8014', 'D12BD8C2 BCE4', 'D20A9585 0001', '802F0991 00FF'])

    def test_main_ram_flag_users_are_left_alone(self):
        self.assertEqual(units(self.new, '[Menu state]'), ['D104FF82 00FF', '8104FF86 00FF'])

    def test_menu_screen_heap_write_checks_the_layout(self):
        self.assertEqual(units(self.new, '[Menu screen]'),
                         ['D00A9578 0000', 'D00457A5 0006', 'D115982C 802B', 'D115982E B4C0', '812B7434 429A'])

    def test_in_match_screens_main_ram_and_other_guards_are_left_alone(self):
        self.assertEqual(units(self.new, '[In a match]'), ['D00457A5 000A', '8131A988 1234'])
        self.assertEqual(units(self.new, '[Screen, main RAM]'), ['D00457A5 0006', '8104FF86 00FF'])
        self.assertEqual(units(self.new, '[Other guard]'), ['D00A9578 0000', '812B9EB8 0606'])

    def test_report_and_header(self):
        self.assertEqual(self.report, {'flag_writes_guarded': 3, 'screen_writes_guarded': 1, 'groups': 3})
        self.assertIn(g.HEADER, self.new)

    def test_refuses_a_list_that_has_them(self):
        with self.assertRaises(ValueError):
            g.add_guards(self.new)

    def test_crlf_is_kept(self):
        new, _ = g.add_guards(LIST.replace('\n', '\r\n'))
        self.assertNotIn('\n\n\n', new.replace('\r\n', '\n').replace('\n\n', '\n', 0)[:0])
        self.assertEqual(new.count('\n'), new.count('\r\n'))

    def test_signature_count_in_a_snapshot(self):
        ram = bytearray(0x400000)
        for i in range(g.RECORDS):
            struct.pack_into('<I', ram, g.TABLES['1B'] + i * g.STRIDE + g.SIG_OFFSET, 0x8014BCE4)
        self.assertEqual(g.signatures_present(bytes(ram)), {'layout': False, '1A': 0, '1B': g.RECORDS})


if __name__ == '__main__':
    unittest.main()
