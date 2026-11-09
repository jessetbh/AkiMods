"""Tests for translating addresses inside the code a GameShark list writes (aki-roadmap#205). Synthetic ROM and layout.
Run: python tests/test_aki_conv_values.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import aki_conv_values as v  # noqa: E402

# Main text 0x80000400..0x80030000 moved by -0x90, main data to 0x80060000 by -0xA0; the slot at 0x80100000 holds
# overlay X (moved -0x100) or Y (moved -0x200). Caves in 0x80300000 and up don't move.
RANGES = [(0x000400, 0x030000, -0x90), (0x030000, 0x060000, -0xA0), (0x200000, 0x800000, 0)]
OVERLAYS = {'x': [(0x100000, 0x110000, -0x100)], 'y': [(0x100000, 0x110000, -0x200)]}
MAPPER = v.Mapper(RANGES, OVERLAYS)
SECTIONS = [{'name': 'main', 'rom': 0x1000, 'text': 0x80000400, 'data': 0x80030000},
            {'name': 'x', 'rom': 0x100000, 'text': 0x80100000, 'data': 0x80110000, 'slot': 0x80100000},
            {'name': 'y', 'rom': 0x200000, 'text': 0x80100000, 'data': 0x80110000, 'slot': 0x80100000}]


def rom(words=()):
    b = bytearray(0x300000)
    for off, w in words:
        b[off:off + 4] = w.to_bytes(4, 'big')
    return bytes(b)


LAYOUT = v.Layout({'sections': SECTIONS}, rom())


def code(addr, *words):
    out = []
    for i, w in enumerate(words):
        out += ['81%06X %04X' % (addr + i * 4, w >> 16), '81%06X %04X' % (addr + i * 4 + 2, w & 0xFFFF)]
    return out


def j(t): return 0x08000000 | ((t & 0x0FFFFFFF) >> 2)
def jal(t): return 0x0C000000 | ((t & 0x0FFFFFFF) >> 2)
def lui(rt, hi): return 0x3C000000 | rt << 16 | hi
def lbu(rt, base, lo): return 0x90000000 | base << 21 | rt << 16 | lo
def addiu(rt, rs, lo): return 0x24000000 | rs << 21 | rt << 16 | lo
def ori(rt, rs, lo): return 0x34000000 | rs << 21 | rt << 16 | lo
def addu(rd, rs, rt): return rs << 21 | rt << 16 | rd << 11 | 0x21


JR_RA = 0x03E00008
AT, V0, V1, A0 = 1, 2, 3, 4


def values(text):
    w = {}
    for line in text.splitlines():
        m = v.LINE.match(line.strip())
        if m and m[1] in ('81', 'A1'):
            w[0x80000000 | int(m[2], 16)] = int(m[3], 16)
    return w


def word(w, a):
    return w[a] << 16 | w[a + 2]


def run(lines, layout=LAYOUT):
    return v.translate_values('\n'.join(['[g]'] + lines), MAPPER, layout)


class Hooks(unittest.TestCase):
    def test_ring_ropes_shape(self):
        # The bug: the hook lands on the cave, whose way back and table read still held the old revision's addresses.
        lines = code(0x021BF0, j(0x80300074)) + code(0x300074, lui(AT, 0x8005), addu(AT, V1, AT), lbu(V1, AT, 0x2900),
                                                     j(0x80021C88), 0)
        text, rep = run(lines)
        w = values(text)
        self.assertEqual(word(w, 0x80021BF0), j(0x80300074))        # caves don't move
        self.assertEqual(word(w, 0x80300080), j(0x80021BF8))        # the way back moved with main text
        self.assertEqual(word(w, 0x80300074), lui(AT, 0x8005))
        self.assertEqual(word(w, 0x8030007C), lbu(V1, AT, 0x2860))  # the table moved with main data, through the addu
        self.assertEqual(rep['hooks_into_caves'], 1)
        self.assertEqual(rep['hilo_pairs'], 1)
        self.assertEqual(rep['unmapped'], [])

    def test_a_cave_nothing_jumps_to_stays_as_it_is(self):
        # Most written words are data, and plenty look like jumps or lui.
        lines = code(0x300000, j(0x80021C88), lui(AT, 0x8005), lbu(V1, AT, 0x2900))
        text, rep = run(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(rep['values_changed'], 0)
        self.assertEqual(rep['unreached_jumps'], 1)

    def test_a_cave_across_groups(self):
        lines = code(0x001000, jal(0x80300000)) + ['[part 2]'] + code(0x300000, jal(0x80010000), 0, JR_RA, 0)
        w = values(run(lines)[0])
        self.assertEqual(word(w, 0x80300000), jal(0x8000FF70))

    def test_every_group_writing_the_same_cave_word_changes(self):
        lines = code(0x001000, j(0x80300000)) + code(0x300000, j(0x80010000), 0) + ['[again]'] + code(0x300000, j(0x80010000))
        text, _ = run(lines)
        self.assertEqual(text.count('81300002 %04X' % (j(0x8000FF70) & 0xFFFF)), 2)

    def test_following_branches_and_stopping_at_the_end(self):
        lines = code(0x001000, j(0x80300000)) + code(0x300000, JR_RA, 0, j(0x80010000))
        w = values(run(lines)[0])
        self.assertEqual(word(w, 0x80300008), j(0x80010000))       # past the jr's slot: not code
        lines = code(0x001000, j(0x80300000)) + code(0x300000, 0x10000001, 0, j(0x80010000), 0)
        w = values(run(lines)[0])
        self.assertEqual(word(w, 0x80300008), j(0x8000FF70))       # the branch's target


class GameInstructions(unittest.TestCase):
    def test_a_lui_load_pair_written_on_game_code(self):
        w = values(run(code(0x002000, lui(V1, 0x8005), lbu(V1, V1, 0x0050)))[0])
        self.assertEqual(word(w, 0x80002000), lui(V1, 0x8005))     # 0x80050050 -> 0x8004FFB0: lui carries the negative low
        self.assertEqual(word(w, 0x80002004), lbu(V1, V1, 0xFFB0))

    def test_ori_has_no_carry(self):
        w = values(run(code(0x002000, lui(V1, 0x8005), ori(V1, V1, 0x0050)))[0])
        self.assertEqual(word(w, 0x80002000), lui(V1, 0x8004))
        self.assertEqual(word(w, 0x80002004), ori(V1, V1, 0xFFB0))

    def test_a_lui_whose_uses_need_different_upper_halves_is_left_and_reported(self):
        # 0x80030050 -> 0x8002FFB0 keeps lui 0x8003 (the low half is negative); ori 0x0020 -> 0x8002FF80 needs lui 0x8002.
        lines = code(0x002000, lui(AT, 0x8003), lbu(V0, AT, 0x0050), ori(V1, AT, 0x0020))
        text, rep = run(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(len(rep['lui_conflicts']), 1)

    def test_a_number_that_is_not_an_address_stays(self):
        lines = code(0x002000, lui(A0, 0x3F80), addiu(A0, A0, 0x10))
        text, rep = run(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(rep['unmapped'], [])

    def test_two_runs_in_one_group_both_translate(self):
        # The first version dropped everything but the last run of a group.
        lines = code(0x002000, jal(0x80010000)) + code(0x003000, lui(V1, 0x8005), lbu(V1, V1, 0x0100))
        w = values(run(lines)[0])
        self.assertEqual(word(w, 0x80003004), lbu(V1, V1, 0x0060))
        lines = code(0x002000, lui(V1, 0x8005), lbu(V1, V1, 0x0100)) + code(0x003000, jal(0x80010000))
        w = values(run(lines)[0])
        self.assertEqual(word(w, 0x80002004), lbu(V1, V1, 0x0060))

    def test_a_jump_that_goes_nowhere_known_stays_and_is_reported(self):
        lines = code(0x002000, j(0x80090000))    # a word that only looks like a jump
        text, rep = run(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(len(rep['unmapped']), 1)


class LowHalves(unittest.TestCase):
    def test_the_low_half_of_a_game_jump(self):
        layout = v.Layout({'sections': SECTIONS}, rom([(0x1000 + 0x1C00, jal(0x80005000))]))
        text, rep = run(['81002002 %04X' % (jal(0x80010000) & 0xFFFF)], layout)
        self.assertEqual(values(text)[0x80002002], jal(0x8000FF70) & 0xFFFF)
        self.assertEqual(rep['low_halves'], 1)

    def test_a_low_half_on_a_load_is_counted_not_touched(self):
        layout = v.Layout({'sections': SECTIONS}, rom([(0x1000 + 0x1C00, lbu(V1, V1, 0))]))
        text, rep = run(['81002002 0050'], layout)
        self.assertEqual(values(text)[0x80002002], 0x0050)
        self.assertEqual(rep['half_immediates'], 1)

    def test_overlays_with_different_targets_are_reported(self):
        layout = v.Layout({'sections': SECTIONS}, rom([(0x100000, jal(0x80100000)), (0x200000, jal(0x80100000))]))
        text, rep = run(['81100002 0400'], layout)
        self.assertEqual(values(text)[0x80100002], 0x0400)
        self.assertEqual(len(rep['unmapped']), 1)


    def test_a_low_half_where_only_some_overlays_have_a_jump_is_left(self):
        # Overlay X has a call here and Y a load: with no guard saying which, the value can't be read as a target.
        layout = v.Layout({'sections': SECTIONS}, rom([(0x100000, jal(0x80100000)), (0x200000, lbu(V1, V1, 0))]))
        text, rep = run(['81100002 0400'], layout)
        self.assertEqual(values(text)[0x80100002], 0x0400)
        self.assertEqual(rep['low_halves'], 0)
        self.assertEqual(rep['half_immediates'], 1)


    def test_a_low_half_that_lands_outside_code_is_not_a_jump(self):
        # Overlay data sits at the same address as another overlay's jump: a string pointer's low half, say.
        layout = v.Layout({'sections': SECTIONS}, rom([(0x1000 + 0x1C00, jal(0x80005000))]))
        text, rep = run(['81002002 %04X' % (jal(0x80040000) & 0xFFFF)], layout)
        self.assertEqual(values(text)[0x80002002], jal(0x80040000) & 0xFFFF)
        self.assertEqual(rep['low_halves'], 0)
        self.assertEqual(len(rep['unmapped']), 1)

    def test_a_low_half_into_a_written_cave_counts(self):
        layout = v.Layout({'sections': SECTIONS}, rom([(0x1000 + 0x1C00, jal(0x80300000))]))
        text, rep = run(['81002002 %04X' % (jal(0x80300100) & 0xFFFF)] + code(0x300100, JR_RA, 0), layout)
        self.assertEqual(rep['low_halves'], 1)
        self.assertEqual(rep['hooks_into_caves'], 1)



class OverlayContext(unittest.TestCase):
    def test_a_guard_on_overlay_text_picks_the_overlay(self):
        # The slot moved differently per overlay, so only the guard can place this jump.
        layout = v.Layout({'sections': SECTIONS}, rom([(0x100000, 0x3C010000), (0x200000, 0x0C030000)]))
        word_lines = code(0x100010, j(0x80100400))
        lines = ['D1100000 3C01', word_lines[0], 'D1100000 3C01', word_lines[1],
                 'D1100000 0C03', word_lines[0], 'D1100000 0C03', word_lines[1]]
        text, rep = run(lines, layout)
        got = [m for m in text.splitlines() if m.startswith('81100012')]
        self.assertEqual(got, ['81100012 %04X' % (j(0x80100300) & 0xFFFF), '81100012 %04X' % (j(0x80100200) & 0xFFFF)])
        self.assertEqual(rep['unmapped'], [])

    def test_no_guard_and_overlays_disagree_stays_and_is_reported(self):
        lines = code(0x100010, j(0x80100400))
        text, rep = run(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(len(rep['unmapped']), 1)


class Text(unittest.TestCase):
    def test_lines_keep_their_comments_and_everything_else_stays(self):
        text, _ = v.translate_values('# head\n[g]\n# note\n81002000 0800 # x\n81002002 4000 // y\n\n', MAPPER, LAYOUT)
        self.assertEqual(text, '# head\n[g]\n# note\n81002000 0800 # x\n81002002 3FDC // y\n\n')

    def test_an_address_the_map_leaves_alone_needs_no_change(self):
        lines = code(0x001000, j(0x80300000)) + code(0x300000, lui(AT, 0x8040), lbu(V1, AT, 0x10), JR_RA, 0)
        text, rep = run(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(rep['hilo_pairs'], 1)


# As above, but caves moved -0x1000: a list translated with an older map (Legends).
MOVED = v.Mapper([(0x200000, 0x400000, -0x1000)] + RANGES, OVERLAYS)


class MovedCaves(unittest.TestCase):
    def run_moved(self, lines):
        return v.translate_values('\n'.join(['[g]'] + lines), MOVED, LAYOUT)

    def test_the_hook_and_a_table_read_follow_the_cave(self):
        lines = code(0x021BF0, j(0x803E0074)) + code(0x3DF074, lui(V0, 0x803C), lbu(V1, V0, 0), JR_RA, 0)
        w = values(self.run_moved(lines)[0])
        self.assertEqual(word(w, 0x80021BF0), j(0x803DF074))
        self.assertEqual(word(w, 0x803DF074), lui(V0, 0x803C))       # 0x803BF000: the low half is negative
        self.assertEqual(word(w, 0x803DF078), lbu(V1, V0, 0xF000))

    def test_a_stepped_base_whose_target_moved_is_left_and_reported(self):
        # Legends' attire lookup: lui t2, 0x8039; lw t1, 0(t2); addiu t2, t2, 8; lw t1, 0(t2). Putting the move in the
        # first load's offset sent the rest of the loop through 0x80390008 and returned garbage: a crash in a memcpy.
        t2 = 10
        lines = code(0x001000, j(0x80301000)) + code(0x300000, lui(t2, 0x8039), lbu(V1, t2, 0), addiu(t2, t2, 8), lbu(V1, t2, 0), JR_RA, 0)
        text, rep = self.run_moved(lines)
        w = values(text)
        self.assertEqual(word(w, 0x80300000), lui(t2, 0x8039))
        self.assertEqual(word(w, 0x80300004), lbu(V1, t2, 0))
        self.assertEqual(word(w, 0x80300008), addiu(t2, t2, 8))
        self.assertEqual(rep['stepped_bases'], ['80300000'])

    def test_a_stepped_base_whose_target_stays_is_fine(self):
        t2 = 10
        mapper = v.Mapper([(0x390000, 0x392000, 0)] + MOVED.ranges, OVERLAYS)
        lines = code(0x001000, j(0x80301000)) + code(0x300000, lui(t2, 0x8039), lbu(V1, t2, 0), addiu(t2, t2, 8), lbu(V1, t2, 0), JR_RA, 0)
        text, rep = v.translate_values('\n'.join(['[g]'] + lines), mapper, LAYOUT)
        self.assertEqual(word(values(text), 0x80300004), lbu(V1, t2, 0))
        self.assertEqual(rep['stepped_bases'], [])

    def test_move_writes_moves_conditions_and_writes_in_range_only(self):
        text, moved = v.move_writes('D138F000 0001\n8138F004 3C01\n81100000 0001\n50000402 0002\n81391000 0001\n', 0x38F000, 0x391000, 0x1000)
        self.assertEqual(text.splitlines()[:3], ['D1390000 0001', '81390004 3C01', '81100000 0001'])
        self.assertIn('50000402 0002', text)
        self.assertEqual(moved, 2)

    def test_a_pointer_to_a_moved_table_follows_it(self):
        lines = code(0x3E7B18, 0x803E8BA0, 0x80EEEEEE, 0x80500000) + code(0x3E7BA0, 0x11111111)
        w = values(self.run_moved(lines)[0])
        self.assertEqual(word(w, 0x803E7B18), 0x803E7BA0)
        self.assertEqual(word(w, 0x803E7B1C), 0x80EEEEEE)            # past RAM
        self.assertEqual(word(w, 0x803E7B20), 0x80500000)            # doesn't move

    def test_a_pointer_like_word_with_nothing_written_behind_it_stays(self):
        lines = code(0x3E7B18, 0x803E8BA0)                           # a colour, for all anyone knows
        text, rep = self.run_moved(lines)
        self.assertEqual(values(text), values('\n'.join(lines)))
        self.assertEqual(rep['pointers'], 0)


if __name__ == '__main__':
    unittest.main()
