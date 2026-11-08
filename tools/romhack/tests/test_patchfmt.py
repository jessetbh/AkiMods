# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 the AkiMods contributors
"""Tests for patchfmt.py. Every patch is built here by small encoders from pseudo-random or
patterned bytes; no ROM data is used anywhere.

    python -m unittest            (from the repository root)
"""
import os
import random
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(HERE, '..', 'romhack'), os.path.join(HERE, '..')):
    if os.path.exists(os.path.join(_p, 'patchfmt.py')):
        sys.path.insert(0, _p)
import patchfmt as pf  # noqa: E402

Z64 = b'\x80\x37\x12\x40'


def rand_bytes(n, seed):
    if n == 0:
        return b''
    return random.Random(seed).getrandbits(8 * n).to_bytes(n, 'little')


def fake_rom(n, seed=1):
    """Pseudo-random bytes shaped like a z64 ROM header (magic, CRC at 0x10, cart ID at 0x3C)."""
    data = bytearray(rand_bytes(n, seed))
    data[0:4] = Z64
    data[0x3B:0x3F] = b'NTSE'
    return bytes(data)


def diff_runs(a, b):
    """[(start, end)] of maximal runs where equal-length a and b differ."""
    runs, n, i, step = [], len(a), 0, 1 << 16
    while i < n:
        j = min(i + step, n)
        if a[i:j] != b[i:j]:
            for k in range(i, j):
                if a[k] != b[k]:
                    if runs and runs[-1][1] == k:
                        runs[-1][1] = k + 1
                    else:
                        runs.append([k, k + 1])
        i = j
    return [tuple(r) for r in runs]


def padded(a, b):
    n = max(len(a), len(b))
    return bytes(a) + bytes(n - len(a)), bytes(b) + bytes(n - len(b))


def swap16(data):
    out = bytearray(data)
    out[0::2], out[1::2] = data[1::2], data[0::2]
    return bytes(out)


# ------------------------------------------------------------------------------- encoders

def beat_num(n):
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def beat_footer(body, c1, c2):
    body = bytes(body) + c1.to_bytes(4, 'little') + c2.to_bytes(4, 'little')
    return body + (zlib.crc32(body) & 0xFFFFFFFF).to_bytes(4, 'little')


