"""A stand-in for patchfmt.py (aki-roadmap#228) until Worker Ant's module lands: the same API, fewer formats.

    detect(patch) -> 'bps' | 'ips' | 'ups' | 'aps' | 'xdelta' | 'rup' | 'ppf' | None
    apply(patch, source) -> (target bytes, info dict); raises PatchError

Applies BPS, IPS and UPS. The other formats are recognised and refused with a PatchError (aki_import says which).
A PatchError for a source that doesn't match carries source_crc32 (BPS, UPS) so the importer can name the base it
wants. aki_import uses patchfmt.py when it is there and this file only otherwise. Stdlib only.
"""
import zlib

MAGIC = ((b'BPS1', 'bps'), (b'UPS1', 'ups'), (b'PATCH', 'ips'), (b'APS10', 'aps'), (b'\xd6\xc3\xc4', 'xdelta'),
         (b'NINJA', 'rup'), (b'PPF', 'ppf'))
MAX_TARGET = 128 << 20      # nothing an N64 patch makes is bigger


class PatchError(Exception):
    def __init__(self, msg, source_crc32=None):
        super().__init__(msg)
        self.source_crc32 = source_crc32


def detect(patch):
    for magic, fmt in MAGIC:
        if patch.startswith(magic):
            return fmt
    return None


def apply(patch, source):
    fmt = detect(patch)
    if fmt == 'bps':
        return _bps(patch, source), {'format': 'bps'}
    if fmt == 'ups':
        return _ups(patch, source), {'format': 'ups'}
    if fmt == 'ips':
        return _ips(patch, source), {'format': 'ips'}
    if fmt is None:
        raise PatchError('not a patch file')
    raise PatchError('%s patches need patchfmt.py (this stand-in applies BPS, IPS and UPS)' % fmt)


def _crc(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def _check_footer(patch, source, kind):
    if len(patch) < 12 + 4:
        raise PatchError('%s patch too short' % kind)
    src_crc = int.from_bytes(patch[-12:-8], 'little')
    if _crc(patch[:-4]) != int.from_bytes(patch[-4:], 'little'):
        raise PatchError('%s patch is damaged (its own checksum)' % kind)
    if _crc(source) != src_crc:
        raise PatchError('%s patch is for another ROM (source CRC32 %08X)' % (kind, src_crc), source_crc32=src_crc)
    return int.from_bytes(patch[-8:-4], 'little')


def _number(patch, p, kind):
    data, shift = 0, 1
    while True:
        if p >= len(patch):
            raise PatchError('%s patch is truncated' % kind)
        x = patch[p]
        p += 1
        data += (x & 0x7F) * shift
        if x & 0x80:
            return data, p
        shift <<= 7
        data += shift
        if shift > 1 << 64:
            raise PatchError('%s patch has a bad number' % kind)


def _bps(patch, source):
    tgt_crc = _check_footer(patch, source, 'BPS')
    p = 4
    src_size, p = _number(patch, p, 'BPS')
    tgt_size, p = _number(patch, p, 'BPS')
    meta, p = _number(patch, p, 'BPS')
    p += meta
    if src_size != len(source) or tgt_size > MAX_TARGET:
        raise PatchError('BPS sizes do not match the ROM')
    out = bytearray()
    src_rel = tgt_rel = 0
    end = len(patch) - 12
    while p < end:
        cmd, p = _number(patch, p, 'BPS')
        kind, length = cmd & 3, (cmd >> 2) + 1
        if len(out) + length > tgt_size:
            raise PatchError('BPS patch writes past its target size')
        if kind == 0:
            out += source[len(out):len(out) + length]
        elif kind == 1:
            out += patch[p:p + length]
            p += length
        else:
            off, p = _number(patch, p, 'BPS')
            off = (-1 if off & 1 else 1) * (off >> 1)
            if kind == 2:
                src_rel += off
                if src_rel < 0 or src_rel + length > len(source):
                    raise PatchError('BPS SourceCopy outside the ROM')
                out += source[src_rel:src_rel + length]
                src_rel += length
            else:
                tgt_rel += off
                if tgt_rel < 0 or tgt_rel >= len(out):
                    raise PatchError('BPS TargetCopy outside the output')
                for _ in range(length):
                    out.append(out[tgt_rel])
                    tgt_rel += 1
    if len(out) != tgt_size or _crc(out) != tgt_crc:
        raise PatchError('BPS result does not match its checksum')
    return bytes(out)


def _ups(patch, source):
    tgt_crc = _check_footer(patch, source, 'UPS')
    p = 4
    src_size, p = _number(patch, p, 'UPS')
    tgt_size, p = _number(patch, p, 'UPS')
    if src_size != len(source) or tgt_size > MAX_TARGET:
        raise PatchError('UPS sizes do not match the ROM')
    out = bytearray(source[:tgt_size].ljust(tgt_size, b'\0'))
    pos, end = 0, len(patch) - 12
    while p < end:
        skip, p = _number(patch, p, 'UPS')
        pos += skip
        while p < end and patch[p] != 0:
            if pos < tgt_size:
                out[pos] ^= patch[p]
            pos += 1
            p += 1
        p += 1
        pos += 1
    if _crc(out) != tgt_crc:
        raise PatchError('UPS result does not match its checksum')
    return bytes(out)


def _ips(patch, source):
    """IPS (and IPS32-less 'PATCH' files): records of offset(3) size(2) data, size 0 = RLE; 'EOF', then an optional
    3-byte truncation length. IPS has no source check: it applies to anything."""
    out = bytearray(source)
    p = 5
    while True:
        if p + 3 > len(patch):
            raise PatchError('IPS patch is truncated (no EOF)')
        if patch[p:p + 3] == b'EOF':
            p += 3
            if len(patch) >= p + 3:
                cut = int.from_bytes(patch[p:p + 3], 'big')
                del out[cut:]
            break
        if p + 5 > len(patch):
            raise PatchError('IPS record is truncated')
        off = int.from_bytes(patch[p:p + 3], 'big')
        size = int.from_bytes(patch[p + 3:p + 5], 'big')
        p += 5
        if size == 0:
            if p + 3 > len(patch):
                raise PatchError('IPS RLE record is truncated')
            count, value = int.from_bytes(patch[p:p + 2], 'big'), patch[p + 2]
            p += 3
            data = bytes([value]) * count
        else:
            data = patch[p:p + size]
            if len(data) != size:
                raise PatchError('IPS record is truncated')
            p += size
        if off + len(data) > MAX_TARGET:
            raise PatchError('IPS record writes past 128 MB')
        if off > len(out):
            out += b'\0' * (off - len(out))
        out[off:off + len(data)] = data
    return bytes(out)
