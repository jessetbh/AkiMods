#!/usr/bin/env python3
"""Convert a Project64 cheat file (.cht) into an AKI codes file (the cheats mod's "Codes files" format).

Reads both Project64 layouts:
  - 1.6/2.x (one big Project64.cht):   [CRC1-CRC2-C:xx] / Name=... / Cheat0="Name",80XXXXXX YYYY,... /
                                       Cheat0_N=note / Cheat0_O=$FFFF Max,$0000 None
  - 3.x (one file per game):           [CRC1-CRC2-C:xx] / Name=... / $Name / code lines / .note / Note=...

The section header's CRC pair identifies the game and cartridge revision. That becomes the output's
"# rom: <game>-rev<N>" tag (the cheats engine refuses a list for another game, and translates or refuses one
for another revision), and the header itself is kept as a "# source:" comment. Cheat names become
"[Name]" headers and notes become comments.

Codes with "????" values (Project64 options) need a choice: by default they are written commented out, with
the options listed; --first-option picks each one's first option instead.

  python cht2codes.py NoMercy.cht -o codes.txt                  # the (single) game section in the file
  python cht2codes.py Project64.cht --game nomercy -o codes.txt # pick the section by game
  python cht2codes.py NoMercy.cht --list                        # list the cheats, change nothing
  python cht2codes.py NoMercy.cht --select "Unlock|Money" -o codes.txt
  python cht2codes.py NoMercy.cht --rev 0 -o codes.txt          # revision when the header doesn't say

Exit codes: 0 ok, 1 usage/input error, 2 written but the revision is unknown (the tag then refuses to load).
"""
import argparse
import re
import sys

# Header CRC pair (ROM header 0x10/0x14, what Project64 sections use) -> (game key, revision), for the
# ROMs the six ports run.
KNOWN_ROMS = {
    ('2C3E19BD', '5113EE5E'): ('wt', 0),
    ('DEE596AB', 'AF3B7AE7'): ('revenge', 0),
    ('BCACD7CB', '39BFD590'): ('wm2k', 0),
    ('045C08C4', '4AFD798B'): ('vpw64', 0),
    ('6C80F13B', '427EDEAA'): ('nomercy', 1),
    ('CD094235', '88074B62'): ('vpw2', 0),
}

# Section names, for headers whose CRC pair isn't one of the port ROMs (e.g. No Mercy Rev 0).
GAME_NAMES = [
    (re.compile(r'no\s*mercy', re.I), 'nomercy'),
    (re.compile(r'wrestle\s*mania\s*2000', re.I), 'wm2k'),
    (re.compile(r'revenge', re.I), 'revenge'),
    (re.compile(r'world\s*tour', re.I), 'wt'),
    (re.compile(r'virtual\s*pro\s*wrestling\s*2|pro\s*wrestling\s*2', re.I), 'vpw2'),
    (re.compile(r'virtual\s*pro\s*wrestling', re.I), 'vpw64'),
]
REV1_NAME = re.compile(r'\b(v\s*1\.1|rev\s*(1|a)|\(v1\.1\))', re.I)
REV0_NAME = re.compile(r'\b(v\s*1\.0|rev\s*0|\(v1\.0\))', re.I)

# The Rev 0 -> Rev 1 address ranges the engine translates (mirror of codes_engine.h's default map).
NOMERCY_REV0_MAP = [(0x80040000, 0x800D9960, -0x1530)]

SECTION_RE = re.compile(r'^\[([0-9A-Fa-f]{8})-([0-9A-Fa-f]{8})-C:([0-9A-Fa-f]{2})\]\s*$')
CODE_RE = re.compile(r'^([0-9A-Fa-f]{8})[\s:]+([0-9A-Fa-f]{4}|\?\?\?\?)$')
OLD_CHEAT_RE = re.compile(r'^Cheat(\d+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*,?(.*)$')
OLD_EXTRA_RE = re.compile(r'^Cheat(\d+)_([NO])\s*=\s*(.*)$')


