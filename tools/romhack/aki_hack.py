"""aki-hack: survey full-conversion mods for the AKI family ports (what a hack is, and how it can run).

Subcommands (run `python aki-hack.py <cmd> -h` for details):
  survey  FILE...   identify each hack (a ROM in any byte order, a zip, a patch, a code list, a save or a texture
                    pack): its base game and revision, and its tier, with the reasons. --md writes a table.

Tiers (aki-roadmap#28/#76, plans/fullconv-spike.md):
  stock           an unmodified base ROM
  patch-data      ROM patch that changes data only: a patch mod works on any port with ROM-patch mods
  patch-code      changes code in place, at stock addresses: a patch mod live-recompiles only those functions
                  ("immediates only" = every changed word differs only in its immediate, as VPW Studio builds do)
  rev0-layout     built on another revision whose code sits at known shifts (No Mercy Rev 0): needs the revision map
                  (Track A), then it's a patch against the port's revision
  relinked        code and data were moved (most functions differ only in address immediates): needs the de-relink
                  tool (Track A), then it's a patch mod
  restructured    changed code leaves function boundaries or changes code outside known functions, without being a
                  wholesale relink: needs porting work (de-relink style) or a full recompile
  different-base  made from another revision or release than the port's ROM
  codes / save / textures   non-patch parts of a conversion (see the manifest, plans/fullconv-manifest.md)
  unknown         not recognised

Needs the stock ROMs and RecompiledFuncs/ of the game repos (depot\\<Repo>\\, gitignored there; AKI_DEPOT names the
depot, else it's the folder holding this AkiMods checkout) and the patch-mod packager (make_patch_mod.py next to this
file). Reads only; it writes nothing but the --md/--json reports, and those hold no ROM bytes. aki_import.py calls
survey_rom() with the player's own port ROM instead (Stock(roms=..., tables=...)).
"""
import argparse
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
# AkiMods/tools/romhack -> the depot that holds the AkiMods checkout and the game repos.
DEPOT = os.environ.get('AKI_DEPOT') or os.path.dirname(os.path.dirname(os.path.dirname(TOOLS)))

# game key -> (repo, stock ROM file at the repo root, port save file name). Mirrors AkiMods common/games.psd1.
GAMES = {
    'wt':      ('WcwNwoWorldTour',  'wcw.z64',     None),
    'revenge': ('WcwRevengeRecomp', 'revenge.z64', None),
    'wm2k':    ('Wm2kRecomp',       'wm2k.z64',    'wwf.wm2k.us.bin'),
    'vpw64':   ('Vpw64Recomp',      'vpw64.z64',   None),
    'nomercy': ('NoMercyRecomp',    'nomercy.z64', 'wwf.nomercy.us.bin'),
    'vpw2':    ('Vpw2Recomp',       'vpw2.z64',    'vpw.vpw2.jp.bin'),
}

# Known dumps by full-ROM CRC32 (.z64 order): (game, revision tag). The ports use the first revision listed per game.
KNOWN_DUMPS = {
    'BACEEA13': ('nomercy', 'nomercy-rev1'),
    'B33F44F0': ('nomercy', 'nomercy-rev0'),   # USA v1.0: what NM+ V3, Project Outsider, Showdown 64 ... target
}
PORT_REVISION = {'nomercy': 'nomercy-rev1'}
# ROM-offset shifts of code that sits elsewhere in another revision (Track A, plans/fullconv-status-A.md: No Mercy Rev 0
# -> Rev 1 is ROM +0x70, main code +0x90/+0xA0). A "moved" hack with these shifts is that revision's layout, not a relink.
REVISION_SHIFTS = {'nomercy': ('nomercy-rev0', {0x70, 0x90, 0xA0})}

ROM_EXTS = ('.z64', '.n64', '.v64', '.rom', '.bin')
PATCH_EXTS = ('.bps', '.ips', '.ups', '.xdelta', '.vcdiff', '.ppf', '.aps')
CODE_EXTS = ('.txt', '.cht', '.ini', '.gsc', '.cfg')
SAVE_EXTS = ('.fla', '.sra', '.eep', '.mpk', '.srm', '.sav')
SAVE_SIZES = {131072: 'flash', 32768: 'sram-or-pak', 512: 'eeprom-4k', 2048: 'eeprom-16k', 296960: 'mupen-srm'}

