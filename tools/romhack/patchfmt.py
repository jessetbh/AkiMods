# SPDX-License-Identifier: GPL-3.0-or-later
#
# patchfmt.py: apply ROM-hack patches (xdelta/VCDIFF, BPS, IPS, UPS, APS) to a stock ROM.
# Copyright (C) 2026 the AkiMods contributors
#
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
# the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this program. If
# not, see <https://www.gnu.org/licenses/>.
"""Apply ROM-hack patches to a stock ROM. Pure Python, standard library only (3.7+).

Players' hacks come as patches against a stock ROM in one of five formats:

    vcdiff  xdelta / xdelta3 (RFC 3284 VCDIFF, plus xdelta3's app header and window adler32)
    bps     BPS (beat): source, target and patch CRC32s
    ips     IPS, with RLE records and the 24-bit truncate extension after "EOF" (shortens only)
    ups     UPS: source, target and patch CRC32s, XOR runs, size change
    aps     APS for N64 ("APS10"): cart ID and header CRC of the stock ROM, records

API:

    detect(patch) -> format name above, or None if it isn't a patch we know
    apply(patch, source, fmt=None) -> (target, info)

`patch` and `source` are any bytes-like objects; `target` is a bytearray. `info` is a dict:
format, format_name, source_size, target_size, source_checked (whether the patch stores
something that identifies its ROM and the source matched it), byte_order (when the source was
converted, see below), notes (list of player-readable strings), plus per-format extras
(expected_source_crc32, target_crc32, metadata, description, app_header, ...).

Every failure raises PatchError with a message a player can act on. Its `kind` says which:

    unknown-format   not a patch file we recognise
    unsupported      a real patch, but a variant we can't apply (e.g. xdelta3 secondary compression)
    damaged          the patch is truncated or corrupt
    wrong-rom        the patch is for a different ROM (size or checksum mismatch)
    already-patched  the ROM given already matches the patch's output
    too-large        the patch claims an output bigger than MAX_OUTPUT_SIZE

Source checks run before the patch is applied wherever the format records them (BPS, UPS, APS
N64, and VCDIFF source-window bounds); VCDIFF's per-window adler32 is checked as each window is
built. If a checked source doesn't match and it is an N64 ROM stored in another byte order
(.v64 byte-swapped or .n64 word-swapped), apply() converts it and tries again, and says so in
info['byte_order'] and info['notes'].

Memory: the source is read through a memoryview (no copy) and the output is one preallocated
buffer where the format gives its size, so 100 MB in and out is fine.
"""
import array
import zlib

__all__ = ['PatchError', 'detect', 'apply', 'n64_byte_order', 'convert_byte_order',
           'FORMAT_NAMES', 'MAX_OUTPUT_SIZE']

MAX_OUTPUT_SIZE = 512 * 1024 * 1024   # far beyond any N64 ROM (64 MB carts, hacks up to ~65 MB)

FORMAT_NAMES = {
    'vcdiff': 'xdelta (VCDIFF)',
    'bps': 'BPS',
    'ips': 'IPS',
    'ups': 'UPS',
    'aps': 'APS (N64)',
}


class PatchError(Exception):
    """A patch can't be applied. str(err) is a player-readable message; err.kind is one of
    unknown-format, unsupported, damaged, wrong-rom, already-patched, too-large."""

    def __init__(self, message, kind='damaged', fmt=None):
        super().__init__(message)
        self.kind = kind
        self.format = fmt


def _damaged(fmt, what=''):
    msg = 'The %s patch file is damaged or incomplete' % FORMAT_NAMES[fmt]
    if what:
        msg += ' (%s)' % what
    return PatchError(msg + '. Try downloading it again.', 'damaged', fmt)


def _size_text(n):
    if n >= 1024 * 1024:
        return '%d bytes (%.1f MB)' % (n, n / (1024.0 * 1024.0))
    return '%d bytes' % n