class Cheat:
    def __init__(self, name):
        self.name = name
        self.codes = []     # (word, value or '????')
        self.notes = []
        self.options = []   # (value, label)


class Section:
    def __init__(self, crc1, crc2, country):
        self.crc1, self.crc2, self.country = crc1.upper(), crc2.upper(), country.upper()
        self.name = ''
        self.cheats = []

    @property
    def header(self):
        return '[%s-%s-C:%s]' % (self.crc1, self.crc2, self.country)

    def identify(self):
        """(game key or None, revision or None)."""
        known = KNOWN_ROMS.get((self.crc1, self.crc2))
        if known:
            return known
        game = next((g for rx, g in GAME_NAMES if rx.search(self.name)), None)
        rev = None
        if game == 'nomercy':
            rev = 1 if REV1_NAME.search(self.name) else 0 if REV0_NAME.search(self.name) else None
        return game, rev


def parse_options(text):
    out = []
    for part in text.split(','):
        part = part.strip()
        m = re.match(r'^\$?([0-9A-Fa-f]{1,4})\s*(.*)$', part)
        if m:
            out.append((m.group(1).upper().zfill(4), m.group(2).strip()))
    return out


def parse_cht(text):
    sections, sec, cheat = [], None, None
    old = {}  # 1.6/2.x: index -> Cheat
    for raw in text.splitlines():
        line = raw.strip().lstrip('﻿')
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            sec = Section(*m.groups())
            sections.append(sec)
            cheat, old = None, {}
            continue
        if sec is None:
            continue
        if line.startswith('Name='):
            sec.name = line[5:].strip()
            continue
        m = OLD_CHEAT_RE.match(line)
        if m:
            cheat = Cheat(m.group(2).replace('\\\\', '\\'))
            for part in m.group(3).split(','):
                c = CODE_RE.match(part.strip())
                if c:
                    cheat.codes.append((c.group(1).upper(), c.group(2).upper()))
            old[m.group(1)] = cheat
            sec.cheats.append(cheat)
            continue
        m = OLD_EXTRA_RE.match(line)
        if m:
            target = old.get(m.group(1))
            if target is not None:
                if m.group(2) == 'N':
                    target.notes.append(m.group(3).strip())
                else:
                    target.options += parse_options(m.group(3))
            continue
        # 3.x layout
        c = CODE_RE.match(line)
        if c and cheat is not None:
            cheat.codes.append((c.group(1).upper(), c.group(2).upper()))
            continue
        if line.startswith('$'):
            pending = cheat is not None and any(v == '????' for _, v in cheat.codes)
            if pending and re.match(r'^\$[0-9A-Fa-f]{1,4}(\s|$)', line):
                cheat.options += parse_options(line)
                continue
            cheat = Cheat(line[1:].strip())
            sec.cheats.append(cheat)
            continue
        if cheat is not None and (line.startswith('.') or line.startswith('Note=')):
            cheat.notes.append(line[1:].strip() if line.startswith('.') else line[5:].strip())
            continue
        if cheat is not None and line.startswith('Options='):
            cheat.options += parse_options(line[8:])
    return sections


def rev0_unmapped(word):
    addr = 0x80000000 | (int(word, 16) & 0xFFFFFF)
    return not any(lo <= addr < hi for lo, hi, _ in NOMERCY_REV0_MAP)


