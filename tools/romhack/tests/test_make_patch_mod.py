"""Tests for make_patch_mod.py (BPS round trip, the changed-function report, escaping branches).

    python -m unittest discover -s tools/romhack/tests
"""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import make_patch_mod as mpm  # noqa: E402

OVERLAYS = '''
static FuncEntry section_0_main_funcs[] = {
    { .func = func_80000400, .offset = 0x00000000, .rom_size = 0x00000010 },
    { .func = func_80000410, .offset = 0x00000010, .rom_size = 0x00000010 },
};

static SectionTableEntry section_table[] = {
    { .rom_addr = 0x00001000, .ram_addr = 0x80000400, .size = 0x00000030, .funcs = section_0_main_funcs, .num_funcs = 2 },
};
'''

NOP = (0).to_bytes(4, 'big')
JR_RA = (0x03E00008).to_bytes(4, 'big')


def rom(code):
    data = bytearray(0x2000)
    data[0:4] = b'\x80\x37\x12\x40'
    data[0x1000:0x1000 + len(code)] = code
    return bytes(data)


def j(target):
    return ((0x02 << 26) | ((target >> 2) & 0x3FFFFFF)).to_bytes(4, 'big')


def jal(target):
    return ((0x03 << 26) | ((target >> 2) & 0x3FFFFFF)).to_bytes(4, 'big')


class TestMakePatchMod(unittest.TestCase):
    def setUp(self):
        fd, self.inl = tempfile.mkstemp(suffix='.inl')
        with os.fdopen(fd, 'w') as f:
            f.write(OVERLAYS)
        self.sections = mpm.load_sections(self.inl)
        self.base = rom(JR_RA + NOP * 3 + JR_RA + NOP * 3 + NOP * 4)

    def tearDown(self):
        os.remove(self.inl)

    def test_sections_and_functions_are_read(self):
        self.assertEqual(len(self.sections), 1)
        name, rom_addr, ram, size, funcs = self.sections[0]
        self.assertEqual((rom_addr, ram, size), (0x1000, 0x80000400, 0x30))
        self.assertEqual([f[0] for f in funcs], ['func_80000400', 'func_80000410'])

    def test_bps_round_trip(self):
        hack = bytearray(self.base)
        hack[0x1500:0x1510] = b'hack data here!!'
        hack += b'appended'
        patch = mpm.bps_encode(self.base, bytes(hack))
        self.assertEqual(mpm.bps_apply(self.base, patch), bytes(hack))

    def test_data_only_hack_changes_no_functions(self):
        hack = bytearray(self.base)
        hack[0x1800] ^= 0xFF
        funcs, loose = mpm.changed_functions(self.sections, self.base, bytes(hack))
        self.assertEqual((funcs, loose), ([], 0))

    def test_in_function_edit_is_reported_with_its_word_count(self):
        hack = bytearray(self.base)
        hack[0x1014:0x1018] = (0x00000025).to_bytes(4, 'big')  # nop -> or $zero, $zero, $zero
        funcs, loose = mpm.changed_functions(self.sections, self.base, bytes(hack))
        self.assertEqual(loose, 0)
        self.assertEqual([(f[1], f[2], f[4]) for f in funcs], [('func_80000410', 0x80000410, 1)])

    def test_bytes_outside_functions_count_as_loose(self):
        hack = bytearray(self.base)
        hack[0x1024] ^= 0x01  # inside the section, past the last function
        funcs, loose = mpm.changed_functions(self.sections, self.base, bytes(hack))
        self.assertEqual((funcs, loose), ([], 1))

    def test_calls_to_known_functions_dont_escape(self):
        hack = bytearray(self.base)
        hack[0x1004:0x1008] = jal(0x80000410)
        funcs, _ = mpm.changed_functions(self.sections, self.base, bytes(hack))
        self.assertEqual(mpm.escaping_branches(self.sections, bytes(hack), funcs), [])

    def test_jump_into_added_code_escapes(self):
        hack = bytearray(self.base)
        hack[0x1004:0x1008] = j(0x80000424)  # not a function start, outside the function
        funcs, _ = mpm.changed_functions(self.sections, self.base, bytes(hack))
        escapes = mpm.escaping_branches(self.sections, bytes(hack), funcs)
        self.assertEqual([(e[0], e[2], e[3]) for e in escapes], [('func_80000400', 0x80000424, 'jump')])


if __name__ == '__main__':
    unittest.main()