CODE_RE = re.compile(r'^\s*([0-9A-Fa-f]{8})[\s:]+([0-9A-Fa-f]{4})\b')
PJ64_SECTION_RE = re.compile(r'^\[([0-9A-Fa-f]{8})-([0-9A-Fa-f]{8})-C:([0-9A-Fa-f]{2})\]')
CODE_TYPES = {
    0x80: '8-bit write', 0x81: '16-bit write', 0xA0: '8-bit write (uncached)', 0xA1: '16-bit write (uncached)',
    0xD0: 'if 8-bit equal', 0xD1: 'if 16-bit equal', 0xD2: 'if 8-bit not equal', 0xD3: 'if 16-bit not equal',
    0x50: 'repeat (serial)', 0xF0: '8-bit boot write', 0xF1: '16-bit boot write', 0xDE: 'entry point',
    0xEE: 'disable expansion pak', 0x88: 'button 8-bit write', 0x89: 'button 16-bit write', 0xFF: 'enabler',
}


# ------------------------------------------------------------------------------------ helpers

def load_packager():
    """make_patch_mod.py next to this file (load_sections, changed_functions, escaping_branches)."""
    for path in (os.path.join(TOOLS, 'make_patch_mod.py'),):
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location('make_patch_mod', path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, path
    return None, None


def to_z64(data):
    """(big-endian ROM bytes, original order) or (None, None) when it isn't an N64 ROM."""
    head = data[:4]
    if head == b'\x80\x37\x12\x40':
        return data, 'z64'
    if head == b'\x37\x80\x40\x12':
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b), 'v64'
    if head == b'\x40\x12\x37\x80':
        b = bytearray(len(data))
        for k in range(4):
            b[k::4] = data[3 - k::4]
        return bytes(b), 'n64'
    return None, None


def header(rom):
    return {
        'name': rom[0x20:0x34].decode('ascii', 'replace').strip(),
        'code': rom[0x3B:0x3F].decode('ascii', 'replace'),
        'revision': rom[0x3F],
        'crc1': '%08X' % int.from_bytes(rom[0x10:0x14], 'big'),
        'crc2': '%08X' % int.from_bytes(rom[0x14:0x18], 'big'),
        'entry': int.from_bytes(rom[0x08:0x0C], 'big'),
    }


class Stock:
    """The six stock ROMs and their recompiled section tables, loaded on demand.
    roms: {game: z64 bytes} to use instead of the game repos' ROMs (only those games are then available);
    tables: game -> (sections, repaired names) or (None, set()), instead of the repos' RecompiledFuncs."""

    def __init__(self, roms=None, tables=None):
        self.roms, self.sections, self.repaired = {}, {}, {}
        self.packager, self.packager_path = load_packager()
        self.refs = {}   # extra reference dumps (e.g. nomercy-rev0), from --ref
        self.only = None
        self.table_source = tables
        if roms is not None:
            self.roms = dict(roms)
            self.only = list(roms)

    def rom(self, game):
        if self.only is not None:
            return self.roms.get(game)
        if game not in self.roms:
            repo, name, _ = GAMES[game]
            path = os.path.join(DEPOT, repo, name)
            self.roms[game] = open(path, 'rb').read() if os.path.isfile(path) else None
        return self.roms[game]

    def available(self):
        return [g for g in GAMES if self.rom(g) is not None]

    def tables(self, game):
        if game not in self.sections and self.table_source is not None:
            self.sections[game], self.repaired[game] = self.table_source(game)
        if game not in self.sections:
            repo = GAMES[game][0]
            inl = os.path.join(DEPOT, repo, 'RecompiledFuncs', 'recomp_overlays.inl')
            rep = os.path.join(DEPOT, repo, 'RecompiledFuncs', 'repaired_functions.txt')
            self.sections[game] = self.packager.load_sections(inl) if (self.packager and os.path.isfile(inl)) else None
            self.repaired[game] = ({l.strip() for l in open(rep, encoding='utf-8') if l.strip() and not l.startswith('#')}
                                   if os.path.isfile(rep) else set())
        return self.sections[game], self.repaired[game]