def _check_output_size(fmt, n):
    if n > MAX_OUTPUT_SIZE:
        raise PatchError('The %s patch would make a file of %s, which is far larger than any N64 ROM; '
                         'it is probably damaged.' % (FORMAT_NAMES[fmt], _size_text(n)), 'too-large', fmt)


def _wrong_rom(fmt, expected, got):
    return PatchError('This patch is for a different ROM: it expects %s, but yours is %s. Use the '
                      'stock ROM the hack was made for.' % (expected, got), 'wrong-rom', fmt)


def _view(data):
    mv = data if isinstance(data, memoryview) else memoryview(data)
    if mv.ndim != 1 or mv.format != 'B':
        mv = mv.cast('B')
    return mv


def _new_info(fmt, source_size):
    return {'format': fmt, 'format_name': FORMAT_NAMES[fmt], 'source_size': source_size,
            'target_size': None, 'source_checked': False, 'notes': []}


def _copy_inside(buf, frm, to, length):
    """buf[to:to+length] = buf[frm:...] copied one byte at a time forwards (frm < to), so an
    overlapping copy repeats the frm..to pattern, as BPS TargetCopy and VCDIFF COPY require."""
    dist = to - frm
    if dist >= length:
        buf[to:to + length] = buf[frm:frm + length]
    else:
        pattern = bytes(buf[frm:to])
        reps, rem = divmod(length, dist)
        buf[to:to + length] = pattern * reps + pattern[:rem]


# ------------------------------------------------------------------------------ N64 byte order

_N64_MAGIC = {
    b'\x80\x37\x12\x40': 'z64',   # big-endian, what the recomp runtime stores
    b'\x37\x80\x40\x12': 'v64',   # 16-bit byte-swapped
    b'\x40\x12\x37\x80': 'n64',   # 32-bit little-endian words
}


def n64_byte_order(rom):
    """'z64', 'v64' or 'n64' from an N64 ROM's first word, or None if it doesn't look like one."""
    return _N64_MAGIC.get(bytes(rom[:4]))


def _swapped(data, width):
    n = len(data) - len(data) % width
    code = 'H' if width == 2 else next(c for c in 'IL' if array.array(c).itemsize == 4)
    a = array.array(code)
    a.frombytes(data[:n])
    a.byteswap()
    out = bytearray(a.tobytes())
    out += data[n:]
    return out


def convert_byte_order(rom, to):
    """A copy of an N64 ROM in byte order `to` ('z64', 'v64' or 'n64'). Raises ValueError if
    the ROM's order can't be told from its first word."""
    frm = n64_byte_order(rom)
    if frm is None:
        raise ValueError('not an N64 ROM in a byte order we know')
    data = rom
    if frm == 'v64':
        data = _swapped(data, 2)
    elif frm == 'n64':
        data = _swapped(data, 4)
    if to == 'v64':
        data = _swapped(data, 2)
    elif to == 'n64':
        data = _swapped(data, 4)
    elif to != 'z64':
        raise ValueError('unknown byte order %r' % (to,))
    return data if isinstance(data, bytearray) else bytearray(data)


# ------------------------------------------------------------------------------------- detect

def detect(patch):
    """Format name ('vcdiff', 'bps', 'ips', 'ups', 'aps') from the patch's header, or None."""
    head = bytes(patch[:5])
    if head[:3] == b'\xd6\xc3\xc4':
        return 'vcdiff'
    if head[:4] == b'BPS1':
        return 'bps'
    if head[:4] == b'UPS1':
        return 'ups'
    if head == b'APS10':
        return 'aps'
    if head == b'PATCH':
        return 'ips'
    return None


# ---------------------------------------------------------------------------------------- BPS / UPS

def _beat_number(patch, p, end, fmt):
    """BPS/UPS variable-length number: 7 bits per byte, low first, high bit ends it."""
    data, shift = 0, 1
    while True:
        if p >= end:
            raise _damaged(fmt, 'a number runs past the end')
        x = patch[p]
        p += 1
        data += (x & 0x7F) * shift
        if x & 0x80:
            return data, p
        shift <<= 7
        data += shift
        if shift > 1 << 64:
            raise _damaged(fmt, 'a number is too long')


