#!/usr/bin/env python3
"""Keep WWF Legends' moveset and attire codes off a heap they no longer describe (aki-roadmap#221).

The pack writes its movesets and extra attires at fixed heap addresses, most of them every frame with no condition:
0x80319260-0x80323FB0, where the game keeps two blocks on the menus (attire table 0x6660 bytes, pointer at
0x80047E90; moveset table 0x46E0 bytes, pointer at 0x80047E94) and one big block during a match (0x802F8080, size
0x2BF30, pointer at 0x801593E8). The game frees and allocates these again around every match (func_8000B314 /
func_8000B280). Stock lands them at the same addresses each time. With this pack the heap has extra fragments
after the first match, so they land elsewhere, and the same writes hit whatever lives there now. The second match
then crashes while loading.

Each write into that range becomes two units, so exactly one of them can fire:
- menus: the block pointer is still what the codes expect (0x80319260 / 0x8031F8D0);
- in a match: the block pointer is 0 and the match block pointer is 0x802F8090.
In any other layout the write is skipped instead of corrupting memory. On the first visit nothing changes; after the
layout moves, these codes stop (the game's own movesets and attires show) instead of crashing.

  legends_block_guards.py codes.txt [-o out.txt] [--check snapshot.bin ...]

--check takes RDRAM snapshots (host word order) and says which of the two guards holds in each.
"""
import argparse
import re
import struct
import sys

RANGE = (0x319260, 0x323FB0)
ATTIRE = (0x319260, 0x319260 + 0x6660)      # data start, end
ATTIRE_PTR = 0x047E90
MOVESET_PTR = 0x047E94
MOVESET = (0x31F8D0, 0x31F8D0 + 0x46E0)
MATCH_PTR = 0x1593E8
MATCH_BLOCK = 0x802F8090
HEADER = '# aki-roadmap#221: moveset/attire heap writes check where the game keeps those blocks (legends_block_guards)'
CODE = re.compile(r'^([0-9A-Fa-f]{2})([0-9A-Fa-f]{6}) ([0-9A-Fa-f]{4})$')
# Any line the engine could read as a write (other separators, trailing comments, longer values): refused when it
# targets the range but isn't in the plain form above, rather than left unguarded.
LOOSE_WRITE = re.compile(r'^\s*(80|81|A0|A1)([0-9A-Fa-f]{6})[\s:\-]', re.IGNORECASE)


def word_lines(addr, value):
    return ['D1%06X %04X' % (addr, value >> 16), 'D1%06X %04X' % (addr + 2, value & 0xFFFF)]


def menu_guard(target):
    """The menu block whose pointer a write depends on: writes in the attire table check 0x80047E90, the rest of
    the range (the moveset table and the header between them) check 0x80047E94."""
    if ATTIRE[0] <= target < ATTIRE[1]:
        return word_lines(ATTIRE_PTR, 0x80000000 | ATTIRE[0])
    return word_lines(MOVESET_PTR, 0x80000000 | MOVESET[0])


def match_guard(target):
    ptr = ATTIRE_PTR if ATTIRE[0] <= target < ATTIRE[1] else MOVESET_PTR
    return word_lines(ptr, 0) + word_lines(MATCH_PTR, MATCH_BLOCK)


def add_guards(text):
    """Returns (new text, report). Line endings and every other line are kept."""
    if HEADER in text:
        raise ValueError('this list already has the #221 guards')
    newline = '\r\n' if '\r\n' in text else '\n'
    out = []
    unit = []
    guarded = 0
    groups = set()
    group = ''
    gap = False  # conditions were flushed by a line the engine skips: they still govern the next write there
    for line in text.split(newline):
        s = line.strip()
        m = CODE.match(s)
        if not m or s.startswith('['):
            if s.startswith('['):
                group = s
            loose = LOOSE_WRITE.match(s)
            if loose and RANGE[0] <= int(loose.group(2), 16) < RANGE[1]:
                raise ValueError('write into the moveset/attire range in a form this tool does not parse: %r' % s)
            gap = gap or bool(unit)
            out.extend(unit)
            unit = []
            out.append(line)
            continue
        kind = m.group(1).upper()
        if kind[0] == 'D':
            unit.append(line)
            continue
        target = int(m.group(2), 16)
        if kind == '50':
            unit.append(line)  # a repeater governs the next write, like a condition
            continue
        repeat = CODE.match(unit[-1].strip()) if unit else None
        if repeat and repeat.group(1).upper() == '50':
            count, step = (int(repeat.group(2), 16) >> 8) & 0xFF, int(repeat.group(2), 16) & 0xFF
            if any(RANGE[0] <= target + k * step < RANGE[1] for k in range(count)):
                raise ValueError('repeater into the moveset/attire range, not handled: %s' % group)
            out.extend(unit)
            out.append(line)
            unit = []
            gap = False
            continue
        if kind in ('80', '81', 'A0', 'A1') and RANGE[0] <= target < RANGE[1]:
            if gap:
                raise ValueError('conditions separated from a write into the moveset/attire range, not handled: %s' % group)
            out.extend(menu_guard(target) + unit + [line])
            out.extend(match_guard(target) + unit + [line])
            guarded += 1
            groups.add(group)
        else:
            if kind in ('80', '81', 'A0', 'A1'):
                gap = False
            else:
                gap = gap or bool(unit)  # a code the engine doesn't treat as a write or prefix (88, F1...) is skipped
            out.extend(unit)
            out.append(line)
            unit = []
            continue
        gap = False
        unit = []
    out.extend(unit)
    new = newline.join(out)
    new, tagged = re.subn(r'(?m)^(# rom: *\S+)(\r?)$', lambda g: g.group(1) + g.group(2) + '\n' + HEADER + g.group(2), new, count=1)
    if not tagged:
        new = HEADER + newline + new
    return new, {'writes_guarded': guarded, 'groups': len(groups)}


def layout(rdram):
    """Which guard holds in an RDRAM snapshot: 'menus', 'match' or 'moved'."""
    w = lambda a: struct.unpack_from('<I', rdram, a)[0]
    if w(ATTIRE_PTR) == 0x80000000 | ATTIRE[0] and w(MOVESET_PTR) == 0x80000000 | MOVESET[0]:
        return 'menus'
    if w(ATTIRE_PTR) == 0 and w(MOVESET_PTR) == 0 and w(MATCH_PTR) == MATCH_BLOCK:
        return 'match'
    return 'moved'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes', nargs='?')
    ap.add_argument('-o', '--out')
    ap.add_argument('--check', nargs='*', default=[])
    a = ap.parse_args()
    for p in a.check:
        print(p, layout(open(p, 'rb').read()))
    if a.codes:
        new, report = add_guards(open(a.codes, encoding='utf-8', newline='').read())
        print(report)
        if a.out:
            open(a.out, 'w', encoding='utf-8', newline='').write(new)
    return 0


if __name__ == '__main__':
    sys.exit(main())