def word_similarity(a, b, start=0x1000, length=0x100000):
    """Share of identical 32-bit words in the boot code region (a cheap base-game guess)."""
    end = min(start + length, len(a), len(b))
    if end <= start:
        return 0.0
    same = total = 0
    for i in range(start, end, 64):  # every 16th word is plenty for a ranking
        total += 1
        same += a[i:i + 4] == b[i:i + 4]
    return same / total


def immediate_only(base_word, hack_word):
    """True when two MIPS words differ only in an address-like immediate (I-type low 16 bits, J-type target)."""
    op = base_word >> 26
    if op != hack_word >> 26:
        return False
    if op in (2, 3):  # j / jal
        return True
    return (base_word >> 16) == (hack_word >> 16) and op not in (0,)  # I-type: same op/rs/rt


def masked_words(rom, limit=None):
    """The ROM as big-endian words with address-like immediates zeroed (J-type targets, I-type immediates), so code
    that only moved compares equal. R-type (op 0) words are kept whole."""
    # Stdlib only (the converter runs on embeddable Python): each word's first byte says its kind (op = byte >> 2), so
    # per-byte masks from a translate table, ANDed with each byte column as one big integer, do it without a loop.
    n = (min(len(rom), limit) if limit else len(rom)) // 4
    if n == 0:
        return b''
    rom = bytes(rom[:n * 4])
    first = rom[0::4]

    def masked(column, table):
        mask = first.translate(table)
        return (int.from_bytes(column, 'big') & int.from_bytes(mask, 'big')).to_bytes(n, 'big')
    out = bytearray(n * 4)
    out[0::4] = first.translate(_FIRST_BYTE)
    out[1::4] = masked(rom[1::4], _KEEP_UNLESS_J)
    out[2::4] = masked(rom[2::4], _KEEP_IF_R)
    out[3::4] = masked(rom[3::4], _KEEP_IF_R)
    return bytes(out)


_FIRST_BYTE = bytes((b & 0xFC) if (b >> 2) in (2, 3) else b for b in range(256))   # J-type: only the op stays
_KEEP_UNLESS_J = bytes(0x00 if (b >> 2) in (2, 3) else 0xFF for b in range(256))     # byte 1: rs/rt, not a J target
_KEEP_IF_R = bytes(0xFF if (b >> 2) == 0 else 0x00 for b in range(256))              # bytes 2-3: R-type only