def _check_beat_source(fmt, src, want_size, want_crc, out_size, out_crc, info):
    crc = zlib.crc32(src) & 0xFFFFFFFF
    if len(src) == want_size and crc == want_crc:
        info['source_checked'] = True
        return
    if len(src) == out_size and crc == out_crc:
        raise PatchError('This ROM already has the patch applied (it matches the patched ROM the '
                         'patch makes). Use the stock ROM instead.', 'already-patched', fmt)
    raise _wrong_rom(fmt, 'a ROM of %s with CRC32 %08X' % (_size_text(want_size), want_crc),
                     '%s with CRC32 %08X' % (_size_text(len(src)), crc))


def _beat_footer(fmt, patch):
    if len(patch) < 4 + (3 if fmt == 'bps' else 2) + 12:
        raise _damaged(fmt, 'too short')
    stored = int.from_bytes(patch[-4:], 'little')
    if zlib.crc32(patch[:-4]) & 0xFFFFFFFF != stored:
        raise _damaged(fmt, "its own checksum doesn't match")
    return int.from_bytes(patch[-12:-8], 'little'), int.from_bytes(patch[-8:-4], 'little')


def _apply_bps(patch, src):
    fmt = 'bps'
    src_crc, tgt_crc = _beat_footer(fmt, patch)
    end = len(patch) - 12
    p = 4
    src_size, p = _beat_number(patch, p, end, fmt)
    tgt_size, p = _beat_number(patch, p, end, fmt)
    meta_size, p = _beat_number(patch, p, end, fmt)
    if p + meta_size > end:
        raise _damaged(fmt, 'metadata runs past the end')
    info = _new_info(fmt, len(src))
    info.update(target_size=tgt_size, expected_source_size=src_size, expected_source_crc32=src_crc,
                target_crc32=tgt_crc, metadata=bytes(patch[p:p + meta_size]).decode('utf-8', 'replace'))
    p += meta_size
    _check_output_size(fmt, tgt_size)
    _check_beat_source(fmt, src, src_size, src_crc, tgt_size, tgt_crc, info)

    out = bytearray(tgt_size)
    o = src_rel = tgt_rel = 0
    n_src = len(src)
    while p < end:
        cmd, p = _beat_number(patch, p, end, fmt)
        kind, length = cmd & 3, (cmd >> 2) + 1
        if o + length > tgt_size:
            raise _damaged(fmt, 'an action writes past the end of the output')
        if kind == 0:                                   # SourceRead
            if o + length > n_src:
                raise _damaged(fmt, 'SourceRead past the end of the ROM')
            out[o:o + length] = src[o:o + length]
        elif kind == 1:                                 # TargetRead
            if p + length > end:
                raise _damaged(fmt, 'TargetRead runs past the end')
            out[o:o + length] = patch[p:p + length]
            p += length
        else:
            d, p = _beat_number(patch, p, end, fmt)
            d = -(d >> 1) if d & 1 else d >> 1
            if kind == 2:                               # SourceCopy
                src_rel += d
                if src_rel < 0 or src_rel + length > n_src:
                    raise _damaged(fmt, 'SourceCopy outside the ROM')
                out[o:o + length] = src[src_rel:src_rel + length]
                src_rel += length
            else:                                       # TargetCopy
                tgt_rel += d
                if tgt_rel < 0 or tgt_rel >= o:
                    raise _damaged(fmt, 'TargetCopy from data not yet written')
                _copy_inside(out, tgt_rel, o, length)
                tgt_rel += length
        o += length
    if o != tgt_size:
        raise _damaged(fmt, 'the actions make %d bytes, not %d' % (o, tgt_size))
    if zlib.crc32(out) & 0xFFFFFFFF != tgt_crc:
        raise _damaged(fmt, "the patched ROM's checksum doesn't match")
    return out, info


