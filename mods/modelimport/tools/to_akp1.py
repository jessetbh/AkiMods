"""Adds the AKP1 runtime header (aki-roadmap#130, runtime contract v1) to an aki-model output that doesn't have one yet,
until aki-model writes it itself. Also the reference for the header layout.

    python to_akp1.py <in>.akimodel <mods folder>/models/<id>.akimodel

Copies manifest.json, pack.json and preview*.png, and writes model.bin = 64-byte header + the old model.bin. The data
doesn't move: the header's built base is the old base - 64, so every address in it stays right, and the relocation
offsets and list offsets grow by 64.
"""
import json
import pathlib
import shutil
import struct
import sys

MAGIC, VERSION, SIZE = 0x414B5031, 1, 64
WELDED = 0xFFFFFFFF
FLAG_WELDED, FLAG_TLUT, FLAG_CULL = 1, 2, 4


def header_and_tables(manifest, data_len):
    base = int(manifest["base"], 16)
    welded = manifest.get("matrices") == "segment 0xD"
    if welded:
        entries = [(WELDED, int(manifest["parts"][0]["dl_address"], 16) - base)]
    else:
        entries = [(p["index"], int(p["dl_address"], 16) - base) for p in manifest["parts"] if p.get("index") is not None]
    relocs = list(manifest.get("relocations", []))
    tables = b"".join(struct.pack(">II", part, off + SIZE) for part, off in entries)
    tables += b"".join(struct.pack(">I", off + SIZE) for off in relocs)
    eoff = SIZE + data_len
    while eoff % 8:
        eoff += 1
    roff = eoff + 8 * len(entries)
    flags = (FLAG_WELDED if welded else 0) | (FLAG_TLUT if any(t.get("format") == "CI4" for t in manifest.get("texture_tiles", [])) else 0)
    if manifest.get("winding") == "normals" and not manifest.get("double_sided"):
        flags |= FLAG_CULL   # aki-model turned every triangle to face its normals: back faces can be culled
    size = roff + 4 * len(relocs)
    profile = manifest.get("profile") or manifest.get("game") or "nomercy"
    head = struct.pack(">IHH8sIIIIIIIIII8x", MAGIC, VERSION, SIZE, profile.encode()[:8], base - SIZE, size, flags,
                       eoff, len(entries), roff, len(relocs), 0, 0, 1)
    assert len(head) == SIZE
    return head, eoff, tables, size


def main():
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    data = (src / "model.bin").read_bytes()
    if data[:4] == b"AKP1":
        sys.exit(f"{src}: already has an AKP1 header")
    head, eoff, tables, size = header_and_tables(manifest, len(data))
    out = head + data + b"\0" * (eoff - SIZE - len(data)) + tables
    assert len(out) == size
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "model.bin").write_bytes(out)
    for name in ["manifest.json", "pack.json"] + [p.name for p in src.glob("preview*.png")]:
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    print(f"{dst}: {size} bytes, built base 0x{int(manifest['base'], 16) - SIZE:08X}")


if __name__ == "__main__":
    main()
