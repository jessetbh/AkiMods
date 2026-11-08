"""Tests for tools/cht2codes.py (synthetic .cht text, no ROM). Run: python mods/cheats/tests/test_cht2codes.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import cht2codes  # noqa: E402

OLD_STYLE = '''[6C80F13B-427EDEAA-C:45]
Name=WWF No Mercy (U) (V1.1)
Cheat0="Have All\\\\Unlock Everything",50000B02 0000,810BD910 FFFF
Cheat0_N=Every wrestler and costume
Cheat1="Money",81097EDA ????
Cheat1_O=$FFFF Max,$0000 None
Cheat2="Enabler",F10396B0 2400

[2C3E19BD-5113EE5E-C:45]
Name=WCW vs. nWo - World Tour (U)
Cheat0="Unlock",80060665 00FF
'''

NEW_STYLE_REV0 = '''[B33F44F0-12345678-C:45]
Name=WWF No Mercy (U) (V1.0)

$Unlock Everything
.All characters
50000B02 0000
810BEE40 FFFF

$Smackdown Mall Money
8114FAD6 FFFF

$Infinite Money
8109940A ????
$FFFF Max
$0000 None
'''


class Cht2Codes(unittest.TestCase):
    def test_old_style_sections_names_notes_options(self):
        secs = cht2codes.parse_cht(OLD_STYLE)
        self.assertEqual(len(secs), 2)
        nm = secs[0]
        self.assertEqual(nm.identify(), ('nomercy', 1))
        self.assertEqual([c.name for c in nm.cheats], ['Have All\\Unlock Everything', 'Money', 'Enabler'])
        self.assertEqual(nm.cheats[0].codes, [('50000B02', '0000'), ('810BD910', 'FFFF')])
        self.assertEqual(nm.cheats[0].notes, ['Every wrestler and costume'])
        self.assertEqual(nm.cheats[1].options, [('FFFF', 'Max'), ('0000', 'None')])
        self.assertEqual(secs[1].identify(), ('wt', 0))

    def test_convert_rev1_tag_and_option_commented(self):
        out, rep = cht2codes.convert(cht2codes.parse_cht(OLD_STYLE)[0], 'x.cht')
        self.assertIn('# rom: nomercy-rev1\n', out)
        self.assertIn('# source: Project64 [6C80F13B-427EDEAA-C:45]', out)
        self.assertIn('[Have All\\Unlock Everything]\n# Every wrestler and costume\n50000B02 0000\n810BD910 FFFF', out)
        self.assertIn('# 81097EDA ???? -- choose a value (FFFF = Max, 0000 = None)', out)
        self.assertEqual(rep['needs_option'], 1)
        out, rep = cht2codes.convert(cht2codes.parse_cht(OLD_STYLE)[0], 'x.cht', first_option=True)
        self.assertIn('81097EDA FFFF', out)
        self.assertEqual(rep['needs_option'], 0)

    def test_new_style_rev0_by_name_and_unmapped_count(self):
        sec = cht2codes.parse_cht(NEW_STYLE_REV0)[0]
        self.assertEqual(sec.identify(), ('nomercy', 0))
        self.assertEqual([c.name for c in sec.cheats], ['Unlock Everything', 'Smackdown Mall Money', 'Infinite Money'])
        self.assertEqual(sec.cheats[2].options, [('FFFF', 'Max'), ('0000', 'None')])
        out, rep = cht2codes.convert(sec, 'nm.cht')
        self.assertIn('# rom: nomercy-rev0\n', out)
        self.assertIn('# All characters\n', out)
        self.assertEqual(rep['unmapped_rev0'], 1)  # mall money

    def test_select(self):
        out, rep = cht2codes.convert(cht2codes.parse_cht(NEW_STYLE_REV0)[0], 'nm.cht', select='unlock')
        self.assertEqual(rep['cheats'], 1)
        self.assertNotIn('Mall', out)

    def test_unknown_revision_is_refused_by_tag(self):
        text = NEW_STYLE_REV0.replace('(V1.0)', '')
        out, rep = cht2codes.convert(cht2codes.parse_cht(text)[0], 'nm.cht')
        self.assertIn('# rom: nomercy-unknown\n', out)
        self.assertIsNone(rep['rev'])
        out, rep = cht2codes.convert(cht2codes.parse_cht(text)[0], 'nm.cht', rev=0)
        self.assertIn('# rom: nomercy-rev0\n', out)


if __name__ == '__main__':
    unittest.main()
