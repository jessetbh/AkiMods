#!/usr/bin/env python3
"""Move WWF Legends' code-cave tables out of No Mercy's framebuffers (aki-roadmap#249).

The pack writes its Extra Attires tables, its Body Shapes code and tables, and its Ring Ropes code cave every frame,
at 0x8038F000-0x803E7F8E (Rev 1 addresses; the list was written for Rev 0 at +0x1000). No Mercy keeps its two
framebuffers at 0x8038F800 and 0x803C7C00 (480x240, 16-bit), and its heap arena ends at 0x8038F800. So these
writes land in the picture, a horizontal line every other frame, and the first 2 KB of Extra Attires land on the
heap's last bytes. Project64 with Rice never renders into RDRAM, so the tables survived there and the authors never
saw it. On a console, or in our port, the game draws over them.

No Mercy uses 4 MB. This moves every write in 0x8038F000-0x80400000 by +0x201000, to the Rev 0 address plus
0x200000 (0x80590000-0x805E8F8E: free Expansion RAM; the pack's own tables use 0x8050xxxx, the relink zero block
starts at 0x80600000), and moves the references to them the same way:
  - the hooks into the moved caves: Body Shapes `j 0x803E7AF0` at 0x80018C38, Ring Ropes `j 0x803DF074` at
    0x80021BF0;
  - the Extra Attires lookup cave's table bases, `lui 0x803C` at 0x80700000 and `lui 0x8039` at 0x80700084. They
    were never translated from Rev 0 (#205: a stepped base can't take a low-half edit), so they missed the tables
    by 0x1000; now they point at them;
  - the Body Shapes pointer table load in game code, whose `lui` immediate the pack writes (0x80018C56: Rev 0
    value 0x803F, same fix as the cave bases);
  - the pointer words inside the moved blocks that point into them (Body Shapes' shape table).
The hooks are still refused by the live-code engine (#187/#205/#245), so the moved code doesn't run yet. The list
keeps everything it needs for the day hooks are followed, and nothing lands in the picture or the heap any more.

Legends-specific: it checks the expected words before changing them and refuses a list that differs.

  legends_fb_relocate.py codes.txt [-o out.txt]
"""
import argparse
import re
import sys

SRC = (0x8038F000, 0x80400000)
DELTA = 0x201000
DEST_FREE = (0x80590000, 0x80601000)   # must hold no write before the move (SRC + DELTA)
HOOKS = {0x80018C38: 0x080F9EBC, 0x80021BF0: 0x080F7C1D}          # j into the moved caves
STEPPED_LUI = {0x80700000: 0x3C02803C, 0x80700084: 0x3C0A8039}     # Extra Attires table bases (Rev 0 values)
# The pack writes only the immediate of a game `lui`: 0x80018C54 `lui a2,0x8005` -> 0x803F, the Body Shapes pointer
# table load (`lw a2,-0x7588(a2)` + 4*index). Rev 0 value, like the cave bases.
LONE_LUI = {0x80018C56: 0x803F}
GAME_CODE_END = 0x80144670   # main and the overlays' text end below this (ovl_e text ends here)
LUI_STEP = 0x20                                                    # Rev 0 base + 0x200000
HEADER = '# aki-roadmap#249: cave tables moved out of the framebuffers to Expansion RAM, +0x201000 (legends_fb_relocate)'
CODE = re.compile(r'^([0-9A-Fa-f]{2})([0-9A-Fa-f]{6}) ([0-9A-Fa-f]{4})$')
LOOSE_WRITE = re.compile(r'^\s*(80|81|A0|A1|D[0-3])([0-9A-Fa-f]{6})[\s:\-]', re.IGNORECASE)


def in_src(a):
    return SRC[0] <= a < SRC[1]