def _apply_ups(patch, src):
    fmt = 'ups'
    in_crc, out_crc = _beat_footer(fmt, patch)
    end = len(patch) - 12
    p = 4
    in_size, p = _beat_number(patch, p, end, fmt)
    out_size, p = _beat_number(patch, p, end, fmt)
    info = _new_info(fmt, len(src))
    info.update(target_size=out_size, expected_source_size=in_size, expected_source_crc32=in_crc,
                target_crc32=out_crc)
    _check_output_size(fmt, out_size)
    _check_beat_source(fmt, src, in_size, in_crc, out_size, out_crc, info)

    out = bytearray(out_size)
    n = min(in_size, out_size)
    out[:n] = src[:n]               # bytes past the source read as 0, which out already holds
    pos = 0
    while p < end:
        skip, p = _beat_number(patch, p, end, fmt)
        pos += skip
        z = patch.find(b'\0', p, end)
        if z < 0:
            raise _damaged(fmt, 'an XOR run has no terminator')
        k = min(z - p, out_size - pos)
        if k > 0:                   # runs past the output are only there to reverse the patch
            x = int.from_bytes(out[pos:pos + k], 'big') ^ int.from_bytes(patch[p:p + k], 'big')
            out[pos:pos + k] = x.to_bytes(k, 'big')
        pos += z - p + 1
        p = z + 1
    if zlib.crc32(out) & 0xFFFFFFFF != out_crc:
        raise _damaged(fmt, "the patched ROM's checksum doesn't match")
    return out, info


# ------------------------------------------------------------------------------------------- IPS

def _apply_ips(patch, src):
    fmt = 'ips'
    info = _new_info(fmt, len(src))
    info['notes'].append("IPS patches don't record which ROM they are for, so the ROM wasn't checked.")
    out = bytearray(src)
    n = len(patch)
    p = 5
    while True:
        if p + 3 > n:
            raise _damaged(fmt, 'no EOF marker')
        if patch[p:p + 3] == b'EOF':
            p += 3
            break
        off = int.from_bytes(patch[p:p + 3], 'big')
        if p + 5 > n:
            raise _damaged(fmt, 'a record is cut off')
        size = int.from_bytes(patch[p + 3:p + 5], 'big')
        p += 5
        if size == 0:                                   # RLE record
            if p + 3 > n:
                raise _damaged(fmt, 'an RLE record is cut off')
            count = int.from_bytes(patch[p:p + 2], 'big')
            data = patch[p + 2:p + 3] * count
            p += 3
            if not count:
                continue
        else:
            if p + size > n:
                raise _damaged(fmt, 'a record is cut off')
            data = patch[p:p + size]
            p += size
        e = off + len(data)
        if e > len(out):
            _check_output_size(fmt, e)
            out.extend(bytes(e - len(out)))
        out[off:e] = data
    rest = n - p
    if rest == 3:                                       # truncate extension
        size = int.from_bytes(patch[p:p + 3], 'big')
        if size < len(out):                             # it only ever shortens (as Lunar IPS does)
            del out[size:]
            info['notes'].append('The patch cuts the file to %s.' % _size_text(size))
        else:
            info['notes'].append('Ignored 3 extra bytes after the end of the patch.')
    elif rest:
        info['notes'].append('Ignored %d extra bytes after the end of the patch.' % rest)
    info['target_size'] = len(out)
    return out, info


# ------------------------------------------------------------------------------------------- APS