def crc(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def bps_actions(source, target, actions, meta=b''):
    """BPS from explicit actions: ('SR', n), ('TR', bytes), ('SC', n, abs_src), ('TC', n, abs_tgt)."""
    out = bytearray(b'BPS1' + beat_num(len(source)) + beat_num(len(target)) + beat_num(len(meta)) + meta)
    src_rel = tgt_rel = 0
    for act in actions:
        if act[0] == 'SR':
            out += beat_num(((act[1] - 1) << 2) | 0)
        elif act[0] == 'TR':
            out += beat_num(((len(act[1]) - 1) << 2) | 1) + act[1]
        else:
            kind, length, at = act
            rel = at - (src_rel if kind == 'SC' else tgt_rel)
            out += beat_num(((length - 1) << 2) | (2 if kind == 'SC' else 3))
            out += beat_num((abs(rel) << 1) | (rel < 0))
            if kind == 'SC':
                src_rel = at + length
            else:
                tgt_rel = at + length
    return beat_footer(out, crc(source), crc(target))


def bps_encode(source, target):
    """SourceRead where the bytes match, TargetRead where they don't."""
    actions, pos = [], 0
    m = min(len(source), len(target))
    for s, e in diff_runs(source[:m], target[:m]) + ([(m, len(target))] if len(target) > m else []):
        if s > pos:
            actions.append(('SR', s - pos))
        actions.append(('TR', bytes(target[s:e])))
        pos = e
    if pos < len(target):
        actions.append(('SR', len(target) - pos))
    return bps_actions(source, target, actions)


def ups_encode(source, target):
    out = bytearray(b'UPS1' + beat_num(len(source)) + beat_num(len(target)))
    a, b = padded(source, target)
    pos = 0
    for s, e in diff_runs(a, b):
        out += beat_num(s - pos)
        out += (int.from_bytes(a[s:e], 'big') ^ int.from_bytes(b[s:e], 'big')).to_bytes(e - s, 'big') + b'\0'
        pos = e + 1
    return beat_footer(out, crc(source), crc(target))


def ips_encode(source, target, rle=True, truncate=True):
    out = bytearray(b'PATCH')
    a, b = padded(source, target)
    runs = diff_runs(a[:len(target)], b[:len(target)])
    if len(target) > len(source) and (not runs or runs[-1][1] != len(target)):
        runs.append((len(target) - 1, len(target)))      # write the last byte so the file grows
    for s, e in runs:
        while s < e:
            if s == 0x454F46:                            # that offset reads as "EOF"
                s -= 1
            n = min(e - s, 0xFFFF)
            chunk = b[s:s + n]
            if rle and n >= 4 and chunk == chunk[:1] * n:
                out += s.to_bytes(3, 'big') + b'\0\0' + n.to_bytes(2, 'big') + chunk[:1]
            else:
                out += s.to_bytes(3, 'big') + n.to_bytes(2, 'big') + chunk
            s += n
    out += b'EOF'
    if truncate and len(target) < len(source):
        out += len(target).to_bytes(3, 'big')
    return bytes(out)


def aps_encode(source, target, n64=True, order_byte=1, desc=b'synthetic test patch'):
    out = bytearray(b'APS10' + bytes([1 if n64 else 0, 0]) + desc.ljust(50, b' '))
    if n64:
        out += bytes([order_byte]) + source[0x3C:0x3F] + source[0x10:0x18] + bytes(5)
    out += len(target).to_bytes(4, 'little')
    a, b = padded(source, target)
    for s, e in diff_runs(a[:len(target)], b[:len(target)]):
        while s < e:
            n = min(e - s, 255)
            chunk = b[s:s + n]
            if n >= 3 and chunk == chunk[:1] * n:
                out += s.to_bytes(4, 'little') + b'\0' + chunk[:1] + bytes([n])
            else:
                out += s.to_bytes(4, 'little') + bytes([n]) + chunk
            s += n
    return bytes(out)


# VCDIFF (RFC 3284). The test's own idea of the default code table, from the RFC's layout.
NOOP, ADD, RUN, COPY = 0, 1, 2, 3


def rfc_code_table():
    t = [(RUN, 0, 0, NOOP, 0, 0)] + [(ADD, s, 0, NOOP, 0, 0) for s in range(18)]
    for m in range(9):
        t += [(COPY, s, m, NOOP, 0, 0) for s in [0] + list(range(4, 19))]
    for m in range(6):
        for a in range(1, 5):
            for c in range(4, 7):
                t.append((ADD, a, 0, COPY, c, m))
    for m in range(6, 9):
        for a in range(1, 5):
            t.append((ADD, a, 0, COPY, 4, m))
    for m in range(9):
        t.append((COPY, 4, m, ADD, 1, 0))
    return t


TABLE = rfc_code_table()
SINGLE = {(e[0], e[1], e[2]): i for i, e in enumerate(TABLE) if e[3] == NOOP}
PAIR = {(e[0], e[1], e[2], e[3], e[4], e[5]): i for i, e in enumerate(TABLE) if e[3] != NOOP}


def vint(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def reference_window(seg, insts):
    """Naive byte-at-a-time decoder: the window's target from (op, ...) instructions."""
    u = bytearray(seg)
    for ins in insts:
        if ins[0] == 'ADD':
            u += ins[1]
        elif ins[0] == 'RUN':
            u += bytes([ins[1]]) * ins[2]
        else:
            addr, n = ins[1], ins[2]
            for k in range(n):
                u.append(u[addr + k])
    return bytes(u[len(seg):])


class Vcdiff:
    """Hand-built VCDIFF/xdelta3 patch writer for tests."""

    def __init__(self, app_header=None, secondary=None, version=0, code_table=False):
        hdr = (1 if secondary is not None else 0) | (2 if code_table else 0) | (4 if app_header is not None else 0)
        self.out = bytearray(b'\xd6\xc3\xc4' + bytes([version, hdr]))
        if secondary is not None:
            self.out.append(secondary)
        if code_table:
            self.out += vint(2) + b'\0\0'
        if app_header is not None:
            self.out += vint(len(app_header)) + app_header
        self.modes_used = set()
        self.codes_used = set()

    def window(self, insts, seg=b'', source=None, target=None, adler=True, pair=True, modes=None,
               delta_indicator=0, bad_adler=False):
        """insts: ('ADD', bytes) / ('RUN', byte, n) / ('COPY', addr, n). source/target: (pos, len)
        of the source segment in the source ROM / earlier output; seg is those bytes (for the
        checksum). modes: optional {inst index: forced address mode}."""
        data, inst, addr = bytearray(), bytearray(), bytearray()
        near, same, slot = [0] * 4, [0] * 768, 0
        here = len(seg)
        coded = []                                       # (type, size, mode)
        for idx, ins in enumerate(insts):
            if ins[0] == 'ADD':
                data += ins[1]
                coded.append((ADD, len(ins[1]), 0))
                here += len(ins[1])
                continue
            if ins[0] == 'RUN':
                data.append(ins[1])
                coded.append((RUN, ins[2], 0))
                here += ins[2]
                continue
            a, n = ins[1], ins[2]
            options = [(0, vint(a)), (1, vint(here - a))]
            options += [(2 + i, vint(a - near[i])) for i in range(4) if a >= near[i]]
            if same[a % 768] == a:
                options.append((6 + (a % 768) // 256, bytes([a % 256])))
            if modes and idx in modes:
                options = [o for o in options if o[0] == modes[idx]]
                assert options, 'forced mode %d not usable for instruction %d' % (modes[idx], idx)
            mode, enc = min(options, key=lambda o: len(o[1]))
            addr += enc
            self.modes_used.add(mode)
            near[slot] = a
            slot = (slot + 1) % 4
            same[a % 768] = a
            coded.append((COPY, n, mode))
            here += n
        i = 0
        while i < len(coded):
            t, s, m = coded[i]
            if pair and i + 1 < len(coded):
                t2, s2, m2 = coded[i + 1]
                key = (t, s, m, t2, s2, m2)
                if key in PAIR:
                    inst.append(PAIR[key])
                    self.codes_used.add(PAIR[key])
                    i += 2
                    continue
            if (t, s, m) in SINGLE and s:
                inst.append(SINGLE[(t, s, m)])
                self.codes_used.add(SINGLE[(t, s, m)])
            else:
                inst.append(SINGLE[(t, 0, m)])
                self.codes_used.add(SINGLE[(t, 0, m)])
                inst += vint(s)
            i += 1
        target_bytes = reference_window(seg, insts)
        body = vint(len(target_bytes)) + bytes([delta_indicator]) + vint(len(data)) + vint(len(inst)) + vint(len(addr))
        if adler:
            a32 = zlib.adler32(target_bytes) & 0xFFFFFFFF
            body += (a32 ^ (1 if bad_adler else 0)).to_bytes(4, 'big')
        body += data + inst + addr
        wi = (1 if source else 0) | (2 if target else 0) | (4 if adler else 0)
        self.out.append(wi)
        if source or target:
            pos, length = source or target
            self.out += vint(length) + vint(pos)
        self.out += vint(len(body)) + body
        return target_bytes

    def bytes(self):
        return bytes(self.out)


# ---------------------------------------------------------------------------------- tests

class DetectTest(unittest.TestCase):
    def test_headers(self):
        self.assertEqual(pf.detect(b'\xd6\xc3\xc4\x00\x00'), 'vcdiff')
        self.assertEqual(pf.detect(b'BPS1\x80'), 'bps')
        self.assertEqual(pf.detect(b'PATCHEOF'), 'ips')
        self.assertEqual(pf.detect(b'UPS1\x80'), 'ups')
        self.assertEqual(pf.detect(b'APS10\x01'), 'aps')
        self.assertEqual(pf.detect(bytearray(b'BPS1')), 'bps')
        self.assertEqual(pf.detect(memoryview(b'UPS1xx')), 'ups')

    def test_unknown(self):
        for junk in (b'', b'PAT', b'PPF30', b'IPS32', b'APS1\x00', b'\x80\x37\x12\x40'):
            self.assertIsNone(pf.detect(junk))
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(b'hello world', b'rom')
        self.assertEqual(cm.exception.kind, 'unknown-format')
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(b'APS1\x00\x00\x00\x00', b'rom')
        self.assertIn('Game Boy Advance', str(cm.exception))


class BpsTest(unittest.TestCase):
    def setUp(self):
        self.src = fake_rom(4096, 2)
        t = bytearray(self.src)
        t[100:110] = b'0123456789'
        t[2000:2003] = b'xyz'
        self.tgt = bytes(t) + b'tail bytes'

    def test_round_trip_and_info(self):
        out, info = pf.apply(bps_encode(self.src, self.tgt), self.src)
        self.assertEqual(out, self.tgt)
        self.assertIsInstance(out, bytearray)
        self.assertEqual(info['format'], 'bps')
        self.assertTrue(info['source_checked'])
        self.assertEqual(info['target_size'], len(self.tgt))
        self.assertEqual(info['expected_source_crc32'], crc(self.src))

    def test_all_actions_and_overlap(self):
        src = rand_bytes(300, 3)
        # TargetRead 'ab', overlapping TargetCopy repeats it, SourceCopy backwards and forwards.
        tgt = src[:50] + b'ab' + b'ababababab' + src[200:240] + src[10:20] + b'Q' * 7
        actions = [('SR', 50), ('TR', b'ab'), ('TC', 10, 50), ('SC', 40, 200), ('SC', 10, 10),
                   ('TR', b'Q'), ('TC', 6, len(tgt) - 7)]
        patch = bps_actions(src, tgt, actions, meta=b'<x>meta</x>')
        out, info = pf.apply(patch, src)
        self.assertEqual(out, tgt)
        self.assertEqual(info['metadata'], '<x>meta</x>')

    def test_shrink(self):
        tgt = self.src[:1000]
        self.assertEqual(pf.apply(bps_encode(self.src, tgt), self.src)[0], tgt)

    def test_wrong_rom(self):
        other = fake_rom(4096, 99)
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bps_encode(self.src, self.tgt), other)
        self.assertEqual(cm.exception.kind, 'wrong-rom')
        self.assertIn('different ROM', str(cm.exception))
        self.assertIn('%08X' % crc(self.src), str(cm.exception))

    def test_already_patched(self):
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bps_encode(self.src, self.tgt), self.tgt)
        self.assertEqual(cm.exception.kind, 'already-patched')

    def test_damaged(self):
        patch = bytearray(bps_encode(self.src, self.tgt))
        patch[20] ^= 0xFF
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bytes(patch), self.src)
        self.assertEqual(cm.exception.kind, 'damaged')
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bps_encode(self.src, self.tgt)[:10], self.src)
        self.assertEqual(cm.exception.kind, 'damaged')

    def test_bad_target_copy(self):
        src = rand_bytes(64, 4)
        patch = bps_actions(src, b'x' * 8, [('TC', 8, 0)])     # copies output not yet written
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(patch, src)
        self.assertEqual(cm.exception.kind, 'damaged')

    def test_too_large(self):
        body = b'BPS1' + beat_num(4) + beat_num(1 << 40) + beat_num(0)
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(beat_footer(body, crc(b'abcd'), 0), b'abcd')
        self.assertEqual(cm.exception.kind, 'too-large')

    def test_byte_swapped_source(self):
        out, info = pf.apply(bps_encode(self.src, self.tgt), swap16(self.src))
        self.assertEqual(out, self.tgt)
        self.assertEqual(info['byte_order'], 'z64')
        self.assertTrue(any('.v64' in n for n in info['notes']))

    def test_inputs_any_bytes_like(self):
        patch = bps_encode(self.src, self.tgt)
        out, _ = pf.apply(bytearray(patch), memoryview(bytearray(self.src)))
        self.assertEqual(out, self.tgt)