def relink_probe(base, rom, sections, samples=300, sig_words=12):
    """Find stock functions in the hack by masked signature: how many sit at their stock ROM offset, how many moved
    (and by which shifts), how many weren't found. A relinked hack has most functions moved by a few constant
    shifts (Track A's relink map does this properly; this is the survey's quick look)."""
    funcs = [(rom_ + off, sz) for _, rom_, ram, size, e in sections for _, off, sz in e if sz >= sig_words * 4]
    if not funcs:
        return None
    step = max(1, len(funcs) // samples)
    mb, mh = masked_words(base), masked_words(rom)
    same = moved = missing = 0
    shifts = {}
    for a, sz in funcs[::step]:
        sig = mb[a:a + sig_words * 4]
        if mh[a:a + len(sig)] == sig:
            same += 1
            continue
        at = mh.find(sig)
        while at >= 0 and at % 4:
            at = mh.find(sig, at + 1)
        if at < 0:
            missing += 1
        else:
            moved += 1
            shifts[at - a] = shifts.get(at - a, 0) + 1
    total = same + moved + missing
    top = sorted(shifts.items(), key=lambda kv: -kv[1])[:3]
    return {'sampled': total, 'same': same, 'moved': moved, 'missing': missing,
            'top_shifts': ['%+#x x%d' % kv for kv in top]}


# ------------------------------------------------------------------------------------ analysers

def survey_rom(name, data, stock):
    rom, order = to_z64(data)
    if rom is None:
        return None
    h = header(rom)
    sha1 = hashlib.sha1(rom).hexdigest().upper()
    import zlib
    crc = '%08X' % (zlib.crc32(rom) & 0xFFFFFFFF)
    out = {'file': name, 'kind': 'rom', 'byte_order': order, 'size': len(rom), 'sha1': sha1, 'crc32': crc, 'header': h,
           'reasons': []}
    if crc in KNOWN_DUMPS:
        game, rev = KNOWN_DUMPS[crc]
        out.update(game=game, base_revision=rev)
        if rev != PORT_REVISION.get(game, rev):
            out['tier'] = 'stock'
            out['reasons'].append('unmodified %s dump; the port uses %s. Patches made for it apply to this one'
                                  % (rev, PORT_REVISION[game]))
            return out
    for g in stock.available():
        if hashlib.sha1(stock.rom(g)).hexdigest().upper() == sha1:
            out.update(game=g, tier='stock')
            out['reasons'].append('byte-identical to the %s stock ROM' % g)
            return out

    same_code = [g for g in stock.available() if stock.rom(g)[0x3B:0x3F] == rom[0x3B:0x3F]]
    scores = sorted(((word_similarity(stock.rom(g), rom), g) for g in stock.available()), reverse=True)
    game = same_code[0] if same_code else (scores[0][1] if scores and scores[0][0] >= 0.3 else None)
    probe = None
    if game is not None and stock.tables(game)[0] is not None:
        probe = relink_probe(stock.rom(game), rom, stock.tables(game)[0])
    if game is None or (probe and probe['missing'] > 0.5 * probe['sampled']):
        # The header lies or the code moved: find the stock game whose functions the hack contains.
        best = None
        for g in stock.available():
            if stock.tables(g)[0] is None:
                continue
            p = relink_probe(stock.rom(g), rom, stock.tables(g)[0], samples=120)
            found = (p['same'] + p['moved']) / p['sampled'] if p and p['sampled'] else 0.0
            if best is None or found > best[0]:
                best = (found, g, p)
        if best and best[0] >= 0.5 and best[1] != game:
            game = best[1]
            probe = relink_probe(stock.rom(game), rom, stock.tables(game)[0])
            out['reasons'].append('header %r/rev %d; %.0f%% of %s functions found in the code'
                                  % (h['code'], h['revision'], 100 * best[0], game))
    if game is None:
        out.update(tier='unknown')
        out['reasons'].append('game code %r matches no port, and no port functions were found in the code' % h['code'])
        return out
    base = stock.rom(game)
    out['game'] = game
    if probe:
        out['relink_probe'] = probe
    if not same_code and not out['reasons']:
        out['reasons'].append('game code %r differs from the port; base from code similarity (%.0f%%)'
                              % (h['code'], 100 * dict((gg, sc) for sc, gg in scores)[game]))
    found = (probe['same'] + probe['moved']) / probe['sampled'] if probe and probe['sampled'] else 1.0
    if probe and found < 0.5:
        out.update(tier='different-base')
        out['reasons'].append('only %.0f%% of the port functions found (%d/%d): another release or revision%s'
                              % (100 * found, probe['same'] + probe['moved'], probe['sampled'],
                                 ' (header revision %d, the port uses %d)' % (rom[0x3F], base[0x3F]) if rom[0x3F] != base[0x3F] else ''))
        return out
    rev_layout = None
    if probe and probe['moved'] > probe['same'] and game in REVISION_SHIFTS:
        label, known = REVISION_SHIFTS[game]
        at_known = sum(int(t.split(' x')[1]) for t in probe['top_shifts'] if int(t.split(' x')[0], 16) in known)
        if at_known >= 0.9 * probe['moved']:
            rev_layout = label
    if rev_layout:
        out.update(tier='rev0-layout', base_revision=rev_layout + ' (code layout)')
        out['reasons'].append('%d of %d sampled functions sit at the %s shifts (%s), %d in place, %d not found: built on'
                              ' %s, not relinked. Needs the Rev0->Rev1 map (Track A), then the real changes are a patch'
                              % (probe['moved'], probe['sampled'], label, ', '.join(probe['top_shifts']), probe['same'],
                                 probe['missing'], label))
        if len(rom) > len(base):
            out['reasons'].append('expanded ROM: %.1f MB' % (len(rom) / 1048576))
        return out
    if probe and probe['moved'] > probe['same']:
        out.update(tier='relinked')
        out['reasons'].append('%d of %d sampled functions moved (shifts %s), %d in place, %d not found: relinked,'
                              ' needs the de-relink tool (Track A)' % (probe['moved'], probe['sampled'],
                                                                       ', '.join(probe['top_shifts']), probe['same'], probe['missing']))
        if len(rom) > len(base):
            out['reasons'].append('expanded ROM: %.1f MB' % (len(rom) / 1048576))
        if rom[0x3F] != base[0x3F] or rom[0x3B:0x3F] != base[0x3B:0x3F]:
            out['reasons'].append('header edited (code %s, rev %d)' % (h['code'], rom[0x3F]))
        rev_hint(out, game, rom, base, stock)
        return out
    if rom[0x3F] != base[0x3F]:
        out['reasons'].append('header revision %d (the port uses %d), but the code matches the port ROM' % (rom[0x3F], base[0x3F]))
    if len(rom) > len(base):
        out['reasons'].append('expanded ROM: %.1f MB (stock %.0f MB)' % (len(rom) / 1048576, len(base) / 1048576))
    entry_hook = h['entry'] != int.from_bytes(base[0x08:0x0C], 'big')
    boot_changed = rom[0x40:0x1000] != base[0x40:0x1000]
    if entry_hook:
        out['reasons'].append('entry point moved (0x%08X)' % h['entry'])

    sections, repaired = stock.tables(game)
    if sections is None:
        out.update(tier='unknown')
        out['reasons'].append('no RecompiledFuncs/recomp_overlays.inl or packager for %s: can\'t check code' % game)
        return out
    funcs, loose = stock.packager.changed_functions(sections, base, rom)
    total = sum(1 for s in sections for f in s[4] if f[2] > 0)
    escapes = stock.packager.escaping_branches(sections, rom, funcs)

    # Words that differ only in address immediates (moved code) vs real changes, over the changed functions.
    by_name = {(n, f): (r, ram, off, sz) for n, r, ram, size, e in sections for f, off, sz in e}
    real = imm_words = changed_words = 0
    for section, func, vram, size, words in funcs:
        r, ram, off, sz = by_name[(section, func)]
        func_real = False
        for i in range(0, sz, 4):
            a = int.from_bytes(base[r + off + i:r + off + i + 4], 'big')
            b = int.from_bytes(rom[r + off + i:r + off + i + 4], 'big')
            if a != b:
                changed_words += 1
                if immediate_only(a, b):
                    imm_words += 1
                else:
                    func_real = True
        real += func_real
    out.update(functions_total=total, functions_changed=len(funcs), functions_real=real, loose_code_bytes=loose,
               escaping=len(escapes), repaired_changed=sorted(f[1] for f in funcs if f[1] in repaired))

    share = len(funcs) / total if total else 0.0
    imm_share = imm_words / changed_words if changed_words else 0.0
    if not funcs and not loose:
        out['tier'] = 'patch-data'
        out['reasons'].append('no code changes: data only')
    elif share > 0.5 and imm_share > 0.6:
        out['tier'] = 'relinked'
        out['reasons'].append('%d of %d functions differ, %.0f%% of changed words only in address immediates;'
                              ' about %d functions really changed' % (len(funcs), total, 100 * imm_share, real))
    elif escapes or loose:
        out['tier'] = 'restructured'
        out['reasons'].append('%d changed function(s); %d branch(es) leave their function, %d changed code byte(s)'
                              ' outside functions' % (len(funcs), len(escapes), loose))
    else:
        out['tier'] = 'patch-code'
        out['reasons'].append('%d function(s) changed in place (%d word(s))%s' % (
            len(funcs), changed_words, ', immediates only' if real == 0 else ''))
    if boot_changed and out['tier'] in ('patch-data', 'patch-code'):
        out['reasons'].append('boot code (0x40-0xFFF) changed')
    if game in PORT_REVISION and 'base_revision' not in out:
        out['base_revision'] = PORT_REVISION[game]
    if out.get('repaired_changed'):
        out['reasons'].append('touches %d function(s) the port repaired offline: %s' % (
            len(out['repaired_changed']), ', '.join(out['repaired_changed'][:4])))
    return out


def rev_hint(out, game, rom, base, stock):
    """No Mercy hacks may be made from Rev 0 (USA v1.0) although the port is Rev 1: say which, as far as we can tell.
    With a Rev 0 reference (--ref nomercy-rev0=PATH) the code decides; otherwise the header revision is a hint."""
    if game != 'nomercy':
        return
    ref = stock.refs.get('nomercy-rev0')
    if ref is not None:
        r0, r1 = word_similarity(ref, rom), word_similarity(base, rom)
        out['base_revision'] = 'nomercy-rev0' if r0 > r1 else 'nomercy-rev1'
        out['reasons'].append('code is closer to %s (aligned words: rev0 %.0f%%, rev1 %.0f%%)'
                              % (out['base_revision'], 100 * r0, 100 * r1))
    elif rom[0x3F] == 0:
        out['base_revision'] = 'nomercy-rev0 (header; unverified)'
        out['reasons'].append('header revision 0: probably made from Rev 0 (USA v1.0), so the shifts above include'
                              ' Rev 0 -> Rev 1 differences; pass --ref nomercy-rev0=<dump> to check')
    else:
        out['base_revision'] = 'unknown (header edited)'


def survey_codes(name, data, stock):
    text = data.decode('utf-8', 'replace')
    codes, sections = [], []
    for line in text.splitlines():
        m = PJ64_SECTION_RE.match(line.strip())
        if m:
            sections.append((m.group(1).upper(), m.group(2).upper(), m.group(3).upper()))
        c = CODE_RE.match(line)
        if c:
            codes.append((int(c.group(1), 16), int(c.group(2), 16)))
    if not codes:
        return None
    types = {}
    for addr, val in codes:
        t = CODE_TYPES.get(addr >> 24, 'type %02X' % (addr >> 24))
        types[t] = types.get(t, 0) + 1
    out = {'file': name, 'kind': 'codes', 'tier': 'codes', 'codes': len(codes), 'types': types, 'reasons': []}
    # Which ROM the codes target: Project64 cheat sections carry the header CRCs.
    for crc1, crc2, country in sections:
        for g in stock.available():
            h = header(stock.rom(g))
            if (h['crc1'], h['crc2']) == (crc1, crc2):
                out.update(game=g, rom_revision='matches the port ROM (%s)' % g)
                break
        else:
            continue
        break
    if 'rom_revision' not in out:
        out['rom_revision'] = 'unknown (no Project64 [CRC-CRC-C:xx] header matching a port ROM)'
        out['reasons'].append('check the codes against the port\'s revision: No Mercy Rev 0 codes corrupt Rev 1')
    unknown = [t for t in types if t.startswith('type ')]
    if unknown:
        out['reasons'].append('unusual code types: %s' % ', '.join(unknown))
    return out


SAVE_SIGNATURES = {  # game -> big-endian signature in the save image
    'nomercy': b'WWF2 AKI',
    'vpw2': b'19991101',
    'wm2k': b'\x19\x99\x09\x01',
}


def byte_orders(sig):
    w = bytearray(sig + b'\0' * (-len(sig) % 4))
    s32 = bytearray(len(w))
    for i in range(0, len(w), 4):
        s32[i:i + 4] = w[i:i + 4][::-1]
    s16 = bytearray(len(w))
    s16[0::2], s16[1::2] = w[1::2], w[0::2]
    return {'raw (big-endian)': bytes(sig), 'word-swapped (Project64)': bytes(s32[:len(sig)]),
            'halfword-swapped': bytes(s16[:len(sig)])}


def survey_save(name, data):
    ext = os.path.splitext(name)[1].lower()
    kind = SAVE_SIZES.get(len(data))
    if kind is None and ext not in SAVE_EXTS:
        return None
    out = {'file': name, 'kind': 'save', 'tier': 'save', 'size': len(data), 'save_kind': kind or 'unknown size',
           'reasons': []}
    if kind == 'mupen-srm':
        out['reasons'].append('mupen64plus .srm: EEPROM 2 KB + 4 paks + SRAM 32 KB + FlashRAM 128 KB in one file')
    found = []
    for game, sig in SAVE_SIGNATURES.items():
        for order, pattern in byte_orders(sig).items():
            # Signatures are aligned to their word in the image; search 4-byte aligned positions.
            for m in re.finditer(re.escape(pattern), data):
                found.append((game, order, m.start()))
                break
    if found:
        game, order, at = found[0]
        out.update(game=game, byte_order=order)
        out['reasons'].append('%s save signature at 0x%X' % (game, at))
    else:
        out['byte_order'] = 'unknown (no known signature)'
    if ext == '.mpk' or (kind == 'sram-or-pak' and data[:1] == b'\x81'):
        out['save_kind'] = 'controller-pak'
    return out


def survey_textures(name, names):
    lower = [n.lower() for n in names]
    pngs = [n for n in lower if n.endswith(('.png', '.dds'))]
    if not pngs and not any(n.endswith(('.htc', '.hts')) for n in lower):
        return None
    base = [os.path.basename(n) for n in pngs]
    rt64 = sum(1 for b in base if re.match(r'^[0-9a-f]{16}\.(png|dds)$', b))
    rice = sum(1 for b in base if '#' in b)
    jabo = sum(1 for b in base if re.match(r'^[0-9a-f]{32}\.(png|dds)$', b))
    has_db = any(os.path.basename(n) == 'rt64.json' for n in lower)
    naming = 'rt64' if (has_db or rt64 >= max(rice, jabo)) and rt64 else ('rice/gliden64' if rice >= jabo and rice
                                                                          else ('jabo' if jabo else 'other'))
    if any(n.endswith(('.htc', '.hts')) for n in lower):
        naming = 'gliden64 cache (.htc/.hts)'
    out = {'file': name, 'kind': 'textures', 'tier': 'textures', 'images': len(pngs), 'naming': naming,
           'rt64_json': has_db, 'reasons': []}
    if naming == 'rice/gliden64':
        out['reasons'].append('Rice/GLideN64 names: convert with aki-tex convert-rice (needs a dump map), #93')
    elif naming == 'jabo':
        out['reasons'].append('Jabo names (MD5 of decoded texture): not convertible offline')
    elif not has_db:
        out['reasons'].append('rt64 hash names without rt64.json: aki-tex pack writes one')
    return out


def classify_bytes(name, data, stock):
    lower = name.lower()
    if lower.endswith(PATCH_EXTS):
        out = {'file': name, 'kind': 'patch', 'format': os.path.splitext(lower)[1][1:], 'size': len(data), 'reasons': []}
        if data[:4] == b'BPS1':
            crc = int.from_bytes(data[-12:-8], 'little')
            out['source_crc32'] = '%08X' % crc
            import zlib
            for g in stock.available():
                if zlib.crc32(stock.rom(g)) & 0xFFFFFFFF == crc:
                    out['game'] = g
                    out['reasons'].append('BPS source CRC32 matches the %s stock ROM' % g)
            if 'game' not in out:
                out['reasons'].append('BPS source CRC32 %08X matches no port ROM (another revision?)' % crc)
        else:
            out['reasons'].append('apply it to the base ROM first, then survey the result')
        out['tier'] = 'patch'
        return [out]
    r = survey_rom(name, data, stock) if len(data) >= 0x101000 else None
    if r:
        return [r]
    s = survey_save(name, data)
    if s and (lower.endswith(SAVE_EXTS) or s.get('game')):
        return [s]
    if lower.endswith(CODE_EXTS) or len(data) < 1 << 20:
        c = survey_codes(name, data, stock)
        if c:
            return [c]
    return []


def survey_path(path, stock):
    name = os.path.basename(path)
    if os.path.isdir(path):
        names = [os.path.relpath(os.path.join(d, f), path) for d, _, fs in os.walk(path) for f in fs]
        results = []
        t = survey_textures(name, names)
        if t:
            results.append(t)
        for n in names:
            if not n.lower().endswith(('.png', '.dds', '.json')):
                results += classify_bytes(os.path.join(name, n), open(os.path.join(path, n), 'rb').read(), stock)
        return results
    if zipfile.is_zipfile(path):
        results = []
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if not i.is_dir()]
            t = survey_textures(name, [i.filename for i in members])
            if t:
                results.append(t)
            for i in members:
                if i.filename.lower().endswith(('.png', '.dds', '.json', '.htc', '.hts')):
                    continue
                for r in classify_bytes(i.filename, z.read(i), stock):
                    r['container'] = name
                    results.append(r)
        return results or [{'file': name, 'kind': 'zip', 'tier': 'unknown', 'reasons': ['nothing recognised inside']}]
    return classify_bytes(name, open(path, 'rb').read(), stock) or \
        [{'file': name, 'kind': 'file', 'tier': 'unknown', 'reasons': ['not a ROM, patch, code list, save or texture pack']}]


