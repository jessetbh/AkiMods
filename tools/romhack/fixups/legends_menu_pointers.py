#!/usr/bin/env python3
"""Give WWF Legends' main menu its own entries back (Legends menu pass, after aki-roadmap#300).

The pack's "Interface\\Main Menu\\Main Screen\\Navigation\\Selection Pointers" codes rewrite the main menu's six entry
records in the heap: each record's label id (85xx), the low half of its pointer to the hack's menu-item descriptor
(0x8014xxxx), and the flag bytes that grey out Title Mode and Create Mode. Without them our port shows a different
list (Exhibition, Royal Rumble, Pay Per View, King of the Ring, Special Ref, Ladder Match, Ironman) and has no
Options; the Project64 reference shows Exhibition, Title Mode, Royal Rumble, King of the Ring, Create Mode, Options.

#300 left the codes out because their pointer halves are Rev 0 values: written as they are, they point at garbage and
the main menu froze. The record addresses translate like the pack's other main-menu codes (import-pj64 does that).
The descriptors themselves are the hack's data, which derelink places 0x1500 lower in Rev 1: Rev 0 0x8014D3E0 is
Rev 1 0x8014BEE0 (found by matching the same descriptor bytes in both RAMs at the main menu; the whole run
0x801415E0-0x80159920 moves by -0x1500). Only the low halves change; the 0x8014 upper halves are the game's own.

This rewrites the six pointer halves and refuses a list that differs from the one it was made for (each must be
exactly the Rev 0 value at the translated address).

  legends_menu_pointers.py codes.txt [-o out.txt]
"""
import argparse
import re
import sys

SECTION = 'WWF Legends\\Interface\\Main Menu\\Main Screen\\Navigation\\Selection Pointers'
DESCRIPTOR_SHIFT = -0x1500
# Rev 1 address (as import-pj64 translates it) -> the Rev 0 pointer low half the pack writes there
POINTERS = {0x812B65FE: 0xD3E0, 0x812B6686: 0xD464, 0x812B670E: 0xD3F6,
            0x812B6796: 0xD422, 0x812B681E: 0xD4A8, 0x812B68A6: 0xD4BE}
CODE = re.compile(r'^([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{4})(.*)$')


def fix_pointers(text):
    """(new text, report). Raises ValueError when the section or one of its six writes isn't what was expected."""
    nl = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(nl)
    start = next((i for i, l in enumerate(lines) if l.strip() == '[' + SECTION + ']'), None)
    if start is None:
        raise ValueError('no [%s] section (left out? it must be translated, not left out)' % SECTION)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('[')), len(lines))
    seen = {}
    for i in range(start + 1, end):
        m = CODE.match(lines[i].strip())
        if not m:
            continue
        addr, value = int(m.group(1), 16), int(m.group(2), 16)
        if addr not in POINTERS:
            continue
        if value != POINTERS[addr]:
            raise ValueError('%08X writes %04X, not the Rev 0 pointer half %04X this fix was made for' % (addr, value, POINTERS[addr]))
        lines[i] = '%08X %04X%s' % (addr, (value + DESCRIPTOR_SHIFT) & 0xFFFF, m.group(3))
        seen[addr] = seen.get(addr, 0) + 1
    missing = [a for a in POINTERS if a not in seen]
    if missing:
        raise ValueError('the section has no write at %s' % ', '.join('%08X' % a for a in missing))
    lines.insert(start + 1, '# Legends menu pass: descriptor pointer halves moved %s for Rev 1 (legends_menu_pointers)' % '-0x1500')
    return nl.join(lines), {'pointer_writes': sum(seen.values()), 'addresses': len(seen)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    new, report = fix_pointers(open(a.codes, encoding='utf-8', newline='').read())
    print(report)
    if a.out:
        open(a.out, 'w', encoding='utf-8', newline='').write(new)
    return 0


if __name__ == '__main__':
    sys.exit(main())