class UpsTest(unittest.TestCase):
    def setUp(self):
        self.src = fake_rom(3000, 5)

    def test_same_size(self):
        t = bytearray(self.src)
        t[5:9] = b'\0\0\0\0'
        t[2999] ^= 0x55
        out, info = pf.apply(ups_encode(self.src, bytes(t)), self.src)
        self.assertEqual(out, t)
        self.assertTrue(info['source_checked'])

    def test_grow_and_shrink(self):
        grow = self.src + rand_bytes(500, 6) + b'\0\0'
        self.assertEqual(pf.apply(ups_encode(self.src, grow), self.src)[0], grow)
        shrink = self.src[:1234]
        self.assertEqual(pf.apply(ups_encode(self.src, shrink), self.src)[0], shrink)

    def test_errors(self):
        tgt = self.src[:2000] + b'changed'
        patch = ups_encode(self.src, tgt)
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(patch, fake_rom(3000, 7))
        self.assertEqual(cm.exception.kind, 'wrong-rom')
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(patch, tgt)
        self.assertEqual(cm.exception.kind, 'already-patched')
        bad = bytearray(patch)
        bad[8] ^= 1
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bytes(bad), self.src)
        self.assertEqual(cm.exception.kind, 'damaged')

    def test_unterminated_run(self):
        body = b'UPS1' + beat_num(4) + beat_num(4) + beat_num(0) + b'\x01\x02'   # no 0 terminator
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(beat_footer(body, crc(b'abcd'), 0), b'abcd')
        self.assertEqual(cm.exception.kind, 'damaged')