def _apply_aps(patch, src):
    fmt = 'aps'
    n = len(patch)
    if n < 61:
        raise _damaged(fmt, 'too short')
    ptype, encoding = patch[5], patch[6]
    info = _new_info(fmt, len(src))
    info['description'] = bytes(patch[7:57]).decode('latin-1').rstrip(' \0')
    if encoding != 0:
        raise PatchError('This APS patch uses an encoding method (%d) this tool does not know.' % encoding,
                         'unsupported', fmt)
    if ptype == 1:                                      # N64 header
        if n < 78:
            raise _damaged(fmt, 'too short')
        if patch[57] not in (0, 1):
            raise _damaged(fmt, 'unknown ROM byte order %d' % patch[57])
        order = 'v64' if patch[57] == 0 else 'z64'
        cart, crc = bytes(patch[58:61]), bytes(patch[61:69])
        size = int.from_bytes(patch[74:78], 'little')
        p = 78
        have = n64_byte_order(src)
        if have is not None and have != order:
            src = memoryview(convert_byte_order(src, order))
            info['byte_order'] = order
            info['notes'].append('Your ROM was in .%s byte order; it was converted to .%s, the order '
                                 'this patch was made for, and the patched ROM is in that order.'
                                 % (have, order))
        info['patch_byte_order'] = order
        info.update(expected_cart_id=cart.decode('latin-1'), expected_header_crc=crc.hex().upper())
        # The header fields may be stored as they sit in the original file or in z64 order
        # (creators differ, and no spec settles it), so either one matches.
        got_cart, got_crc = bytes(src[0x3C:0x3F]), bytes(src[0x10:0x18])
        z_head = bytes(src[:0x40])
        if order != 'z64' and n64_byte_order(z_head) is not None:
            z_head = bytes(convert_byte_order(z_head, 'z64'))
        if (got_cart, got_crc) != (cart, crc) and (z_head[0x3C:0x3F], z_head[0x10:0x18]) != (cart, crc):
            raise _wrong_rom(fmt, 'cart ID %r with header CRC %s' % (cart.decode('latin-1'), crc.hex().upper()),
                             'cart ID %r with header CRC %s' % (got_cart.decode('latin-1'), got_crc.hex().upper()))
        info['source_checked'] = True
    elif ptype == 0:
        size = int.from_bytes(patch[57:61], 'little')
        p = 61
        info['notes'].append("This APS patch doesn't record which ROM it is for, so the ROM wasn't checked.")
    else:
        raise PatchError('This APS patch is of a type (%d) this tool does not know.' % ptype, 'unsupported', fmt)
    _check_output_size(fmt, size)
    info['target_size'] = size
    out = bytearray(size)
    k = min(size, len(src))
    out[:k] = src[:k]
    while p < n:
        if p + 5 > n:
            raise _damaged(fmt, 'a record is cut off')
        off = int.from_bytes(patch[p:p + 4], 'little')
        length = patch[p + 4]
        p += 5
        if length == 0:                                 # RLE record: byte, count
            if p + 2 > n:
                raise _damaged(fmt, 'an RLE record is cut off')
            data = patch[p:p + 1] * patch[p + 1]
            p += 2
        else:
            if p + length > n:
                raise _damaged(fmt, 'a record is cut off')
            data = patch[p:p + length]
            p += length
        if off + len(data) > size:
            raise _damaged(fmt, 'a record writes past the end of the output')
        out[off:off + len(data)] = data
    return out, info


# ---------------------------------------------------------------------------------------- VCDIFF

NOOP, ADD, RUN, COPY = 0, 1, 2, 3
VCD_DECOMPRESS, VCD_CODETABLE, VCD_APPHEADER = 1, 2, 4      # header indicator (APPHEADER: xdelta3)
VCD_SOURCE, VCD_TARGET, VCD_ADLER32 = 1, 2, 4               # window indicator (ADLER32: xdelta3)
NEAR_SIZE, SAME_SIZE = 4, 3                                 # default address cache
_SECONDARY = {1: 'DJW', 2: 'LZMA', 16: 'FGK'}               # xdelta3 secondary compressor IDs


