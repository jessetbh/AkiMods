"""Solid renders of packs at the same captured game pose, through the game's own matrices (what the RSP would draw,
minus textures): packs side by side per pose, faces shaded by their normal, painter-sorted.

    python skinshot.py <pose stderr.txt> <out.png> <frame,frame,...> <label=pack> [<label=pack> ...]
"""
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.argv, args = sys.argv[:3], sys.argv
poses_file = args[1]
out_png, frames = args[2], [int(f) for f in args[3].split(",")]
packs = [a.split("=", 1) for a in args[4:]]

src = open(__file__.replace("skinshot.py", "skinsim.py"), encoding="utf-8").read()
head = src.split("d = open(pack")[0]           # fixed() and the pose parser
g = {"sys": sys, "np": np, "__name__": "x"}
sys.argv = ["x", poses_file, "unused"]
exec(head, g)
poses = g["poses"]

W, H = 420, 520


def load(pack):
    g2 = dict(g)
    g2["pack"] = pack
    body = src.split("d = open(pack")[1].split("rest = np.array")[0]
    exec("d = open(pack" + body, g2)
    return g2["tris"]


def render(tris, M):
    clip = []
    for t in tris:
        tv = []
        for p, infl in t:
            c = sum(w * (np.append(p, 1.0) @ M[q]) for q, w in infl if q in M)
            tv.append(c)
        clip.append(tv)
    clip = np.array(clip)                    # T x 3 x 4
    ndc = clip[..., :3] / clip[..., 3:4]
    pts = ndc[..., :2]
    lo, hi = pts.reshape(-1, 2).min(0), pts.reshape(-1, 2).max(0)
    return ndc, lo, hi


img = Image.new("RGB", (W * len(packs), H * len(frames)), (26, 26, 32))
dr = ImageDraw.Draw(img)
loaded = [(lab, load(pk)) for lab, pk in packs]
for fi, f in enumerate(frames):
    M = poses[f]
    views = [render(tris, M) for _, tris in loaded]
    lo = np.min([v[1] for v in views], axis=0)
    hi = np.max([v[2] for v in views], axis=0)
    sc = 0.9 * min(W / (hi[0] - lo[0]), H / (hi[1] - lo[1]))
    for pi, ((lab, tris), (ndc, _, _)) in enumerate(zip(loaded, views)):
        ox, oy = pi * W, fi * H
        order = np.argsort(-ndc[..., 2].mean(1))   # far first
        for ti in order:
            a, b, c = ndc[ti]
            n = np.cross(b - a, c - a)
            nn = n / (np.linalg.norm(n) + 1e-9)
            shade = 0.3 + 0.7 * abs(nn @ np.array([0.45, 0.6, 0.66]))
            col = tuple(int(v * shade) for v in (215, 160, 110))
            xy = [(ox + W / 2 + (p[0] - (lo[0] + hi[0]) / 2) * sc, oy + H / 2 - (p[1] - (lo[1] + hi[1]) / 2) * sc)
                  for p in (a, b, c)]
            dr.polygon(xy, fill=col)
        dr.text((ox + 8, oy + 8), "%s  (pose %d)" % (lab, f), fill=(255, 230, 120))
img.save(out_png)
print(out_png)