class IpsTest(unittest.TestCase):
    def setUp(self):
        self.src = rand_bytes(0x460000, 8)     # past 0x454F46, the offset that reads as "EOF"

    def test_records_rle_and_eof_offset(self):
        t = bytearray(self.src)
        t[10:20] = b'A' * 10                   # RLE
        t[0x454F46:0x454F50] = b'B' * 3 + b'0123456'
        t[0x100000:0x100000 + 70000] = rand_bytes(70000, 9)   # longer than one record
        patch = ips_encode(self.src, bytes(t))
        self.assertIn(b'\x00\x00\x0a\x00\x00\x00\x0aA', patch)     # offset 10, RLE, 10 x 'A'
        out, info = pf.apply(patch, self.src)
        self.assertEqual(out, t)
        self.assertFalse(info['source_checked'])

    def test_grow_and_truncate(self):
        src = rand_bytes(1000, 10)
        grow = src + b'\x01\x02\x03'
        self.assertEqual(pf.apply(ips_encode(src, grow), src)[0], grow)
        shrink = src[:600]
        out, info = pf.apply(ips_encode(src, shrink), src)
        self.assertEqual(out, shrink)
        self.assertTrue(any('cuts the file' in n for n in info['notes']))

    def test_trailing_junk_and_errors(self):
        src = rand_bytes(100, 11)
        tgt = b'X' + src[1:]
        out, info = pf.apply(ips_encode(src, tgt) + b'junkjunk', src)
        self.assertEqual(out, tgt)
        self.assertTrue(any('extra bytes' in n for n in info['notes']))
        for bad in (b'PATCH', b'PATCH\x00\x00\x01\x00\x05ab', b'PATCH\x00\x00\x01\x00'):
            with self.assertRaises(pf.PatchError) as cm:
                pf.apply(bad, src)
            self.assertEqual(cm.exception.kind, 'damaged')


