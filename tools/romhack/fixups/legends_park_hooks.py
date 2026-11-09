#!/usr/bin/env python3
"""Park WWF Legends' Extra Attires and Body Shapes hooks until #205 fixes them (aki-roadmap#187, #205, #245).

The live-code engine follows hooks into pack code since N64ModernRuntime feat/fullconv-review daec861 (#187). Legends
has three: Ring Ropes works (the red / white / blue ropes, #245), the other two hang the game when followed:
  - Extra Attires: `attire_ptr` (0x8000B4B8, symbols from and0p/aki_n64_decomp) ends in `jr ra` at 0x8000B528, which
    the pack turns into `j 0x8070004C`, plus two `j 0x80700000` in ovl_e (0x8010A964, 0x8010A994). The cave searches
    a table at 0x800459A4 for the wrestler index with no end check and never finds it: the game hangs at boot.
  - Body Shapes: `appearance_build` (0x80018A3C) gets `j 0x805E8AF0` at 0x80018C38 and, around it, three edits to its
    own code: the delay slot 0x80018C3C, the `sll v1,s0,2` at 0x80018C50 and the `lui a2` immediate at 0x80018C56.
    Followed, the game deadlocks in a DMA wait under `model_nodes_build` (0x80015D8C). Those three edits only make
    sense with the hook: without it, 0x80018C3C drops the stock `sra s0,s0,16` and the game freezes the same way.
This comments out every code unit (with the conditions that govern it) that writes one of those game-code words, and
checks each write holds the value expected. The lines stay in the list, prefixed, for the day they work. The caves and
tables the pack writes in RAM stay as they are: nothing jumps there any more.

  legends_park_hooks.py codes.txt [-o out.txt]
  legends_park_hooks.py --unpark codes.txt [-o out.txt]

--unpark (aki-roadmap#205) puts them back, fixed:
  - Body Shapes wrote only the `lui a2` upper half and relied on the game's `lw a2,OFF(a2)` at 0x80018C5C, whose OFF
    is -0x74E8 in Rev 0 and -0x7588 in Rev 1: translated, the table base was 40 entries early, every body type read
    cave code or empty RAM as its pointer. The fix writes Rev 0's low half there too (81018C5E 8B18: 0x805E8B18, the
    table #249 moved), right after the lui write. Found by aki_conv_values' lui_game_lows (AkiMods).
  - Extra Attires needs no change: 0x800459A4 is where Rev 1 keeps the roster's 20-byte attire descriptors (the
    pointers at 0x80047124 start there; Rev 0's start 0xA0 later, and the cave's base sits 8 records before them, the
    same stride), so its search meets every wrestler index, created ones included. It hung with the old runtime, not
    with N64ModernRuntime daec861.
"""
import argparse
import re
import sys

# game-code halfword -> the values the pack writes there (Rev 1 addresses, after #249)
SITES = {
    # Extra Attires
    0x8000B528: {0x081C}, 0x8000B52A: {0x0013},
    0x8010A964: {0x081C}, 0x8010A966: {0x0000},
    0x8010A994: {0x081C}, 0x8010A996: {0x0000},
    # Body Shapes
    0x80018C38: {0x0817}, 0x80018C3A: {0xA2BC},
    0x80018C3C: {0x2400}, 0x80018C50: {0x2400}, 0x80018C56: {0x805F},
}
GROUPS = ('Extra Attires', 'Body Shapes')
PREFIX = '# off until #205: '
HEADER = ('# aki-roadmap#187: Extra Attires and Body Shapes hooks commented out, they hang when followed; '
          'Ring Ropes kept (legends_park_hooks)')
CODE = re.compile(r'^([0-9A-Fa-f]{2})([0-9A-Fa-f]{6}) ([0-9A-Fa-f]{4})$')
LOOSE = re.compile(r'^\s*(80|81|A0|A1|D[0-3]|50)([0-9A-Fa-f]{6})[\s:\-]', re.IGNORECASE)


