"""Tests for make_patch_mod.py (BPS encoding of moved data, the TargetRead limit, the changed-function
report, escaping branches). The ROM-like buffers are pseudo-random bytes, never ROM data.

    python -m unittest discover -s tools/romhack/tests
"""
import os, random, sys, tempfile, unittest

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


def actions(patch):
    """[(kind, length)] of a BPS patch's actions (0 SourceRead, 1 TargetRead, 2 SourceCopy, 3 TargetCopy)."""
    def number(p):
        data, shift = 0, 1
        while True:
            x = patch[p]
            p += 1
            data += (x & 0x7F) * shift
            if x & 0x80:
                return data, p
            shift <<= 7
            data += shift

    p = 4
    for _ in range(3):
        value, p = number(p)
    p += value  # metadata
    out = []
    while p < len(patch) - 12:
        cmd, p = number(p)
        kind, length = cmd & 3, (cmd >> 2) + 1
        out.append((kind, length))
        if kind == 1:
            p += length
        elif kind >= 2:
            _, p = number(p)
    return out


def literal_bytes(patch):
    return sum(length for kind, length in actions(patch) if kind == 1)


class TestBpsMovedData(unittest.TestCase):
    """A hack that moves data (VPW Studio grows or shrinks a file and slides everything after it)
    must not turn into a copy of the stock ROM (aki-roadmap#74)."""

    SIZE = 4 << 20

    def setUp(self):
        r = random.Random(74)
        # ROM-like: random "compressed files" separated by zero padding, and a pointer table.
        data = bytearray()
        while len(data) < self.SIZE:
            data += r.randbytes(r.randrange(0x400, 0x8000))
            data += bytes(r.randrange(0, 0x100) & ~3)
        self.base = bytes(data[:self.SIZE])
        self.rand = r

    def check(self, hack, max_patch):
        stats = {}
        patch = mpm.bps_encode(self.base, hack, stats=stats)
        self.assertEqual(mpm.bps_apply(self.base, patch), hack)
        self.assertLess(len(patch), max_patch)
        self.assertEqual(stats['target_read'], literal_bytes(patch))
        self.assertEqual(sum(stats.values()), len(hack))
        return patch, stats

    def test_unchanged_rom_is_one_source_read(self):
        patch, stats = self.check(self.base, 64)
        self.assertEqual(actions(patch), [(0, len(self.base))])
        self.assertEqual(stats['target_read'], 0)

    def test_in_place_edit_stays_in_place(self):
        hack = bytearray(self.base)
        hack[0x1234:0x1238] = bytes.fromhex('deadbeef')
        patch, stats = self.check(bytes(hack), 64)
        self.assertEqual(stats['source_copy'], 0)
        self.assertLessEqual(stats['target_read'], 4)

    def test_grown_file_slides_the_rest(self):
        # Grow a "file" in the middle by 64 bytes, slide everything after it, rewrite a few
        # pointers after the move, and append the rewritten table at the end (file grows).
        at = 0x180000
        hack = bytearray(self.base[:at] + self.rand.randbytes(64) + self.base[at:])
        for p in range(0x300000, 0x300040, 4):
            hack[p:p + 4] = (int.from_bytes(hack[p:p + 4], 'big') + 64 & 0xFFFFFFFF).to_bytes(4, 'big')
        hack += bytes(0x1000)
        patch, stats = self.check(bytes(hack), 4096)
        self.assertGreater(stats['source_copy'], len(self.base) // 2)
        self.assertLessEqual(stats['target_read'], 64 + 64 + 0x1000)

    def test_shrunk_file_slides_the_rest_back(self):
        hack = self.base[:0x200000] + self.base[0x200100:]
        patch, stats = self.check(hack, 256)
        self.assertEqual(stats['target_read'], 0)

    def test_shifted_block(self):
        # A 64 KB block moves 0x10 bytes later inside the same file size; the gap it leaves
        # and the bytes it covers are rewritten.
        src, n, shift = 0x100000, 0x10000, 0x10
        hack = bytearray(self.base)
        hack[src + shift:src + shift + n] = self.base[src:src + n]
        hack[src:src + shift] = bytes(shift)
        patch, stats = self.check(bytes(hack), 256)
        self.assertGreaterEqual(stats['source_copy'], n)
        self.assertLessEqual(stats['target_read'], shift)

    def test_block_moved_far_away(self):
        # Swap two distant 32 KB regions: both are found through the block index.
        a, b, n = 0x080000, 0x380000, 0x8000
        hack = bytearray(self.base)
        hack[a:a + n], hack[b:b + n] = self.base[b:b + n], self.base[a:a + n]
        patch, stats = self.check(bytes(hack), 256)
        self.assertEqual(stats['target_read'], 0)

    def test_new_data_is_literal(self):
        new = self.rand.randbytes(0x2000)
        hack = self.base[:0x100000] + new + self.base[0x100000:]
        patch, stats = self.check(hack, 0x2000 + 256)
        self.assertEqual(stats['target_read'], len(new))

    def test_too_much_target_read_is_refused(self):
        hack = self.base[:0x1000] + self.rand.randbytes(3 << 20) + self.base[0x1000:]
        with self.assertRaises(mpm.TooMuchTargetRead):
            mpm.bps_encode(self.base, hack, max_target_read=2 << 20)
        # Under the limit it is written.
        patch = mpm.bps_encode(self.base, hack, max_target_read=4 << 20)
        self.assertEqual(mpm.bps_apply(self.base, patch), hack)

    def test_empty_and_tiny_buffers(self):
        for source, target in ((b'', b''), (b'', b'abc'), (b'abc', b''), (b'abc', b'abd'), (b'a' * 100, b'a' * 300)):
            patch = mpm.bps_encode(source, target)
            self.assertEqual(mpm.bps_apply(source, patch), target)


if __name__ == '__main__':
    unittest.main()