# ------------------------------------------------------------------------------------ output

def markdown(results):
    rows = ['| file | game | tier | what it is / why |', '|---|---|---|---|']
    for r in results:
        label = r.get('container', r['file'])
        if r.get('container') and r['file'] != r['container']:
            label = '%s -> %s' % (r['container'], r['file'])
        extra = []
        if r.get('kind') == 'rom' and r.get('tier') not in ('stock', 'unknown'):
            extra.append('%s, rev %d, %.1f MB' % (r['header']['code'], r['header']['revision'], r['size'] / 1048576))
            if r.get('byte_order') != 'z64':
                extra.append('%s byte order' % r['byte_order'])
        if r.get('kind') == 'codes':
            extra.append('%d codes (%s); revision: %s' % (r['codes'], ', '.join('%s %d' % kv for kv in r['types'].items()),
                                                           r['rom_revision']))
        if r.get('kind') == 'save':
            extra.append('%s, %s' % (r['save_kind'], r.get('byte_order')))
        if r.get('kind') == 'textures':
            extra.append('%d images, %s naming%s' % (r['images'], r['naming'], ', rt64.json' if r['rt64_json'] else ''))
        if r.get('base_revision'):
            extra.append('base: %s' % r['base_revision'])
        why = '; '.join(extra + r.get('reasons', []))
        rows.append('| %s | %s | **%s** | %s |' % (label.replace('|', '/'), r.get('game', '?'), r.get('tier', '?'),
                                                   why.replace('|', '/')))
    return '\n'.join(rows) + '\n'