def park(text):
    """Returns (new text, report). Line endings and every other line are kept."""
    if HEADER in text:
        raise ValueError('this list already has its #187 hooks parked')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(newline)
    group = ''
    unit = []          # line indices of the pending conditions / repeaters
    gap = False        # pending conditions were separated from what follows by a line the engine skips: they still
                       # govern the next write there, so commenting that write alone would leave them dangling
    park_lines = []
    seen = {a: 0 for a in SITES}
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('['):
            group = s
            gap = gap or bool(unit)
            continue
        m = CODE.match(s)
        if not m:
            if LOOSE.match(s):
                a = 0x80000000 | int(LOOSE.match(s).group(2), 16)
                if any(abs(a - site) < 4 for site in SITES):
                    raise ValueError('a code near a hook site in a form this tool does not parse: %r' % s)
                gap = gap or bool(unit)   # read by the engine or not, it's unclear what the conditions govern now
            elif s:
                gap = gap or bool(unit)
            continue
        kind, addr, value = m.group(1).upper(), 0x80000000 | int(m.group(2), 16), int(m.group(3), 16)
        if kind not in ('80', '81', 'A0', 'A1', '50') and kind[0] != 'D':
            gap = gap or bool(unit)   # enablers, button codes (F1, 88, EE...): skipped by the engine
            continue
        if kind[0] == 'D' or kind == '50':
            if kind == '50' and unit and CODE.match(lines[unit[-1]].strip()).group(1).upper() == '50':
                raise ValueError('two repeaters in a row, not handled: %r' % s)
            unit.append(i)
            continue
        if kind in ('80', 'A0') and (addr & ~1) in SITES:
            raise ValueError('an 8-bit write on a hook site, not handled: %r' % s)
        rep = next((j for j in unit if CODE.match(lines[j].strip()).group(1).upper() == '50'), None)
        if rep is not None:
            r = int(CODE.match(lines[rep].strip()).group(2), 16)
            count, step = (r >> 8) & 0xFF, r & 0xFF
            if any(addr + k * step in SITES for k in range(count)):
                raise ValueError('a repeater writes a hook site, not handled: %r' % s)
        elif addr in SITES:
            if value not in SITES[addr]:
                raise ValueError('%08X holds %04X in %s, expected %s' % (addr, value, group, '/'.join('%04X' % v for v in sorted(SITES[addr]))))
            if not any(g in group for g in GROUPS):
                raise ValueError('%08X is written by %s, not one of %s' % (addr, group, ', '.join(GROUPS)))
            if gap:
                raise ValueError('the conditions of the write at line %d are separated from it by a line the engine skips: not handled' % (i + 1))
            park_lines += unit + [i]
            seen[addr] += 1
        unit, gap = [], False
    missing = [a for a, n in seen.items() if n == 0]
    if missing:
        raise ValueError('no write found for %s: not the list this tool knows' % ', '.join('%08X' % a for a in missing))
    for i in park_lines:
        lines[i] = PREFIX + lines[i]
    new = newline.join(lines)
    new, tagged = re.subn(r'(?m)^(# rom: *\S+)(\r?)$', lambda g: g.group(1) + g.group(2) + '\n' + HEADER + g.group(2), new, count=1)
    if not tagged:
        new = HEADER + newline + new
    report = '%d lines commented out (%d writes on %d hook sites)' % (len(park_lines), sum(seen.values()), len(SITES))
    return new, report


BODY_LOW = ('81018C56 805F', '81018C5E 8B18')


def unpark(text):
    """The parked lines back on, with the Body Shapes low half written. Returns (new text, report)."""
    if HEADER not in text:
        raise ValueError('this list has no parked #187 hooks')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = [l for l in text.split(newline) if l != HEADER]
    back = 0
    for i, l in enumerate(lines):
        if l.startswith(PREFIX):
            lines[i] = l[len(PREFIX):]
            back += 1
    lui = [i for i, l in enumerate(lines) if l.strip().upper() == BODY_LOW[0]]
    if len(lui) != 1:
        raise ValueError('expected one %s write, found %d' % (BODY_LOW[0], len(lui)))
    added = 0
    if not any(l.strip().upper() == BODY_LOW[1] for l in lines):
        lines.insert(lui[0] + 1, BODY_LOW[1])
        added = 1
    new = newline.join(lines)
    _, check = park(new)   # the sites are all there with the values this tool knows, and nothing is parked
    return new, '%d lines back on, %d Body Shapes low-half write added' % (back, added)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes')
    ap.add_argument('-o', '--out')
    ap.add_argument('--unpark', action='store_true', help='put the parked hooks back, with the #205 fixes')
    a = ap.parse_args()
    new, report = (unpark if a.unpark else park)(open(a.codes, encoding='utf-8', newline='').read())
    print(report)
    if a.out:
        open(a.out, 'w', encoding='utf-8', newline='').write(new)
    return 0


if __name__ == '__main__':
    sys.exit(main())