def _default_code_table():
    """RFC 3284 section 5.6: 256 entries of (type1, size1, mode1, type2, size2, mode2)."""
    t = [(RUN, 0, 0, NOOP, 0, 0)]
    t += [(ADD, s, 0, NOOP, 0, 0) for s in range(18)]
    for m in range(9):
        t += [(COPY, s, m, NOOP, 0, 0) for s in [0] + list(range(4, 19))]
    for m in range(6):
        t += [(ADD, a, 0, COPY, c, m) for a in range(1, 5) for c in range(4, 7)]
    for m in range(6, 9):
        t += [(ADD, a, 0, COPY, 4, m) for a in range(1, 5)]
    t += [(COPY, 4, m, ADD, 1, 0) for m in range(9)]
    assert len(t) == 256
    return t


CODE_TABLE = _default_code_table()


def _vint(buf, p, end):
    """VCDIFF integer: 7 bits per byte, most significant first, high bit means more follow.
    Returns (value, p) or (None, p) when the bytes run out or the number is absurd."""
    v = 0
    for _ in range(10):
        if p >= end:
            return None, p
        b = buf[p]
        p += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, p
    return None, p


def _apply_vcdiff(patch, src):
    fmt = 'vcdiff'
    n = len(patch)
    info = _new_info(fmt, len(src))
    if n < 5:
        raise _damaged(fmt, 'too short')
    if patch[3] != 0:
        if patch[3] == 0x53:
            raise PatchError("This xdelta patch uses open-vcdiff's SDCH format, which this tool can't read. "
                             'Convert it to BPS or a plain xdelta3 patch.', 'unsupported', fmt)
        raise PatchError('This xdelta patch is VCDIFF version %d, which this tool does not know.' % patch[3],
                         'unsupported', fmt)
    hdr = patch[4]
    p = 5
    if hdr & ~(VCD_DECOMPRESS | VCD_CODETABLE | VCD_APPHEADER):
        raise _damaged(fmt, 'unknown header flags')
    secondary = None
    if hdr & VCD_DECOMPRESS:
        if p >= n:
            raise _damaged(fmt, 'too short')
        secondary = patch[p]
        p += 1
    if hdr & VCD_CODETABLE:
        raise PatchError('This xdelta patch uses a custom instruction table, which this tool can\'t read. '
                         'Convert it to BPS or a plain xdelta3 patch.', 'unsupported', fmt)
    if hdr & VCD_APPHEADER:
        size, p = _vint(patch, p, n)
        if size is None or p + size > n:
            raise _damaged(fmt, 'bad application header')
        info['app_header'] = bytes(patch[p:p + size]).decode('utf-8', 'replace')
        p += size

    out = bytearray()
    n_src = len(src)
    checked = unchecked = 0
    windows = 0
    table = CODE_TABLE
    while p < n:
        windows += 1
        wi = patch[p]
        p += 1
        if wi & ~(VCD_SOURCE | VCD_TARGET | VCD_ADLER32) or wi & 3 == 3:
            raise _damaged(fmt, 'unknown window flags')
        seg = b''
        seg_len = 0
        if wi & (VCD_SOURCE | VCD_TARGET):
            seg_len, p = _vint(patch, p, n)
            seg_pos, p = _vint(patch, p, n)
            if seg_len is None or seg_pos is None:
                raise _damaged(fmt, 'bad window header')
            if wi & VCD_SOURCE:
                if seg_pos + seg_len > n_src:
                    raise _wrong_rom(fmt, 'a ROM of at least %s' % _size_text(seg_pos + seg_len),
                                     _size_text(n_src))
                seg = src[seg_pos:seg_pos + seg_len]
            else:
                if seg_pos + seg_len > len(out):
                    raise _damaged(fmt, 'a window copies from output not yet made')
                seg = bytes(out[seg_pos:seg_pos + seg_len])
        dlen, p = _vint(patch, p, n)
        dstart = p
        tlen, p = _vint(patch, p, n)
        if dlen is None or tlen is None or p >= n:
            raise _damaged(fmt, 'bad window header')
        di = patch[p]
        p += 1
        data_len, p = _vint(patch, p, n)
        inst_len, p = _vint(patch, p, n)
        addr_len, p = _vint(patch, p, n)
        if data_len is None or inst_len is None or addr_len is None:
            raise _damaged(fmt, 'bad window header')
        adler = None
        if wi & VCD_ADLER32:
            if p + 4 > n:
                raise _damaged(fmt, 'bad window header')
            adler = int.from_bytes(patch[p:p + 4], 'big')
            p += 4
        if di:
            if di & ~7:
                raise _damaged(fmt, 'unknown window compression flags')
            if secondary is None:
                raise _damaged(fmt, 'a window says it is compressed but the patch names no compressor')
            name = _SECONDARY.get(secondary, 'unknown (ID %d)' % secondary)
            raise PatchError("This xdelta patch uses xdelta3's %s secondary compression, which this tool "
                             "can't read. Re-create it without it (xdelta3 -S none), or convert it to BPS."
                             % name, 'unsupported', fmt)
        data_p = p
        inst_p = data_end = data_p + data_len
        addr_p = inst_end = inst_p + inst_len
        addr_end = addr_p + addr_len
        if addr_end > n or addr_end - dstart != dlen:
            raise _damaged(fmt, 'a window runs past the end' if addr_end > n else 'a window length is wrong')
        _check_output_size(fmt, len(out) + tlen)

        win = bytearray(tlen)
        here = 0
        near = [0] * NEAR_SIZE
        same = [0] * (SAME_SIZE * 256)
        next_slot = 0
        while inst_p < inst_end:
            code = table[patch[inst_p]]
            inst_p += 1
            for t, size, mode in ((code[0], code[1], code[2]), (code[3], code[4], code[5])):
                if t == NOOP:
                    continue
                if size == 0:
                    size, inst_p = _vint(patch, inst_p, inst_end)
                    if size is None:
                        raise _damaged(fmt, 'bad instruction size')
                if here + size > tlen:
                    raise _damaged(fmt, 'an instruction writes past the end of a window')
                if t == ADD:
                    if data_p + size > data_end:
                        raise _damaged(fmt, 'ADD runs past the data section')
                    win[here:here + size] = patch[data_p:data_p + size]
                    data_p += size
                elif t == RUN:
                    if data_p >= data_end:
                        raise _damaged(fmt, 'RUN runs past the data section')
                    win[here:here + size] = patch[data_p:data_p + 1] * size
                    data_p += 1
                else:
                    cur = seg_len + here
                    if mode == 0:
                        a, addr_p = _vint(patch, addr_p, addr_end)
                    elif mode == 1:
                        a, addr_p = _vint(patch, addr_p, addr_end)
                        if a is not None:
                            a = cur - a
                    elif mode < 2 + NEAR_SIZE:
                        a, addr_p = _vint(patch, addr_p, addr_end)
                        if a is not None:
                            a += near[mode - 2]
                    elif mode < 2 + NEAR_SIZE + SAME_SIZE:
                        if addr_p >= addr_end:
                            a = None
                        else:
                            a = same[(mode - 2 - NEAR_SIZE) * 256 + patch[addr_p]]
                            addr_p += 1
                    else:
                        a = None
                    if a is None or a < 0 or a >= cur:
                        raise _damaged(fmt, 'bad COPY address')
                    near[next_slot] = a
                    next_slot = (next_slot + 1) % NEAR_SIZE
                    same[a % (SAME_SIZE * 256)] = a
                    k = size
                    if a < seg_len:                     # from the source segment
                        m = min(k, seg_len - a)
                        win[here:here + m] = seg[a:a + m]
                        k -= m
                        a = seg_len
                    if k:                               # from this window's output, may overlap
                        _copy_inside(win, a - seg_len, here + size - k, k)
                here += size
        if here != tlen or data_p != data_end or addr_p != addr_end:
            raise _damaged(fmt, "a window's sections don't add up")
        if adler is not None:
            if zlib.adler32(win) & 0xFFFFFFFF != adler:
                if wi & VCD_SOURCE:
                    raise PatchError("This patch is for a different ROM: the patched data didn't match the "
                                     'checksum stored in the patch. Use the stock ROM the hack was made for '
                                     '(or, if you are sure it is the right ROM, download the patch again).',
                                     'wrong-rom', fmt)
                raise _damaged(fmt, "a window's checksum doesn't match")
            if wi & VCD_SOURCE:
                checked += 1
        elif wi & VCD_SOURCE:
            unchecked += 1
        out += win
        p = addr_end
    if not windows:
        raise _damaged(fmt, 'it holds no data')
    info['target_size'] = len(out)
    info['windows'] = windows
    info['source_checked'] = checked > 0
    if secondary is not None:
        info['notes'].append("The patch names xdelta3's %s compressor but doesn't use it."
                             % _SECONDARY.get(secondary, 'unknown'))
    if unchecked:
        info['notes'].append("Parts of this xdelta patch carry no checksum, so the ROM couldn't be fully "
                             "checked." if checked else
                             "This xdelta patch carries no checksums, so the ROM couldn't be checked.")
    return out, info