def relocate(text):
    """Returns (new text, report). Line endings and every other line are kept."""
    if HEADER in text:
        raise ValueError('this list already has the #249 move')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(newline)
    # Pass 1: every halfword write, the last one per address (the words the game ends up with).
    half = {}
    copies = {}   # address -> number of halfword write lines
    for i, line in enumerate(lines):
        s = line.strip()
        m = CODE.match(s)
        if not m:
            loose = LOOSE_WRITE.match(s)
            if loose and in_src(0x80000000 | int(loose.group(2), 16)):
                raise ValueError('a code into the moved range in a form this tool does not parse: %r' % s)
            continue
        kind, addr, value = m.group(1).upper(), 0x80000000 | int(m.group(2), 16), int(m.group(3), 16)
        if kind[0] == 'D' and in_src(addr):
            raise ValueError('a condition reads the moved range, not handled: %r' % s)
        if kind in ('80', '81', 'A0', 'A1') and DEST_FREE[0] <= addr < DEST_FREE[1]:
            raise ValueError('the destination is not free: %r' % s)
        if kind in ('81', 'A1'):
            half[addr] = (value, i)
            copies[addr] = copies.get(addr, 0) + 1
    for i, line in enumerate(lines):
        m = CODE.match(line.strip())
        if m and m.group(1).upper() == '50':
            nxt = next((CODE.match(l.strip()) for l in lines[i + 1:] if CODE.match(l.strip())), None)
            if nxt:
                count, step = (int(m.group(2), 16) >> 8) & 0xFF, int(m.group(2), 16) & 0xFF
                base = 0x80000000 | int(nxt.group(2), 16)
                if any(in_src(base + k * step) for k in range(count)):
                    raise ValueError('a repeater writes into the moved range, not handled: %r' % line)

    def word(a):
        if a in half and a + 2 in half:
            return (half[a][0] << 16) | half[a + 2][0]
        return None

    edits = {}   # line index -> new value

    def set_half(a, v):
        if copies.get(a, 0) != 1:
            raise ValueError('%08X is written %d times: which copy to change is not known' % (a, copies.get(a, 0)))
        edits[half[a][1]] = v

    def set_word(a, w):
        set_half(a, w >> 16)
        set_half(a + 2, w & 0xFFFF)

    for a, want in HOOKS.items():
        if word(a) != want:
            raise ValueError('hook at %08X is %s, expected %08X' % (a, None if word(a) is None else '%08X' % word(a), want))
        target = (a & 0xF0000000) | ((want & 0x03FFFFFF) << 2)
        new_target = target + DELTA
        set_word(a, (want & 0xFC000000) | ((new_target & 0x0FFFFFFF) >> 2))
    for a, want in STEPPED_LUI.items():
        if word(a) != want:
            raise ValueError('table base at %08X is %s, expected %08X' % (a, None if word(a) is None else '%08X' % word(a), want))
        set_word(a, (want & 0xFFFF0000) | ((want + LUI_STEP) & 0xFFFF))
    for a, want in LONE_LUI.items():
        if a not in half or half[a][0] != want:
            raise ValueError('lui immediate at %08X is %s, expected %04X' % (a, None if a not in half else '%04X' % half[a][0], want))
        if a - 2 in half:
            raise ValueError('the opcode half at %08X is written too: not a lone lui immediate' % (a - 2))
        set_half(a, want + LUI_STEP)
    pointers = 0
    for a in sorted(half):
        if a % 4 or not in_src(a):
            continue
        w = word(a)
        if w is not None and in_src(w):
            set_word(a, w + DELTA)
            pointers += 1
    # Any other word that jumps into the range from code the pack writes into game code or the caves is a reference
    # this tool doesn't know: refuse. (The Extra Attires tables at 0x8050xxxx hold data words like 0C0F0000.)
    for a in sorted(half):
        if a % 4 or a in HOOKS or a in STEPPED_LUI:
            continue
        w = word(a)
        if w is None or not (a < SRC[0] or in_src(a) or a >= 0x80700000):
            continue
        if (w >> 26) in (2, 3) and in_src((a & 0xF0000000) | ((w & 0x03FFFFFF) << 2)):
            raise ValueError('an unknown jump into the moved range at %08X: %08X' % (a, w))
        if (w >> 26) == 0x0F and 0x8038 <= (w & 0xFFFF) <= 0x8040 and (a < GAME_CODE_END or a >= 0x80400000):
            raise ValueError('an unknown lui of the moved range at %08X: %08X' % (a, w))
    # A lone immediate half in game code that could be a lui of the range (the opcode half isn't written).
    for a, (v, _) in half.items():
        if a % 4 == 2 and a < GAME_CODE_END and a not in LONE_LUI and a - 2 not in half and 0x8038 <= v <= 0x8040:
            raise ValueError('an unknown write of a range-like immediate into game code at %08X: %04X' % (a, v))

    moved = 0
    out = []
    for i, line in enumerate(lines):
        m = CODE.match(line.strip())
        if not m:
            out.append(line)
            continue
        kind, addr = m.group(1).upper(), 0x80000000 | int(m.group(2), 16)
        is_write = kind in ('80', '81', 'A0', 'A1')
        if i not in edits and not (is_write and in_src(addr)):
            out.append(line)
            continue
        value = edits.get(i, int(m.group(3), 16))
        if is_write and in_src(addr):
            addr += DELTA
            moved += 1
        lead = line[:len(line) - len(line.lstrip())]
        out.append('%s%s%06X %04X' % (lead, kind, addr & 0xFFFFFF, value))
    new = newline.join(out)
    new, tagged = re.subn(r'(?m)^(# rom: *\S+)(\r?)$', lambda g: g.group(1) + g.group(2) + '\n' + HEADER + g.group(2), new, count=1)
    if not tagged:
        new = HEADER + newline + new
    return new, {'writes_moved': moved, 'hooks': len(HOOKS), 'table_bases': len(STEPPED_LUI) + len(LONE_LUI), 'pointers': pointers}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    new, report = relocate(open(a.codes, encoding='utf-8', newline='').read())
    print(report)
    if a.out:
        open(a.out, 'w', encoding='utf-8', newline='').write(new)
    return 0


if __name__ == '__main__':
    sys.exit(main())
