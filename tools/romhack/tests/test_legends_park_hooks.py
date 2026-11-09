"""Tests for tools/legends_park_hooks.py (aki-roadmap#187). No ROM, no game."""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    'legends_park_hooks', os.path.join(os.path.dirname(__file__), '..', 'fixups', 'legends_park_hooks.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

LIST = """# WWF Legends: test list
# rom: nomercy-rev1

[WWF Legends\\Arenas\\Ring Ropes\\Assembly]
D1021BEC 8042
81021BF0 0817
D1021BEC 8042
81021BF2 801D
815E0074 3C01

[WWF Legends\\Body Shapes\\Assembly & Pointers]
81018C38 0817
81018C3A A2BC
81018C3C 2400
815E8AF0 2410
81018C50 2400
81018C56 805F
815E8B18 805E

[WWF Legends\\Attires\\Extra Attires\\Assembly\\Part 1]
D100B524 00C2
8100B528 081C
D100B524 00C2
8100B52A 0013
D10D9968 3C01
D110A960 0213
8110A964 081C
D30D9968 3C01
D110A990 0213
8110A994 081C
D10D9968 3C01
D110A960 0213
8110A966 0000
D30D9968 3C01
D110A990 0213
8110A996 0000
D1015AE8 8503
81015AEA 0001
"""


class UnparkHooks(unittest.TestCase):
    def test_round_trip_adds_the_body_low_half_after_the_lui(self):
        parked, _ = p.park(LIST)
        new, report = p.unpark(parked)
        self.assertNotIn(p.PREFIX, new)
        self.assertNotIn(p.HEADER, new)
        self.assertIn('81018C56 805F\n81018C5E 8B18\n815E8B18 805E', new)
        self.assertEqual(new.replace('81018C5E 8B18\n', ''), LIST)
        self.assertIn('21 lines back on, 1 ', report)

    def test_low_half_already_there_is_not_doubled(self):
        parked, _ = p.park(LIST.replace('81018C56 805F\n', '81018C56 805F\n81018C5E 8B18\n'))
        new, report = p.unpark(parked)
        self.assertEqual(new.count('81018C5E 8B18'), 1)
        self.assertIn(', 0 ', report)

    def test_crlf_kept(self):
        parked, _ = p.park(LIST.replace('\n', '\r\n'))
        new, _ = p.unpark(parked)
        self.assertNotIn('\n', new.replace('\r\n', ''))

    def test_unparked_list_refused(self):
        with self.assertRaises(ValueError):
            p.unpark(LIST)


class ParkHooks(unittest.TestCase):
    def parked(self, text):
        return [l[len(p.PREFIX):] for l in text.split('\n') if l.startswith(p.PREFIX)]

    def test_hook_units_and_their_guards_go_rope_and_caves_stay(self):
        new, report = p.park(LIST)
        off = self.parked(new)
        self.assertEqual(len(off), 21)
        self.assertIn('21 lines', report)
        for kept in ('81021BF0 0817', '81021BF2 801D', '815E0074 3C01', '815E8AF0 2410', '815E8B18 805E',
                     'D1015AE8 8503', '81015AEA 0001'):
            self.assertIn('\n' + kept, new)
            self.assertNotIn(kept, off)
        self.assertEqual(off[:3], ['81018C38 0817', '81018C3A A2BC', '81018C3C 2400'])
        self.assertEqual(off[5:9], ['D100B524 00C2', '8100B528 081C', 'D100B524 00C2', '8100B52A 0013'])
        self.assertEqual(off[9:12], ['D10D9968 3C01', 'D110A960 0213', '8110A964 081C'])   # stacked guards go too
        self.assertEqual(new.split('\n')[2], p.HEADER)                                      # after the rom tag

    def test_twice_refused(self):
        new, _ = p.park(LIST)
        with self.assertRaises(ValueError):
            p.park(new)

    def test_crlf_kept(self):
        new, _ = p.park(LIST.replace('\n', '\r\n'))
        self.assertNotIn('\r\r', new)
        self.assertEqual(new.count('\n'), new.count('\r\n'))
        self.assertIn('\r\n' + p.PREFIX + '8100B528 081C\r\n', new)

    def test_unexpected_value_refused(self):
        with self.assertRaises(ValueError):
            p.park(LIST.replace('81018C3A A2BC', '81018C3A 9EBC'))   # the pre-#249 hook: not this list

    def test_missing_site_refused(self):
        with self.assertRaises(ValueError):
            p.park(LIST.replace('81018C56 805F\n', ''))

    def test_site_written_by_another_group_refused(self):
        other = LIST + '\n[WWF Legends\\Something Else]\n81018C50 2400\n'
        with self.assertRaises(ValueError):
            p.park(other)

    def test_guard_cut_off_by_a_skipped_line_refused(self):
        # the engine carries conditions across lines it skips: commenting the write alone would leave them dangling
        for between in ('[WWF Legends\\Attires\\Extra Attires\\X]', 'F1000000 0000', '88000000 0001', '81021000 1234 ; x',
                        '# a note'):
            text = LIST.replace('D100B524 00C2\n8100B528 081C', 'D100B524 00C2\n%s\n8100B528 081C' % between)
            with self.assertRaises(ValueError, msg=between):
                p.park(text)

    def test_blank_line_and_unrelated_skips_are_fine(self):
        text = LIST.replace('D1015AE8 8503\n', 'D1015AE8 8503\nF1000000 0000\n')   # a gap on a unit not parked
        new, _ = p.park(text.replace('81018C3C 2400\n', '81018C3C 2400\n\n'))
        self.assertEqual(len(self.parked(new)), 21)

    def test_skipped_kind_on_a_site_is_not_a_write(self):
        text = LIST + '\n[WWF Legends\\Body Shapes\\X]\n88018C38 0001\n'
        new, _ = p.park(text)
        self.assertIn('\n88018C38 0001', new)

    def test_eight_bit_write_and_odd_forms_near_a_site_refused(self):
        with self.assertRaises(ValueError):
            p.park(LIST + '\n[WWF Legends\\Body Shapes\\X]\n80018C39 0001\n')
        with self.assertRaises(ValueError):
            p.park(LIST + '\n[WWF Legends\\Body Shapes\\X]\n81018C38:0817\n')

    def test_header_first_without_a_rom_tag(self):
        new, _ = p.park(LIST.replace('# rom: nomercy-rev1\n', ''))
        self.assertTrue(new.startswith(p.HEADER + '\n'))

    def test_repeater_on_a_site_refused(self):
        rep = LIST + '\n[WWF Legends\\Body Shapes\\More]\n50000204 0000\n81018C34 2400\n'
        with self.assertRaises(ValueError):
            p.park(rep)


if __name__ == '__main__':
    unittest.main()