class ApsTest(unittest.TestCase):
    def setUp(self):
        self.src = fake_rom(8192, 12)
        t = bytearray(self.src)
        t[0x1000:0x1010] = b'\xEE' * 16           # RLE
        t[0x1800:0x1900] = rand_bytes(256, 13)    # longer than one record
        self.tgt = bytes(t) + rand_bytes(100, 14)

    def test_n64_round_trip(self):
        out, info = pf.apply(aps_encode(self.src, self.tgt), self.src)
        self.assertEqual(out, self.tgt)
        self.assertTrue(info['source_checked'])
        self.assertEqual(info['description'], 'synthetic test patch')
        self.assertEqual(info['expected_cart_id'], 'TSE')

    def test_simple_type_and_shrink(self):
        out, info = pf.apply(aps_encode(self.src, self.src[:5000], n64=False), self.src)
        self.assertEqual(out, self.src[:5000])
        self.assertFalse(info['source_checked'])

    def test_wrong_rom(self):
        other = bytearray(self.src)
        other[0x10] ^= 1                          # header CRC differs
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(aps_encode(self.src, self.tgt), bytes(other))
        self.assertEqual(cm.exception.kind, 'wrong-rom')
        other = bytearray(self.src)
        other[0x3E:0x3F] = b'J'                   # other region
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(aps_encode(self.src, self.tgt), bytes(other))
        self.assertIn("'TSJ'", str(cm.exception))

    def test_byte_order(self):
        # Patch made against the z64 ROM, player has the v64 one: converted to z64.
        out, info = pf.apply(aps_encode(self.src, self.tgt), swap16(self.src))
        self.assertEqual(out, self.tgt)
        self.assertEqual(info['byte_order'], 'z64')
        # Patch made against a v64 ROM, player has z64: converted to v64, output in v64 order.
        v_src, v_tgt = swap16(self.src), swap16(self.tgt)
        out, info = pf.apply(aps_encode(v_src, v_tgt, order_byte=0), self.src)
        self.assertEqual(out, v_tgt)
        self.assertEqual(info['patch_byte_order'], 'v64')

    def test_errors(self):
        patch = bytearray(aps_encode(self.src, self.tgt))
        patch[6] = 1
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bytes(patch), self.src)
        self.assertEqual(cm.exception.kind, 'unsupported')
        patch = aps_encode(self.src, self.src) + (9000).to_bytes(4, 'little') + b'\x04abcd'
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(patch, self.src)
        self.assertEqual(cm.exception.kind, 'damaged')
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(aps_encode(self.src, self.tgt)[:-3], self.src)
        self.assertEqual(cm.exception.kind, 'damaged')


