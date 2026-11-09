"""Tests for tools/legends_fb_relocate.py (aki-roadmap#249). No ROM, no game."""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    'legends_fb_relocate', os.path.join(os.path.dirname(__file__), '..', 'fixups', 'legends_fb_relocate.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

LIST = """# rom: nomercy-rev1

[Body Shapes\\Assembly & Pointers]
D30D9968 3C01
81018C38 080F
81018C3A 9EBC
81018C56 803F
813E7AF0 2410
813E7AF2 0004
813E7B18 803E
813E7B1A 7BA0

[Arenas\\Ring Ropes\\Assembly]
81021BF0 080F
81021BF2 7C1D
813DF074 3C01
813DF076 8005

[Attires\\Extra Attires\\Assembly\\Part 2]
81700000 3C02
81700002 803C
81700084 3C0A
81700086 8039

[Attires\\Extra Attires\\Some Wrestler]
8138F000 0102
803BF10A 0007
81504410 0C0F
81504412 0000

[Elsewhere]
8138EFFE 1111
81400000 2222
"""


def lines(text, group):
    return [l for l in text.split(group)[1].split('[')[0].splitlines() if l]


class Relocate(unittest.TestCase):
    def setUp(self):
        self.new, self.report = r.relocate(LIST)

    def test_moves_writes_by_0x201000(self):
        self.assertIn('815E8AF0 2410', self.new)
        self.assertIn('815E0074 3C01', self.new)
        self.assertIn('81590000 0102', self.new)       # the 2 KB below the framebuffer move too
        self.assertIn('805C010A 0007', self.new)       # 8-bit writes as well
        self.assertNotIn('813E7AF0', self.new)

    def test_hooks_follow_their_caves(self):
        body = lines(self.new, '[Body Shapes\\Assembly & Pointers]')
        self.assertIn('81018C38 0817', body)
        self.assertIn('81018C3A A2BC', body)            # j 0x805E8AF0
        ropes = lines(self.new, '[Arenas\\Ring Ropes\\Assembly]')
        self.assertIn('81021BF0 0817', ropes)
        self.assertIn('81021BF2 801D', ropes)           # j 0x805E0074

    def test_table_bases_point_at_the_moved_tables(self):
        cave = lines(self.new, '[Attires\\Extra Attires\\Assembly\\Part 2]')
        self.assertEqual(cave, ['81700000 3C02', '81700002 805C', '81700084 3C0A', '81700086 8059'])

    def test_lone_lui_immediate_in_game_code(self):
        body = lines(self.new, '[Body Shapes\\Assembly & Pointers]')
        self.assertIn('81018C56 805F', body)            # lui a2,0x803F (Rev 0) -> 0x805F

    def test_refuses_a_lone_lui_with_its_opcode_written(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST.replace('81018C56 803F', '81018C54 3C06\n81018C56 803F'))

    def test_refuses_an_unknown_lone_immediate_in_game_code(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST + '[Other]\n81030002 803E\n')

    def test_moveset_data_that_looks_like_a_lui_is_left(self):
        new, _ = r.relocate(LIST + '[Moveset]\n813236CA 8040\n')
        self.assertIn('813236CA 8040', new)

    def test_refuses_an_edited_word_written_twice(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST + '[Copy]\nD10D9968 0C03\n81021BF2 7C1D\n')

    def test_refuses_the_top_of_the_destination(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST.replace('[Elsewhere]\n', '[Elsewhere]\n81600F00 0001\n'))

    def test_refuses_a_repeater_into_the_range(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST.replace('[Elsewhere]\n', '[Elsewhere]\n50000402 0000\n813E0000 0001\n'))

    def test_pointer_words_inside_the_blocks(self):
        body = lines(self.new, '[Body Shapes\\Assembly & Pointers]')
        self.assertIn('815E8B18 805E', body)
        self.assertIn('815E8B1A 8BA0', body)            # 0x803E7BA0 + 0x201000

    def test_leaves_everything_else(self):
        self.assertIn('D30D9968 3C01', self.new)
        self.assertIn('81504410 0C0F', self.new)        # data that looks like a jal into the range
        self.assertIn('8138EFFE 1111', self.new)
        self.assertIn('81400000 2222', self.new)
        self.assertEqual(self.report, {'writes_moved': 8, 'hooks': 2, 'table_bases': 3, 'pointers': 1})

    def test_header_and_refuses_twice(self):
        self.assertIn(r.HEADER, self.new.splitlines()[1])
        with self.assertRaises(ValueError):
            r.relocate(self.new)

    def test_keeps_crlf(self):
        new, _ = r.relocate(LIST.replace('\n', '\r\n'))
        self.assertNotIn('\n', new.replace('\r\n', ''))

    def test_refuses_an_unexpected_hook(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST.replace('81018C3A 9EBC', '81018C3A 9EC0'))

    def test_refuses_a_condition_on_the_range(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST.replace('[Elsewhere]\n', '[Elsewhere]\nD13E7AF0 2410\n'))

    def test_refuses_a_busy_destination(self):
        with self.assertRaises(ValueError):
            r.relocate(LIST.replace('[Elsewhere]\n', '[Elsewhere]\n81590000 0001\n'))

    def test_refuses_an_unknown_jump_into_the_range(self):
        extra = '[Other hook]\n81030000 080F\n81030002 7C1D\n'
        with self.assertRaises(ValueError):
            r.relocate(LIST + extra)


if __name__ == '__main__':
    unittest.main()