def convert(section, source_name, select=None, first_option=False, rev=None):
    """Returns (text, report dict)."""
    game, header_rev = section.identify()
    if rev is not None:
        header_rev = rev
    report = {'game': game, 'rev': header_rev, 'cheats': 0, 'codes': 0, 'needs_option': 0, 'unmapped_rev0': 0}
    if game is None:
        tag = 'unknown'
    elif header_rev is None:
        tag = '%s-unknown' % game
    else:
        tag = '%s-rev%d' % (game, header_rev)
    lines = [
        '# Converted from %s by cht2codes.py' % source_name,
        '# rom: %s' % tag,
        '# source: Project64 %s Name=%s' % (section.header, section.name),
    ]
    if tag.endswith('unknown'):
        lines.append('# WARNING: unknown game or revision; the cheats engine refuses this list until the tag is fixed')
    rx = re.compile(select, re.I) if select else None
    for cheat in section.cheats:
        if rx and not rx.search(cheat.name):
            continue
        if not cheat.codes:
            continue
        report['cheats'] += 1
        lines += ['', '[%s]' % cheat.name.replace(']', ')')]
        lines += ['# %s' % n for n in cheat.notes if n]
        for word, value in cheat.codes:
            if value == '????':
                if first_option and cheat.options:
                    value = cheat.options[0][0]
                else:
                    report['needs_option'] += 1
                    choices = ', '.join('%s = %s' % (v, l) if l else v for v, l in cheat.options) or 'none listed'
                    lines.append('# %s ???? -- choose a value (%s) and remove the "# "' % (word, choices))
                    continue
            if game == 'nomercy' and header_rev == 0 and (int(word[:2], 16) & 0xF0) in (0x80, 0xA0, 0xD0) and rev0_unmapped(word):
                report['unmapped_rev0'] += 1
            lines.append('%s %s' % (word, value))
            report['codes'] += 1
    return '\n'.join(lines) + '\n', report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cht')
    ap.add_argument('-o', '--out', help='codes file to write (default: stdout)')
    ap.add_argument('--game', help='section to convert, by game key (wt, revenge, wm2k, vpw64, nomercy, vpw2)')
    ap.add_argument('--section', help='section to convert, by header, e.g. 6C80F13B-427EDEAA')
    ap.add_argument('--select', help='only cheats whose name matches this regex')
    ap.add_argument('--first-option', action='store_true', help='use the first option for "????" codes')
    ap.add_argument('--rev', type=int, help='cartridge revision, when the section header doesn\'t identify it')
    ap.add_argument('--list', action='store_true', help='list sections and cheats only')
    a = ap.parse_args(argv)

    with open(a.cht, 'rb') as f:
        text = f.read().decode('utf-8', 'replace')
    sections = parse_cht(text)
    if not sections:
        print('no Project64 [CRC1-CRC2-C:xx] sections in %s' % a.cht, file=sys.stderr)
        return 1
    if a.section:
        sections = [s for s in sections if (s.crc1 + '-' + s.crc2).startswith(a.section.upper())]
    if a.game:
        sections = [s for s in sections if s.identify()[0] == a.game]
    if a.list:
        for s in sections:
            g, r = s.identify()
            print('%s %s  (game %s, rev %s, %d cheats)' % (s.header, s.name, g, r, len(s.cheats)))
            for c in s.cheats:
                print('    %-50s %d codes%s' % (c.name, len(c.codes), ', options' if c.options else ''))
        return 0
    if len(sections) != 1:
        print('%d matching sections; choose one with --game or --section (see --list)' % len(sections), file=sys.stderr)
        return 1
    out, rep = convert(sections[0], a.cht.replace('\\', '/').split('/')[-1], a.select, a.first_option, a.rev)
    if a.out:
        with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(out)
    else:
        sys.stdout.write(out)
    print('%d cheats, %d codes; game %s, rev %s%s%s' % (
        rep['cheats'], rep['codes'], rep['game'], rep['rev'],
        '; %d codes need an option (left commented out)' % rep['needs_option'] if rep['needs_option'] else '',
        '; %d Rev 0 codes are outside the Rev 1 map and will be dropped' % rep['unmapped_rev0'] if rep['unmapped_rev0'] else ''),
        file=sys.stderr)
    return 2 if rep['game'] is None or rep['rev'] is None else 0


if __name__ == '__main__':
    sys.exit(main())
