"""AKI Starter Kit: draws the kit's three replacement textures from code (CC0 1.0).

    python make_textures.py OUT_DIR

Writes, into OUT_DIR:
    1b97a9b8d43d12a1.png   the ring mat canvas (256x256)
    3ff838b666927262.png   the apron logo banner (512x128)
    65fa3870105272fc.png   the turnbuckle pad covers (256x128)
    rt64.json              the pack database: "autoPath": "rt64", so each file replaces the texture whose rt64 hash
                           is its name, with no entry per texture

Everything here is drawn by this script: shapes, a seeded noise weave and a 5x7 block font defined below. No game
art, no font file. On the same toolchain (Python, Pillow, zlib) the script always gives the same bytes (fixed seed,
no timestamps), so the build can pin the pack's sha256.

How the hashes were found: each name is the rt64 hash of the texture it replaces, from a texture dump of the Raw
arena (Settings > Graphics > Texture dump in the AKI ports, or WCW_TEX_DUMP). A replacement can be any size: keep
the original's aspect ratio (here 4x: 64x64 -> 256x256, 128x32 -> 512x128, 64x32 -> 256x128) and the game draws
it in the same place.
"""
import json
import math
import os
import sys

from PIL import Image, ImageDraw

SEED = 227

# 5x7 block font: the letters this kit writes.
FONT = {
    'A': ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
    'E': ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
    'I': ['11111', '00100', '00100', '00100', '00100', '00100', '11111'],
    'K': ['10001', '10010', '10100', '11000', '10100', '10010', '10001'],
    'R': ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
    'S': ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
    'T': ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
    ' ': ['00000'] * 7,
}


def rng(seed):
    # A small LCG, so the noise doesn't depend on Python's random module version.
    state = seed & 0xFFFFFFFF
    while True:
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        yield state >> 24


def text(draw, s, x, y, cell, fill, shadow=None):
    """Draws s in the block font with its top-left at (x, y), each font pixel a cell x cell square."""
    for ch in s:
        rows = FONT[ch]
        for ry, row in enumerate(rows):
            for rx, bit in enumerate(row):
                if bit == '1':
                    x0, y0 = x + rx * cell, y + ry * cell
                    if shadow:
                        draw.rectangle([x0 + cell // 3, y0 + cell // 3, x0 + cell // 3 + cell - 1, y0 + cell // 3 + cell - 1], fill=shadow)
                    draw.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=fill)
        x += 6 * cell


def text_width(s, cell):
    return len(s) * 6 * cell - cell


def ring_mat():
    # Teal canvas with a light woven grain and a white double border along the top and left edges: the game draws
    # this texture once per quarter of the ring, so the border lands on the ring's outer edge.
    w = h = 256
    img = Image.new('RGBA', (w, h))
    px = img.load()
    noise = rng(SEED)
    for y in range(h):
        for x in range(w):
            weave = 6 if ((x // 4) + (y // 4)) % 2 == 0 else -6
            n = next(noise) % 13 - 6
            px[x, y] = (max(0, 24 + weave + n), max(0, 112 + weave + n), max(0, 118 + weave + n), 255)
    d = ImageDraw.Draw(img)
    for off in (10, 22):
        d.rectangle([off, off, w - 1, off + 3], fill=(236, 240, 238, 255))
        d.rectangle([off, off, off + 3, h - 1], fill=(236, 240, 238, 255))
    return img


def apron_logo():
    # "AKI STARTER KIT" in the block font, white with a violet drop shadow, on a dark slate banner with teal rules.
    w, h = 512, 128
    img = Image.new('RGBA', (w, h), (28, 30, 44, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 8, w - 1, 13], fill=(40, 190, 180, 255))
    d.rectangle([0, h - 14, w - 1, h - 9], fill=(40, 190, 180, 255))
    s, cell = 'AKI STARTER KIT', 5
    x = (w - text_width(s, cell)) // 2
    y = (h - 7 * cell) // 2
    text(d, s, x, y, cell, fill=(245, 245, 250, 255), shadow=(120, 70, 200, 255))
    return img


def turnbuckle_pads():
    # Two pad covers side by side (the game uses each half on a different pad): violet with a teal five-point star,
    # and teal with a violet one.
    w, h = 256, 128
    img = Image.new('RGBA', (w, h))
    d = ImageDraw.Draw(img)
    halves = [((110, 60, 190, 255), (40, 200, 190, 255)), ((40, 170, 165, 255), (125, 75, 205, 255))]
    for i, (bg, fg) in enumerate(halves):
        x0 = i * 128
        d.rectangle([x0, 0, x0 + 127, h - 1], fill=bg)
        d.rectangle([x0 + 4, 4, x0 + 123, h - 5], outline=(235, 235, 240, 255), width=3)
        cx, cy, r_out, r_in = x0 + 64, 64, 42, 17
        pts = []
        for k in range(10):
            a = -math.pi / 2 + k * math.pi / 5
            r = r_out if k % 2 == 0 else r_in
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        d.polygon(pts, fill=fg, outline=(245, 245, 250, 255))
    return img


TEXTURES = {
    '1b97a9b8d43d12a1': ring_mat,
    '3ff838b666927262': apron_logo,
    '65fa3870105272fc': turnbuckle_pads,
}

RT64_JSON = {
    'configuration': {'configurationVersion': 3, 'hashVersion': 5, 'autoPath': 'rt64', 'defaultShift': 'none'},
    'textures': [],
}


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = argv[1]
    os.makedirs(out, exist_ok=True)
    for name, make in TEXTURES.items():
        make().save(os.path.join(out, name + '.png'), optimize=False)
    with open(os.path.join(out, 'rt64.json'), 'w', newline='\n') as f:
        json.dump(RT64_JSON, f, indent=1)
        f.write('\n')
    print('wrote %d textures + rt64.json to %s' % (len(TEXTURES), out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
