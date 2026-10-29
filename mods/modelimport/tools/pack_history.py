"""Bundled starter packs' update history (aki-roadmap#149). The games install a bundled model pack once; a later
bundle replaces an installed copy only when that copy is an earlier bundled version the player hasn't edited: its
contents hash to one of the pack's earlier versions listed in bundled-history.json. This tool keeps that list.

    python pack_history.py update packs\\nomercy --old <folder with the previous bundle's <id>.akimodel packs>
    python pack_history.py hash packs\\nomercy\\q-warrior.akimodel

The hash must match librecomp's recomp::mods::bundled_packs::pack_hash: FNV-1a 64 over every file in the pack folder,
sorted by its '/'-separated path relative to the pack, each as path bytes, 0x00, contents, 0x00. Text files (.json,
.md, .txt) are hashed with CRLF read as LF, so a checkout's line endings don't change it. Stdlib only."""
import json
import os
import sys

FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
TEXT_EXTS = ('.json', '.md', '.txt')
HISTORY_FILE = 'bundled-history.json'


def pack_hash_files(files):
    """files: [(relative path with '/', bytes)] -> the 64-bit hash."""
    h = FNV_OFFSET
    for path, data in sorted(files, key=lambda f: f[0].encode('utf-8')):
        if os.path.splitext(path)[1].lower() in TEXT_EXTS:
            data = data.replace(b'\r\n', b'\n')
        for chunk in (path.encode('utf-8'), b'\0', data, b'\0'):
            for byte in chunk:
                h = ((h ^ byte) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def pack_hash(folder):
    files = []
    for d, _, names in os.walk(folder):
        for n in names:
            full = os.path.join(d, n)
            rel = os.path.relpath(full, folder).replace(os.sep, '/')
            with open(full, 'rb') as f:
                files.append((rel, f.read()))
    return pack_hash_files(files)


def packs_in(folder):
    return sorted(n[:-len('.akimodel')] for n in os.listdir(folder)
                  if n.endswith('.akimodel') and os.path.isdir(os.path.join(folder, n)))


def update(game_dir, old_dir):
    path = os.path.join(game_dir, HISTORY_FILE)
    hist = {'format': 'aki-bundled-history', 'version': 1, 'previous': {}}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            hist = json.load(f)
    prev = hist.setdefault('previous', {})
    added = 0
    for pid in packs_in(old_dir):
        cur = os.path.join(game_dir, pid + '.akimodel')
        if not os.path.isdir(cur):
            continue
        old_h = '%016x' % pack_hash(os.path.join(old_dir, pid + '.akimodel'))
        if old_h == '%016x' % pack_hash(cur):
            continue          # unchanged: nothing to replace
        lst = prev.setdefault(pid, [])
        if old_h not in lst:
            lst.append(old_h)
            added += 1
    hist['previous'] = {k: prev[k] for k in sorted(prev)}
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(hist, f, indent=2)
        f.write('\n')
    print('%s: %d earlier version(s) added, %d packs with history' % (path, added, len(prev)))


def main(argv):
    if len(argv) >= 3 and argv[1] == 'hash':
        print('%016x' % pack_hash(argv[2]))
    elif len(argv) == 5 and argv[1] == 'update' and argv[3] == '--old':
        update(argv[2], argv[4])
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
