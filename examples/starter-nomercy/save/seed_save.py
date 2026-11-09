#!/usr/bin/env python3
"""seed_save.py - makes the AKI starter kit's seed save for WWF No Mercy (USA Rev 1). CC0 1.0.

Why a script and not a save file: a No Mercy save holds data the game builds from the ROM (default attire, default
movesets), so a save file can't be shared under CC0. The kit ships this recipe instead, and kit.py runs it on a blank
save from YOUR game (start the game once, then use its saves\wwf.nomercy.us.bin before you play).

Input : a blank/fresh No Mercy cart flash save (port file saves/wwf.nomercy.us.bin,
        128 KB, raw big-endian) made by the player's own game.
Output: the same save with ONE change: the unlock table is set to "everything unlocked".

    python seed_save.py <blank wwf.nomercy.us.bin> <out.bin>

The edit (see apply_seed_edit): save image bytes 0x6B67..0x6B7C (22 bytes) = 0xFF.
That image range is the persisted copy of the Rev 1 unlock bit table at RAM 0x800BD910
(bits 0..0xAE, tested by func_800D9A5C, set by func_800D9A10). func_800E2C7C copies
image+0x6B67 -> 0x800BD910 (0x19 bytes) after the flash is read at boot (func_800E49FC);
func_800E2C44 copies it back and commits (func_80004EE8). It is the same 22 bytes the
in-game-tested cheat "50000B02 0000 810BD910 FFFF" writes. The 3 spare bytes
0x6B7D..0x6B7F are left as they are. No checksum other than the per-page flash XOR
covers the image.

Flash container (decoded in jessetbh/aki-roadmap#51): pages of 0x80 bytes:
+0 u16 link, +2 u16 chunk index (0x4000 = header), +4 u32 XOR checksum, +8 0x78 payload.
The image is 0x7170 bytes = 242 chunks. The output is a compacted log: chunks 0..241 on
pages 1..242 (chunk 0 link 0x2000, each later page linking to the previous page), header
on page 243 linking to page 242 with tail=1, head=243, generation = old+1. All other bytes
are 0xFF. The game reads it like its own saves (its fresh save also keeps an old generation-1 header on page 0;
this one doesn't, which the game doesn't need).
"""
import struct
import sys

PAGE = 0x80
PAGES = 0x3FE               # pages the driver uses (index < 0x3FE)
FLASH_SIZE = 0x20000
PAYLOAD = 0x78
IMAGE = 0x7170
CHUNKS = IMAGE // PAYLOAD   # 242
HDR_TYPE = 0x4000
CHAIN_END = 0x2000

UNLOCK_OFF = 0x6B67         # image copy of RAM 0x800BD910 (0x19 bytes saved)
UNLOCK_LEN = 22             # 175 flag bits used (ids 0..0xAE) -> 22 bytes, as the tested cheat


def page_sum(pg):
    w = list(struct.unpack('>32I', pg))
    w[1] = 0
    c = 0xFFFFFFFF
    for x in w:
        c ^= x
    return c


def seal(pg):
    pg = bytearray(pg)
    struct.pack_into('>I', pg, 4, page_sum(bytes(pg)))
    return bytes(pg)


def is_header(pg):
    return (struct.unpack_from('>H', pg, 2)[0] == HDR_TYPE and pg[0x10:0x18] == b'WWF2 AKI'
            and struct.unpack_from('>I', pg, 0x18)[0] == 0x00080721 and pg[0x20:0x28] == b'NO MERCY')


