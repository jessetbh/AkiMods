#!/usr/bin/env python3
"""Keep WWF Legends' menu codes off a heap they no longer describe (aki-roadmap#198).

The menu codes write fixed heap addresses, guarded only by a screen id or by "a wrestler select screen is up"
(8004FF82). That holds until the first match. With this pack the menus are rebuilt 0x242A0 higher afterwards (stock
rebuilds them in place), and the same writes land on allocator headers and texture bytes. A free block's size becomes
0x0400xxxx or 0x42CFxxxx, and the next allocation stores through 0xC028xxxx.

Two checks, added to the units that write the heap:
- Layout: the menu pool slot 8015982C still points where the codes expect (802BB4C0). It lives in main RAM and is
  rebuilt with the menus, so unlike heap contents it can't be stale. Every unit under the flag or a menu screen id.
- Record: under the flag, the record the unit is about holds its pointer 8014BCE4 at +0x28.

Writes to main RAM, and heap writes guarded by neither (in-match data), are left alone.

  legends_portrait_guards.py codes.txt [-o out.txt] [--check snapshot.bin ...]

--check takes RDRAM snapshots (host word order) and reports how many record signatures hold in each.
"""
import argparse
import re
import struct
import sys

FLAG_GUARD = 'D104FF82 00FF'
HEAP = (0x16E000, 0x38F800)
TABLES = {'1A': 0x2BD810, '1B': 0x2BDEF8}
RECORDS = 9
STRIDE = 0x88
SIG_OFFSET = 0x28
SIG_HIGH, SIG_LOW = 0x8014, 0xBCE4
LAYOUT_SLOT = 0x15982C
LAYOUT_HIGH, LAYOUT_LOW = 0x802B, 0xB4C0
NOT_MENU_SCREENS = {0x0A, 0x2A}  # in a match: the menu pool isn't there, and these codes aren't about it
SCREEN_GUARD = re.compile(r'^(D00457A5|D10457A4) ([0-9A-F]{4})$')
HEADER = '# aki-roadmap#198: menu heap writes also check the heap layout (legends_portrait_guards)'
CODE = re.compile(r'^([0-9A-Fa-f]{2})([0-9A-Fa-f]{6}) ([0-9A-Fa-f]{4})$')


def record_of(addr):
    """The record containing a RAM offset: (table name, record start), or None."""
    for name, start in TABLES.items():
        if start <= addr < start + RECORDS * STRIDE:
            return name, start + (addr - start) // STRIDE * STRIDE
    return None


def signature_lines(record):
    a = record + SIG_OFFSET
    return ['D1%06X %04X' % (a, SIG_HIGH), 'D1%06X %04X' % (a + 2, SIG_LOW)]


def layout_lines():
    return ['D1%06X %04X' % (LAYOUT_SLOT, LAYOUT_HIGH), 'D1%06X %04X' % (LAYOUT_SLOT + 2, LAYOUT_LOW)]


def menu_screen_guard(guards):
    """Index of the first guard that says "this menu screen is up", or None."""
    for i, g in enumerate(guards):
        m = SCREEN_GUARD.match(g)
        if m and int(m.group(2), 16) not in NOT_MENU_SCREENS:
            return i
    return None


def in_heap(addr):
    return HEAP[0] <= addr < HEAP[1]


def choose_record(target, guards, group, last_record):
    """The record a unit depends on: its target, else the record its own guards read, else the group's latest one."""
    found = record_of(target)
    if found:
        return found[1]
    for g in guards:
        found = record_of(int(g[2:8], 16))
        if found:
            return found[1]
    if last_record is not None:
        return last_record
    for name, start in TABLES.items():
        if '\\%s]' % name in group or '\\%s\\' % name in group:
            return start
    return TABLES['1A']


def add_guards(text):
    """Returns (new text, report). Line endings and every other line are kept."""
    if HEADER in text:
        raise ValueError('this list already has the #198 guards')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(newline)
    out = []
    unit = []  # conditional lines waiting for the code they govern
    group = ''
    last_record = None
    guarded = 0
    screens = 0
    groups = set()
    for line in lines:
        s = line.strip()
        m = CODE.match(s)
        if s.startswith('['):
            out.extend(unit)
            unit = []
            group = s
            last_record = None
            out.append(line)
            continue
        if not m:
            out.extend(unit)
            unit = []
            out.append(line)
            continue
        kind = m.group(1).upper()
        if kind[0] == 'D':
            unit.append(line)
            continue
        target = int(m.group(2), 16)
        stripped = [u.strip().upper() for u in unit]
        writes = kind in ('80', '81', 'A0', 'A1', 'F0', 'F1')
        if FLAG_GUARD in stripped and kind == '50':
            raise ValueError('repeater under the portrait flag, not handled: %s' % group)
        if writes and in_heap(target):
            screen = menu_screen_guard(stripped)
            if FLAG_GUARD in stripped:
                record = choose_record(target, stripped, group, last_record)
                last_record = record
                at = stripped.index(FLAG_GUARD) + 1
                unit[at:at] = layout_lines() + signature_lines(record)
                guarded += 1
                groups.add(group)
            elif screen is not None:
                unit[screen + 1:screen + 1] = layout_lines()
                screens += 1
                groups.add(group)
        out.extend(unit)
        unit = []
        out.append(line)
    out.extend(unit)
    new = newline.join(out)
    new, tagged = re.subn(r'(?m)^(# rom: *\S+)(\r?)$', lambda g: g.group(1) + g.group(2) + '\n' + HEADER + g.group(2), new, count=1)
    if not tagged:
        new = HEADER + newline + new
    return new, {'flag_writes_guarded': guarded, 'screen_writes_guarded': screens, 'groups': len(groups)}


def signatures_present(rdram):
    """How many of the records hold the signature in an RDRAM snapshot: {table: count, 'layout': bool}."""
    result = {'layout': struct.unpack_from('<I', rdram, LAYOUT_SLOT)[0] == (LAYOUT_HIGH << 16 | LAYOUT_LOW)}
    for name, start in TABLES.items():
        result[name] = sum(struct.unpack_from('<I', rdram, start + i * STRIDE + SIG_OFFSET)[0] == (SIG_HIGH << 16 | SIG_LOW)
                           for i in range(RECORDS))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes', nargs='?')
    ap.add_argument('-o', '--out')
    ap.add_argument('--check', nargs='*', default=[])
    a = ap.parse_args()
    for p in a.check:
        print(p, signatures_present(open(p, 'rb').read()))
    if a.codes:
        new, report = add_guards(open(a.codes, encoding='utf-8', newline='').read())
        print(report)
        if a.out:
            open(a.out, 'w', encoding='utf-8', newline='').write(new)
    return 0


if __name__ == '__main__':
    sys.exit(main())