def layout_tables(stock):
    """Stock tables from derelink layouts (layouts/<game>.json next to this file) instead of RecompiledFuncs:
    a layout counts only for the ROM it was made from. No repaired-function list (layouts don't carry one)."""
    def tables(game):
        rom = stock.rom(game)
        if rom is None or stock.packager is None:
            return None, set()
        try:
            path = stock.packager.resolve_layout(game, rom)
        except stock.packager.PatchModError as e:
            print('note: no layout tables for %s (%s)' % (game, e), file=sys.stderr)
            return None, set()
        return stock.packager.layout_sections(path), set()
    return tables


def cmd_survey(args):
    roms = None
    for spec in args.rom or []:
        game, _, path = spec.partition('=')
        if game not in GAMES or not path:
            sys.exit('--rom takes GAME=PATH (games: %s)' % ', '.join(GAMES))
        if not os.path.isfile(path):
            sys.exit('--rom %s: no such file' % spec)
        roms = roms or {}
        roms[game] = to_z64(open(path, 'rb').read())[0]
        if roms[game] is None:
            sys.exit('--rom %s: not an N64 ROM' % spec)
    stock = Stock(roms=roms)
    if args.layout:
        stock.table_source = layout_tables(stock)
    for ref in args.ref or []:
        label, _, path = ref.partition('=')
        stock.refs[label] = to_z64(open(path, 'rb').read())[0]
    if stock.packager is None:
        print('warning: make_patch_mod.py not found (AKIMODS_DIR or depot\\AkiMods*): code tiers unavailable',
              file=sys.stderr)
    results = []
    for pattern in args.paths:
        for path in ([pattern] if os.path.exists(pattern) else (sorted(glob.glob(pattern)) or [pattern])):
            print('surveying %s' % path, file=sys.stderr)
            results += survey_path(path, stock)
    if args.md:
        open(args.md, 'w', encoding='utf-8').write(markdown(results))
    if args.json:
        open(args.json, 'w', encoding='utf-8').write(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2) if args.print_json else markdown(results))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('survey', help='identify hacks and their tier')
    s.add_argument('paths', nargs='+', help='files, folders or glob patterns')
    s.add_argument('--md', help='write the table as Markdown here')
    s.add_argument('--json', help='write the full results as JSON here')
    s.add_argument('--print-json', action='store_true', help='print JSON instead of the table')
    s.add_argument('--ref', action='append', metavar='LABEL=PATH',
                   help='extra reference dump, e.g. nomercy-rev0=C:\roms\nm10.z64 (your own; read only)')
    s.add_argument('--layout', action='store_true',
                   help='function tables from layouts/<game>.json (no game repo or RecompiledFuncs needed)')
    s.add_argument('--rom', action='append', metavar='GAME=PATH',
                   help='a port ROM to compare with instead of the game repos\' ROMs (only these games then)')
    s.set_defaults(func=cmd_survey)
    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