class VcdiffTest(unittest.TestCase):
    def setUp(self):
        self.src = rand_bytes(5000, 15)

    def test_code_table_matches_rfc(self):
        self.assertEqual(pf.CODE_TABLE, TABLE)
        t = pf.CODE_TABLE                          # spot checks against RFC 3284 section 5.6
        self.assertEqual(t[0], (RUN, 0, 0, NOOP, 0, 0))
        self.assertEqual(t[1], (ADD, 0, 0, NOOP, 0, 0))
        self.assertEqual(t[18], (ADD, 17, 0, NOOP, 0, 0))
        self.assertEqual(t[19], (COPY, 0, 0, NOOP, 0, 0))
        self.assertEqual(t[34], (COPY, 18, 0, NOOP, 0, 0))
        self.assertEqual(t[162], (COPY, 18, 8, NOOP, 0, 0))
        self.assertEqual(t[163], (ADD, 1, 0, COPY, 4, 0))
        self.assertEqual(t[174], (ADD, 4, 0, COPY, 6, 0))
        self.assertEqual(t[234], (ADD, 4, 0, COPY, 6, 5))
        self.assertEqual(t[235], (ADD, 1, 0, COPY, 4, 6))
        self.assertEqual(t[246], (ADD, 4, 0, COPY, 4, 8))
        self.assertEqual(t[247], (COPY, 4, 0, ADD, 1, 0))
        self.assertEqual(t[255], (COPY, 4, 8, ADD, 1, 0))

    def test_instructions_and_address_modes(self):
        v = Vcdiff(app_header=b'hack.z64//stock.z64/')
        seg = self.src[1000:3000]
        insts = [('COPY', 0, 100), ('ADD', b'hi'), ('COPY', 500, 4), ('RUN', 0x7A, 40),
                 ('COPY', 104, 4), ('ADD', b'z'), ('COPY', 200, 18), ('COPY', 0, 30),
                 ('COPY', 2000, 6),                    # first output bytes (address space: seg + target)
                 ('COPY', 2100, 30),                   # overlaps the bytes it is writing
                 ('COPY', 1990, 20),                   # starts in the segment, ends in the output
                 ('ADD', b'abcd'), ('COPY', 700, 5), ('COPY', 2105, 4), ('ADD', b'q'),
                 ('COPY', 0, 7), ('COPY', 500, 4), ('ADD', rand_bytes(300, 16))]
        modes = {0: 0, 2: 0, 4: 1, 6: 2, 7: 6, 8: 0, 12: 0, 13: 4, 16: 7}
        expect = v.window(insts, seg=seg, source=(1000, 2000), modes=modes)
        self.assertTrue({0, 1, 2, 4, 6, 7} <= v.modes_used, v.modes_used)
        self.assertTrue(any(163 <= c <= 246 for c in v.codes_used))
        out, info = pf.apply(v.bytes(), self.src)
        self.assertEqual(out, expect)
        self.assertEqual(info['format'], 'vcdiff')
        self.assertEqual(info['app_header'], 'hack.z64//stock.z64/')
        self.assertTrue(info['source_checked'])
        self.assertEqual(info['notes'], [])

    def test_unpaired_size_zero_codes(self):
        v = Vcdiff()
        insts = [('ADD', rand_bytes(40, 17)), ('COPY', 10, 300), ('RUN', 1, 1000), ('COPY', 0, 4)]
        expect = v.window(insts, seg=self.src[:4000], source=(0, 4000), pair=False)
        self.assertEqual(pf.apply(v.bytes(), self.src)[0], expect)

    def test_multiple_windows_and_target_segment(self):
        v = Vcdiff()
        w1 = v.window([('COPY', 0, 1000), ('ADD', b'first')], seg=self.src[:1000], source=(0, 1000))
        w2 = v.window([('ADD', b'no source'), ('RUN', 0, 10)], adler=False)
        seg3 = (w1 + w2)[500:1005]
        w3 = v.window([('COPY', 0, 505), ('COPY', 505, 50)], seg=seg3, target=(500, 505))
        w4 = v.window([('COPY', 0, 2500)], seg=self.src[2500:5000], source=(2500, 2500))
        out, info = pf.apply(v.bytes(), self.src)
        self.assertEqual(out, w1 + w2 + w3 + w4)
        self.assertEqual(info['windows'], 4)

    def test_unchecked_note(self):
        v = Vcdiff()
        expect = v.window([('COPY', 0, 100)], seg=self.src[:100], source=(0, 100), adler=False)
        out, info = pf.apply(v.bytes(), self.src)
        self.assertEqual(out, expect)
        self.assertFalse(info['source_checked'])
        self.assertTrue(any('no checksums' in n for n in info['notes']))

    def test_wrong_rom(self):
        v = Vcdiff()
        v.window([('COPY', 0, 1000), ('ADD', b'x')], seg=self.src[:1000], source=(0, 1000))
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(v.bytes(), rand_bytes(5000, 18))
        self.assertEqual(cm.exception.kind, 'wrong-rom')
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(v.bytes(), self.src[:900])        # source window past the end of the ROM
        self.assertEqual(cm.exception.kind, 'wrong-rom')
        self.assertIn('at least', str(cm.exception))

    def test_byte_swapped_source(self):
        src = fake_rom(4000, 19)
        v = Vcdiff()
        expect = v.window([('COPY', 0, 4000), ('ADD', b'!')], seg=src, source=(0, 4000))
        out, info = pf.apply(v.bytes(), swap16(src))
        self.assertEqual(out, expect)
        self.assertEqual(info['byte_order'], 'z64')

    def test_bad_checksum_without_source_is_damage(self):
        v = Vcdiff()
        v.window([('ADD', b'abc')], bad_adler=True)
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(v.bytes(), self.src)
        self.assertEqual(cm.exception.kind, 'damaged')

    def test_secondary_compression_refused(self):
        for sid, name in ((1, 'DJW'), (2, 'LZMA'), (16, 'FGK')):
            v = Vcdiff(secondary=sid)
            v.window([('ADD', b'abc')], delta_indicator=1)
            with self.assertRaises(pf.PatchError) as cm:
                pf.apply(v.bytes(), self.src)
            self.assertEqual(cm.exception.kind, 'unsupported')
            self.assertIn(name, str(cm.exception))
            self.assertIn('-S none', str(cm.exception))

    def test_secondary_named_but_unused(self):
        v = Vcdiff(secondary=2)
        expect = v.window([('ADD', b'abc'), ('COPY', 0, 10)], seg=self.src[:10], source=(0, 10))
        out, info = pf.apply(v.bytes(), self.src)
        self.assertEqual(out, expect)
        self.assertTrue(any('LZMA' in n for n in info['notes']))

    def test_unsupported_variants(self):
        v = Vcdiff(code_table=True)
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(v.bytes(), self.src)
        self.assertEqual(cm.exception.kind, 'unsupported')
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(b'\xd6\xc3\xc4S\x00', self.src)
        self.assertIn('SDCH', str(cm.exception))
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(b'\xd6\xc3\xc4\x01\x00', self.src)
        self.assertEqual(cm.exception.kind, 'unsupported')

    def test_damaged(self):
        v = Vcdiff()
        v.window([('COPY', 0, 1000), ('ADD', b'xyz')], seg=self.src[:1000], source=(0, 1000))
        good = v.bytes()
        for cut in range(5, len(good) - 1, 7):
            with self.assertRaises(pf.PatchError) as cm:
                pf.apply(good[:cut], self.src)
            self.assertIn(cm.exception.kind, ('damaged', 'wrong-rom'))
        # COPY from an address not yet written (mode 0, address = here).
        v = Vcdiff()
        body_insts = [('ADD', b'ab')]
        v.window(body_insts, adler=False)
        bad = bytearray(v.bytes())
        copy4 = bytes([TABLE.index((COPY, 4, 0, NOOP, 0, 0))])
        bad += b'\x00' + vint(7) + vint(4) + b'\x00' + vint(0) + vint(1) + vint(1) + copy4 + vint(0)
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bytes(bad), self.src)
        self.assertEqual(cm.exception.kind, 'damaged')
        self.assertIn('COPY address', str(cm.exception))
        with self.assertRaises(pf.PatchError) as cm:     # header only: no windows
            pf.apply(b'\xd6\xc3\xc4\x00\x00', self.src)
        self.assertIn('no data', str(cm.exception))


