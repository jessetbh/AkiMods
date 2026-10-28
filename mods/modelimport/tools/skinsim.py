"""Offline replay of a welded AKP1 pack under poses captured from the game (probe lines: 'probe <frame> <part> M0|M1
<16 words>'), rigid or skinned exactly as the runtime draws it. Scores triangle stretch vs the rest shape (in the
pelvis's frame, model units) and renders the worst pose.

    python skinsim.py <pose stderr.txt> <pack.akimodel> [out.png]
"""
import re
import struct
import sys

import numpy as np

poses_file, pack = sys.argv[1], sys.argv[2]
out_png = sys.argv[3] if len(sys.argv) > 3 else None


def fixed(words):
    out = np.zeros(16)
    for e in range(16):
        k, h = e // 2, e % 2
        i = (words[k] >> (16 * (1 - h))) & 0xFFFF
        f = (words[8 + k] >> (16 * (1 - h))) & 0xFFFF
        v = (i << 16) | f
        out[e] = (v - (1 << 32) if v & 0x80000000 else v) / 65536.0
    return out.reshape(4, 4)


poses = {}
for line in open(poses_file, encoding="utf-8", errors="replace"):
    # The runtime's Log poses lines ("pose <frame> <slot> <part> M0 ..."; slot 2 here), or the older probe's.
    m = re.search(r"(?:pose (\d+) 2|probe (\d+)) (\d+) M0((?: [0-9A-F]{8}){16})", line)
    if m:
        poses.setdefault(int(m.group(1) or m.group(2)), {})[int(m.group(3))] = fixed([int(x, 16) for x in m.group(4).split()])

d = open(pack + "/model.bin", "rb").read()
h = struct.unpack_from(">IHH8sIIIIIIIIIIII", d, 0)
built, eoff, soff, scount = h[4], h[7], (h[14] if h[1] >= 2 else 0), (h[15] if h[1] >= 2 else 0)
phys = built - 0x80000000
_, dl = struct.unpack_from(">II", d, eoff)
skin = []
for i in range(scount):
    e = soff + i * 48
    inf = [struct.unpack_from(">BBhhh", d, e + 16 + k * 8) for k in range(4)]
    skin.append(([(p, w / 255.0) for p, w, *_ in inf if w], np.array(inf[0][2:], float)))

# Walk the list: each cache slot -> (rest xyz, [(part, weight)]).
cache = [None] * 32
part = None
tris = []
o = dl
while o < len(d):
    w0, w1 = struct.unpack_from(">II", d, o)
    op = w0 >> 24
    o += 8
    if op == 0xDC and (w1 >> 24) == 0x0D:
        part = ((w1 & 0xFFFFFF) - 0x38) // 0xB8
    elif op == 0x01:
        n = (w0 >> 12) & 0xFF
        v0 = ((w0 & 0xFF) >> 1) - n
        for k in range(n):
            if (w1 >> 24) == 0x0E:
                infl, pos = skin[(w1 & 0xFFFFFF) // 16 + k]
                cache[v0 + k] = (pos, infl)
            else:
                a = w1 - phys + k * 16
                cache[v0 + k] = (np.array(struct.unpack_from(">hhh", d, a), float), [(part, 1.0)])
    elif op in (0x05, 0x06):
        for w in [w0 & 0xFFFFFF] + ([w1] if op == 0x06 else []):
            tris.append(tuple(cache[((w >> sh) & 0xFF) // 2] for sh in (16, 8, 0)))
    elif op == 0xDF:
        break

rest = np.array([[v[0] for v in t] for t in tris])                    # T x 3 x 3
rest_edges = np.linalg.norm(rest - np.roll(rest, 1, axis=1), axis=2)  # T x 3


def posed(M):
    """Triangles' vertices in the pelvis's frame (model units) for one pose's matrices."""
    inv1 = np.linalg.inv(M[1])
    out = np.zeros_like(rest)
    for ti, t in enumerate(tris):
        for vi, (p, infl) in enumerate(t):
            c = sum(w * (np.append(p, 1.0) @ M[q]) for q, w in infl if q in M)
            l = c @ inv1
            out[ti, vi] = l[:3] / l[3]
    return out


scores = []
worst = (0, None, None)
for f in sorted(poses):
    M = poses[f]
    if 1 not in M:
        continue
    pv = posed(M)
    edges = np.linalg.norm(pv - np.roll(pv, 1, axis=1), axis=2)
    ratio = (edges / np.maximum(rest_edges, 1e-3)).max(axis=1)
    bad = float((ratio > 2.0).mean())
    scores.append((bad, ratio.max()))
    if bad > worst[0]:
        worst = (bad, f, pv)
b = np.array(scores)
print("%s: %d triangles, %d poses: stretched >2x: mean %.1f%%, worst pose %.1f%%; max stretch median %.1fx"
      % (pack.replace("\\", "/").split("/")[-1], len(tris), len(b), 100 * b[:, 0].mean(), 100 * b[:, 0].max(),
         np.median(b[:, 1])))

if out_png and worst[2] is not None:
    from PIL import Image, ImageDraw
    pv, f = worst[2], worst[1]
    img = Image.new("RGB", (1000, 560), (24, 24, 30))
    dr = ImageDraw.Draw(img)
    for view in (0, 1):
        ox = view * 500
        for ti in range(len(tris)):
            e = np.linalg.norm(pv[ti] - np.roll(pv[ti], 1, axis=0), axis=1)
            r = (e / np.maximum(rest_edges[ti], 1e-3)).max()
            col = (230, 70, 70) if r > 2 else (200, 170, 120)
            pts = [(ox + 250 + (p[0] if view == 0 else p[2]) * 1.6, 330 - p[1] * 1.6) for p in pv[ti]]
            dr.polygon(pts, outline=col)
    dr.text((8, 8), "frame %d: red = stretched >2x (front | side, pelvis frame)" % f, fill=(255, 255, 255))
    img.save(out_png)
