"""Tests for fixups/legends_menu_pointers.py (Legends menu pass). No ROM, no game."""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    'legends_menu_pointers', os.path.join(os.path.dirname(__file__), '..', 'fixups', 'legends_menu_pointers.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# import-pj64's translation of the section (addresses -0x1000 through the Rev 0 map override; values as written)
SECTION = """[WWF Legends\\Interface\\Main Menu\\Main Screen\\Navigation\\Selection Pointers]
# default: on
D00457A5 0002
812B65FE D3E0
D00457A5 0002
802B6669 0000
D00457A5 0002
802B666B 0004
D00457A5 0002
812B6680 8509
D00457A5 0002
812B6686 D464
D00457A5 0002
812B6708 8502
D00457A5 0002
812B670E D3F6
D00457A5 0002
802B6779 0000
D00457A5 0002
802B677B 0000
D00457A5 0002
812B6796 D422
D00457A5 0002
812B6818 850C
D00457A5 0002
812B681E D4A8
D00457A5 0002
812B68A0 850D
D00457A5 0002
812B68A6 D4BE
D00457A5 0002
802B6913 0001
"""
LIST = "# rom: nomercy-rev1\n\n[Other]\n812B65FE D3E0\n\n" + SECTION + "\n[After]\n812B68A6 D4BE\n"


def codes(text, section):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith('['):
            cur = line
        elif cur == section and line[:1].isalnum():
            out.append(line.split()[:2])
    return out


class MenuPointers(unittest.TestCase):
    def test_moves_the_six_pointer_halves_only(self):
        new, report = m.fix_pointers(LIST)
        self.assertEqual(report, {'pointer_writes': 6, 'addresses': 6})
        got = dict((a, v) for a, v in codes(new, '[' + m.SECTION + ']') if not a.startswith('D'))
        self.assertEqual(got['812B65FE'], 'BEE0')   # 0x8014D3E0 -> 0x8014BEE0, the descriptor both RAMs share
        self.assertEqual(got['812B6686'], 'BF64')
        self.assertEqual(got['812B670E'], 'BEF6')
        self.assertEqual(got['812B6796'], 'BF22')
        self.assertEqual(got['812B681E'], 'BFA8')
        self.assertEqual(got['812B68A6'], 'BFBE')
        # label ids and flags stay; so do the conditions
        self.assertEqual(got['812B6680'], '8509')
        self.assertEqual(got['802B666B'], '0004')
        self.assertEqual(got['802B6913'], '0001')
        self.assertEqual(sum(1 for a, _ in codes(new, '[' + m.SECTION + ']') if a == 'D00457A5'), 15)
        # the same addresses in other sections are not touched
        self.assertIn('[Other]\n812B65FE D3E0', new)
        self.assertIn('[After]\n812B68A6 D4BE', new)

    def test_guard_duplicates_all_move(self):
        # a guard step (legends_portrait_guards) may repeat a write under other conditions: every copy moves
        doubled = LIST.replace('812B65FE D3E0\nD00457A5 0002\n802B6669', '812B65FE D3E0\nD1106760 27BD\n812B65FE D3E0\nD00457A5 0002\n802B6669')
        new, report = m.fix_pointers(doubled)
        self.assertEqual(report['pointer_writes'], 7)
        self.assertEqual(new.count('812B65FE BEE0'), 2)

    def test_keeps_crlf(self):
        new, _ = m.fix_pointers(LIST.replace('\n', '\r\n'))
        self.assertNotIn('\n', new.replace('\r\n', ''))

    def test_refuses_a_left_out_section(self):
        with self.assertRaises(ValueError):
            m.fix_pointers("# rom: nomercy-rev1\n#   [" + m.SECTION + "]\n")

    def test_refuses_another_list(self):
        with self.assertRaises(ValueError):
            m.fix_pointers(LIST.replace('812B6796 D422', '812B6796 D423'))      # not the Rev 0 value
        with self.assertRaises(ValueError):
            m.fix_pointers(LIST.replace('D00457A5 0002\n812B681E D4A8\n', ''))  # a write missing
        with self.assertRaises(ValueError):
            m.fix_pointers(m.fix_pointers(LIST)[0])                            # run twice


if __name__ == '__main__':
    unittest.main()