class ReviewFixesTest(unittest.TestCase):
    def test_already_patched_in_other_byte_order(self):
        src = fake_rom(2048, 30)
        tgt = src[:1000] + b'new' + src[1003:]
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bps_encode(src, tgt), swap16(tgt))
        self.assertEqual(cm.exception.kind, 'already-patched')

    def test_unchecked_patch_on_swapped_rom_warns(self):
        src = fake_rom(2048, 31)
        out, info = pf.apply(ips_encode(src, b'Z' + src[1:]), swap16(src))
        self.assertEqual(info['unchecked_byte_order'], 'v64')
        self.assertTrue(any('.z64' in n for n in info['notes']))
        out, info = pf.apply(ips_encode(src, b'Z' + src[1:]), src)
        self.assertNotIn('unchecked_byte_order', info)

    def test_aps_v64_patch_with_z64_header_fields(self):
        src, tgt = fake_rom(4096, 32), fake_rom(4096, 33)
        patch = bytearray(aps_encode(swap16(src), swap16(tgt), order_byte=0))
        patch[58:61], patch[61:69] = src[0x3C:0x3F], src[0x10:0x18]   # stored in z64 order
        out, info = pf.apply(bytes(patch), src)
        self.assertEqual(out, swap16(tgt))
        patch[57] = 7
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(bytes(patch), src)
        self.assertEqual(cm.exception.kind, 'damaged')

    def test_forced_format_must_match(self):
        with self.assertRaises(pf.PatchError) as cm:
            pf.apply(b'PATCHEOF', b'rom', fmt='bps')
        self.assertEqual(cm.exception.kind, 'unknown-format')
        self.assertEqual(pf.apply(b'PATCHEOF', b'rom', fmt='ips')[0], b'rom')

    def test_smallest_ups(self):
        patch = beat_footer(b'UPS1' + beat_num(1) + beat_num(1), crc(b'a'), crc(b'a'))
        self.assertEqual(len(patch), 18)
        self.assertEqual(pf.apply(patch, b'a')[0], b'a')

    def test_ips_truncate_only_shortens_and_empty_rle(self):
        src = rand_bytes(100, 34)
        out, info = pf.apply(b'PATCH' + b'EOF' + (5000).to_bytes(3, 'big'), src)
        self.assertEqual(out, src)
        out, _ = pf.apply(b'PATCH' + b'\x00\x10\x00\x00\x00\x00\x00A' + b'EOF', src)   # RLE count 0
        self.assertEqual(out, src)