# -------------------------------------------------------------------------------------- apply

_APPLY = {'vcdiff': _apply_vcdiff, 'bps': _apply_bps, 'ips': _apply_ips, 'ups': _apply_ups,
          'aps': _apply_aps}


def _apply_once(fn, fmt, patch, src):
    try:
        return fn(patch, src)
    except PatchError:
        raise
    except (IndexError, ValueError, OverflowError):
        raise _damaged(fmt)
    except MemoryError:
        raise PatchError('There is not enough memory to apply this %s patch; the file it makes is too '
                         'large (or the patch is damaged).' % FORMAT_NAMES[fmt], 'too-large', fmt)


def apply(patch, source, fmt=None):
    """Apply `patch` to the ROM bytes `source`. Returns (target bytearray, info dict); raises
    PatchError. `fmt` overrides detection ('vcdiff', 'bps', 'ips', 'ups' or 'aps')."""
    patch = bytes(patch)
    src = _view(source)
    if fmt is None:
        fmt = detect(patch)
    if fmt is None:
        if patch[:4] == b'APS1':
            raise PatchError('This is a Game Boy Advance APS patch, not an N64 one.', 'unknown-format')
        raise PatchError("This file isn't a ROM patch this tool knows (xdelta, BPS, IPS, UPS or N64 APS).",
                         'unknown-format')
    if fmt not in _APPLY:
        raise ValueError('unknown patch format %r' % (fmt,))
    if detect(patch) != fmt:
        raise PatchError("This file isn't a %s patch." % FORMAT_NAMES[fmt], 'unknown-format', fmt)
    fn = _APPLY[fmt]
    have = n64_byte_order(src)
    try:
        out, info = _apply_once(fn, fmt, patch, src)
    except PatchError as e:
        if e.kind != 'wrong-rom' or fmt == 'aps' or have is None:
            raise
        first = e
    else:
        if not info['source_checked'] and have in ('v64', 'n64') and 'byte_order' not in info:
            info['unchecked_byte_order'] = have
            info['notes'].append('Your ROM is in .%s byte order and this patch can\'t check the ROM. Most '
                                 'hacks are made for .z64 ROMs; if the game misbehaves, convert your ROM '
                                 'to .z64 and patch it again.' % have)
        return out, info
    for order in ('z64', 'v64', 'n64'):            # a checked ROM in another byte order
        if order == have:
            continue
        try:
            out, info = _apply_once(fn, fmt, patch, memoryview(convert_byte_order(src, order)))
        except PatchError as e:
            if e.kind != 'wrong-rom':
                raise                              # the order matched; this is the real problem
            continue
        info['byte_order'] = order
        info['notes'].append('Your ROM was in .%s byte order; it was converted to .%s, the order this '
                             'patch was made for, and the patched ROM is in that order.' % (have, order))
        return out, info
    raise first