def parse(flash):
    """Return (image, header page bytes, generation) from the newest valid header."""
    if len(flash) != FLASH_SIZE:
        raise ValueError('expected a 128 KB No Mercy flash save, got %d bytes' % len(flash))
    best = None
    for i in range(PAGES):
        pg = flash[i * PAGE:(i + 1) * PAGE]
        if struct.unpack_from('>I', pg, 4)[0] != page_sum(pg) or not is_header(pg):
            continue
        gen = struct.unpack_from('>Q', pg, 8)[0]
        if best is None or gen > best[0]:
            best = (gen, i, pg)
    if best is None:
        raise ValueError('no valid No Mercy header page')
    gen, hpage, hdr = best
    chunks, p, steps = {}, struct.unpack_from('>H', hdr, 0)[0], 0
    while steps <= PAGES:
        if p >= PAGES:
            raise ValueError('chunk chain leaves the flash at page %#x' % p)
        pg = flash[p * PAGE:(p + 1) * PAGE]
        link, idx = struct.unpack_from('>HH', pg)
        if idx & 0xF000 or struct.unpack_from('>I', pg, 4)[0] != page_sum(pg):
            raise ValueError('broken chunk chain at page %#x' % p)
        chunks.setdefault(idx, pg[8:])          # newest copy is met first
        if link == CHAIN_END:
            break
        p, steps = link, steps + 1
    missing = [i for i in range(CHUNKS) if i not in chunks]
    if missing:
        raise ValueError('chunks missing from the chain: %s' % missing[:8])
    img = b''.join(chunks[i] for i in range(CHUNKS))
    return img, hdr, gen


def build(img, src_hdr, gen):
    """Compacted log: chunks on pages 1..242, header on page 243."""
    assert len(img) == IMAGE
    out = bytearray(b'\xff' * FLASH_SIZE)
    for i in range(CHUNKS):
        page = i + 1
        pg = struct.pack('>HHI', CHAIN_END if i == 0 else page - 1, i, 0) + img[i * PAYLOAD:(i + 1) * PAYLOAD]
        out[page * PAGE:(page + 1) * PAGE] = seal(pg)
    hpage = CHUNKS + 1                          # 243
    hdr = bytearray(src_hdr)                    # keep magic/version bytes as the game wrote them
    struct.pack_into('>HHI', hdr, 0, CHUNKS, HDR_TYPE, 0)      # link -> page 242
    struct.pack_into('>Q', hdr, 8, gen)
    struct.pack_into('>HH', hdr, 0x1C, 1, hpage)               # tail, head
    struct.pack_into('>H', hdr, 0x28, CHUNKS)                  # chunk count
    out[hpage * PAGE:(hpage + 1) * PAGE] = seal(bytes(hdr))
    return bytes(out)


def edit_image(img):
    img = bytearray(img)
    img[UNLOCK_OFF:UNLOCK_OFF + UNLOCK_LEN] = b'\xff' * UNLOCK_LEN
    return bytes(img)


def apply_seed_edit(flash_bytes):
    """Blank No Mercy flash save -> seed save (everything unlocked). Self-checks the result."""
    img, hdr, gen = parse(flash_bytes)
    new_img = edit_image(img)
    out = build(new_img, hdr, gen + 1)
    # self-check: re-parse the output, compare images byte for byte
    chk_img, chk_hdr, chk_gen = parse(out)
    if chk_gen != gen + 1 or chk_img != new_img:
        raise AssertionError('re-parse of the written log does not match')
    changed = [i for i in range(IMAGE) if chk_img[i] != img[i]]
    allowed = set(range(UNLOCK_OFF, UNLOCK_OFF + UNLOCK_LEN))
    if not set(changed) <= allowed:
        raise AssertionError('bytes outside the unlock table changed: %s' % [hex(i) for i in changed if i not in allowed][:8])
    for i in range(PAGES):                      # every written page carries a valid sum
        pg = out[i * PAGE:(i + 1) * PAGE]
        if pg != b'\xff' * PAGE and struct.unpack_from('>I', pg, 4)[0] != page_sum(pg):
            raise AssertionError('bad page sum at page %#x' % i)
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src = open(sys.argv[1], 'rb').read()
    img, _, gen = parse(src)
    before = img[UNLOCK_OFF:UNLOCK_OFF + 0x19]
    out = apply_seed_edit(src)
    after = parse(out)[0][UNLOCK_OFF:UNLOCK_OFF + 0x19]
    n = sum(1 for a, b in zip(parse(out)[0], img) if a != b)
    open(sys.argv[2], 'wb').write(out)
    print('generation %d -> %d; %d image bytes changed (unlock table 0x%04X..0x%04X)'
          % (gen, gen + 1, n, UNLOCK_OFF, UNLOCK_OFF + UNLOCK_LEN - 1))
    print('unlock bytes before:', before.hex())
    print('unlock bytes after: ', after.hex())
    print('wrote', sys.argv[2])


if __name__ == '__main__':
    main()