class ByteOrderTest(unittest.TestCase):
    def test_convert(self):
        rom = fake_rom(64, 20)
        v64 = pf.convert_byte_order(rom, 'v64')
        n64 = pf.convert_byte_order(rom, 'n64')
        self.assertEqual(pf.n64_byte_order(v64), 'v64')
        self.assertEqual(pf.n64_byte_order(n64), 'n64')
        self.assertEqual(bytes(v64), swap16(rom))
        self.assertEqual(n64[:4], b'\x40\x12\x37\x80')
        self.assertEqual(pf.convert_byte_order(n64, 'z64'), rom)
        self.assertEqual(pf.convert_byte_order(v64, 'n64'), n64)
        self.assertIsNone(pf.n64_byte_order(b'abcd'))
        with self.assertRaises(ValueError):
            pf.convert_byte_order(b'abcd', 'z64')


class BigFileTest(unittest.TestCase):
    """Over 64 MB in and out (BTBW is 64.7 MB): 66 MB source, 70 MB target."""

    @classmethod
    def setUpClass(cls):
        block = rand_bytes(1 << 20, 21)
        src = bytearray(block * 66)
        src[0:4] = Z64
        for i in range(66):                     # make each MB unique
            src[i << 20 | 0x40:(i << 20 | 0x40) + 4] = i.to_bytes(4, 'big')
        cls.src = bytes(src)
        t = bytearray(src)
        t[0x10:0x18] = src[0x10:0x18]
        for off in (0x1000, 0x123456, 0x2000000, 0x3FFFFF0, len(src) - 10):
            t[off:off + 8] = b'PATCHED!'
        t += rand_bytes(4 << 20, 22)
        cls.tgt = bytes(t)

    def test_bps(self):
        out, _ = pf.apply(bps_encode(self.src, self.tgt), self.src)
        self.assertEqual(len(out), 70 << 20)
        self.assertTrue(out == self.tgt)

    def test_ups(self):
        self.assertTrue(pf.apply(ups_encode(self.src, self.tgt), self.src)[0] == self.tgt)

    def test_aps(self):
        self.assertTrue(pf.apply(aps_encode(self.src, self.tgt), self.src)[0] == self.tgt)

    def test_ips(self):                         # IPS can only address the first 16 MB
        t = bytearray(self.src)
        t[0x123456:0x123460] = b'0123456789'
        out, _ = pf.apply(ips_encode(self.src, bytes(t), truncate=False), self.src)
        self.assertTrue(out == t)

    def test_vcdiff(self):
        v = Vcdiff(app_header=b'big//src/')
        win = 8 << 20
        expect = bytearray()
        for start in range(0, len(self.tgt), win):
            end = min(start + win, len(self.tgt))
            if start < len(self.src):
                seg = self.src[start:min(end, len(self.src))]
                insts = [('COPY', 0, len(seg)), ('ADD', self.tgt[start + len(seg):end])]
                if not insts[1][1]:
                    insts.pop()
                expect += v.window(insts, seg=seg, source=(start, len(seg)))
            else:
                expect += v.window([('ADD', self.tgt[start:end])])
        out, info = pf.apply(v.bytes(), self.src)
        self.assertEqual(len(out), len(expect))
        self.assertTrue(out == expect)
        self.assertTrue(info['source_checked'])


if __name__ == '__main__':
    unittest.main()
