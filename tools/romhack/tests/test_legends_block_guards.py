"""Tests for tools/legends_block_guards.py (aki-roadmap#221). No ROM, no game."""
import importlib.util
import os
import struct
import unittest

spec = importlib.util.spec_from_file_location(
    'legends_block_guards', os.path.join(os.path.dirname(__file__), '..', 'fixups', 'legends_block_guards.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

LIST = """# rom: nomercy-rev1

[Movesets\\Ham & Eggers]
81322FA8 0041
81322FAA D038

[Attires\\Extra\\Part 1]
D10A95A0 0607
803194D0 0055

[Header between the blocks]
8131F8C4 1234

[Outside the range]
81323FB0 1111
8131925E 2222
8104FF86 00FF

[Repeater elsewhere]
50000402 0000
81100000 0001
"""


def body(text, group):
    return [l for l in text.split(group)[1].split('[')[0].splitlines() if l]


class Guards(unittest.TestCase):
    def setUp(self):
        self.new, self.report = g.add_guards(LIST)

    def test_moveset_write_gets_menu_and_match_copies(self):
        self.assertEqual(body(self.new, '[Movesets\\Ham & Eggers]')[:8], [
            'D1047E94 8031', 'D1047E96 F8D0', '81322FA8 0041',
            'D1047E94 0000', 'D1047E96 0000', 'D11593E8 802F', 'D11593EA 8090', '81322FA8 0041'])

    def test_attire_write_checks_attire_pointer_and_keeps_its_own_guard(self):
        self.assertEqual(body(self.new, '[Attires\\Extra\\Part 1]'), [
            'D1047E90 8031', 'D1047E92 9260', 'D10A95A0 0607', '803194D0 0055',
            'D1047E90 0000', 'D1047E92 0000', 'D11593E8 802F', 'D11593EA 8090', 'D10A95A0 0607', '803194D0 0055'])

    def test_header_gap_counts_as_moveset_block(self):
        self.assertEqual(body(self.new, '[Header between the blocks]')[0], 'D1047E94 8031')

    def test_outside_range_untouched(self):
        self.assertEqual(body(self.new, '[Outside the range]'), ['81323FB0 1111', '8131925E 2222', '8104FF86 00FF'])
        self.assertEqual(body(self.new, '[Repeater elsewhere]'), ['50000402 0000', '81100000 0001'])

    def test_report_and_header(self):
        self.assertEqual(self.report, {'writes_guarded': 4, 'groups': 3})
        self.assertIn(g.HEADER, self.new.splitlines()[1])

    def test_twice_refused(self):
        with self.assertRaises(ValueError):
            g.add_guards(self.new)

    def test_repeater_into_range_refused(self):
        with self.assertRaises(ValueError):
            g.add_guards('[x]\n50000402 0000\n81322FA8 0001\n')

    def test_conditions_across_a_skipped_line_refused(self):
        # The engine keeps a condition across blank lines, comments and codes it skips (88, F1): the second copy
        # would lose it, so the tool refuses instead.
        for gap in ('', '# note', '88000000 0001', 'F1000000 0000'):
            with self.assertRaises(ValueError, msg=repr(gap)):
                g.add_guards('[x]\nD10A95A0 0607\n%s\n81322FA8 0041\n' % gap)

    def test_condition_gap_before_a_write_elsewhere_is_fine(self):
        new, report = g.add_guards('[x]\nD10A95A0 0607\n\n81100000 0001\n81322FA8 0041\n')
        self.assertEqual(report['writes_guarded'], 1)

    def test_other_write_forms_into_range_refused(self):
        for line in ('81322FA8 0041 # note', '81322FA8:0041', '81322FA8-0041'):
            with self.assertRaises(ValueError, msg=line):
                g.add_guards('[x]\n%s\n' % line)

    def test_crlf_kept(self):
        new, _ = g.add_guards(LIST.replace('\n', '\r\n'))
        self.assertNotIn('\n', new.replace('\r\n', ''))

    def test_layout_from_snapshot(self):
        ram = bytearray(0x800000)
        put = lambda a, v: struct.pack_into('<I', ram, a, v)
        put(0x47E90, 0x80319260); put(0x47E94, 0x8031F8D0)
        self.assertEqual(g.layout(bytes(ram)), 'menus')
        put(0x47E90, 0); put(0x47E94, 0); put(0x1593E8, 0x802F8090)
        self.assertEqual(g.layout(bytes(ram)), 'match')
        put(0x47E90, 0x80189630); put(0x47E94, 0x8018FCA0)
        self.assertEqual(g.layout(bytes(ram)), 'moved')


if __name__ == '__main__':
    unittest.main()
