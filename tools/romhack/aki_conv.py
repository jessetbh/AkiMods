"""aki-conv: build AKI conversion packages (plans/fullconv-manifest.md) from a hack or a Project64 mod bundle.

Subcommands (run `python aki-conv.py <cmd> -h` for details):
  import-pj64 FOLDER   a Project64 mod bundle (Project64.cht codes, a Rice/Jabo/rt64 hi-res texture folder, a .fla/.sra/
                       .mpk save, optionally a ROM or patch) -> a conversion package, plus a separate texture package
  pack-nrm NRM         a patch-mod .nrm (from make_patch_mod.py or Track A's derelink.py) -> a patch conversion package
  translate-codes      rewrite a codes.txt's RDRAM addresses through a revision map (e.g. No Mercy Rev 0 -> Rev 1)
  bake-codes FOLDER    a pack's codes that edit game instructions (no effect as codes) -> its patch.nrm

Packages are Thunderstore-shaped zips: manifest.json + icon.png + README.md + aki.json + the component files.
Everything here is built from the player's own files on the player's PC; nothing is downloaded or uploaded.
"""
import argparse
import collections
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aki_conv_bake  # noqa: E402

TOOLS = os.path.dirname(os.path.abspath(__file__))
# AkiMods/tools/romhack -> the depot that holds the AkiMods checkout and the game repos (developer runs only).
DEPOT = os.environ.get('AKI_DEPOT') or os.path.dirname(os.path.dirname(os.path.dirname(TOOLS)))
DERELINK = os.path.join(TOOLS, 'derelink.py')
# The ports' ROMs (.z64 SHA-1), as AKI Launcher's assets/games.json and aki_import.GAMES.
PORT_SHA1 = {'wt': '5AD2D8359058C8BB71F08E3D3433B7A50D3BB645', 'revenge': 'E1711A2511394B9357B5F1AC8CA5CC17BD674836',
             'wm2k': '442D417A52ED672CA1A47E7261A5414DEBB1E27A', 'vpw64': 'F9E9FA2ED819C3A39DB5CB6AFECA186F021DB5ED',
             'nomercy': '91CEE3D096F4A76644D8B35B9AEAD6448909ABD1', 'vpw2': '82DD25A044689EAB57AB362FE10C0DA6388C217A'}
# The port ROM's Project64 cheat section, set by a caller that has the player's ROM (aki_import); else read from the repo.
PORT_CHT_KEYS = {}
BS = chr(92)

# Project64 cheat-section header CRCs (ROM header CRC1-CRC2-C:country) -> revision tag.
CHT_SECTIONS = {
    '4E4B0640-1B49BCFB-C:45': 'nomercy-rev0',   # WWF No Mercy (U) v1.0
    '6C80F13B-427EDEAA-C:45': 'nomercy-rev1',   # WWF No Mercy (U) v1.1 (the port's ROM)
}
GAME_OF_TAG = {'nomercy-rev0': 'nomercy', 'nomercy-rev1': 'nomercy'}
# The revision each port runs (the launcher and the runtime use the same tags).
PORT_TAG = {'nomercy': 'nomercy-rev1', 'wt': 'wt-rev0', 'revenge': 'revenge-rev0', 'wm2k': 'wm2k-rev0', 'vpw64': 'vpw64-rev0',
            'vpw2': 'vpw2-rev0'}
GAME_MOD_ID = {'nomercy': 'wwfnomercy', 'wt': 'wcwnwoworldtour', 'revenge': 'wcwnworevenge', 'wm2k': 'wwfwm2k', 'vpw64': 'vpw64',
               'vpw2': 'vpw2'}  # AkiMods common/games.psd1 GameId
PORT_SAVE = {'nomercy': 'wwf.nomercy.us.bin', 'vpw2': 'vpw.vpw2.jp.bin', 'wm2k': 'wwf.wm2k.us.bin'}
CODE_RE = re.compile(r'([0-9A-Fa-f]{8})\s+([0-9A-Fa-f?]{4})')


# ------------------------------------------------------------------------------------ Project64 cheats

def parse_cht(text):
    """Project64 .cht/.ini -> {section_key: {'name', 'cheats': [{'index', 'name', 'codes': [(addr, value)], 'note'}]}}."""
    sections, cur = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'^\[([0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-C:[0-9A-Fa-f]{2})\]$', line)
        if m:
            cur = sections.setdefault(m.group(1).upper(), {'name': '', 'cheats': []})
            continue
        if cur is None:
            continue
        if line.startswith('Name='):
            cur['name'] = line[5:]
            continue
        m = re.match(r'^Cheat(\d+)="([^"]*)"(.*)$', line)
        if m:
            codes = [(int(a, 16), v.upper()) for a, v in CODE_RE.findall(m.group(3))]
            cur['cheats'].append({'index': int(m.group(1)), 'name': m.group(2), 'codes': codes, 'note': ''})
            continue
        m = re.match(r'^Cheat(\d+)_N=(.*)$', line)
        if m:
            for c in cur['cheats']:
                if c['index'] == int(m.group(1)):
                    c['note'] = m.group(2)
    return sections


def pj64_active_cheats(cfg_text, key):
    """The cheats a Project64 2.x config has ticked for one ROM: {index} from "CheatN=1" lines in every [<key>] section of
    Config/Project64.cfg (the file can hold the same section more than once). None when it has no such section. Project64
    1.6 kept this in the registry, so its bundles can't say."""
    active, found, cur = set(), False, False
    for raw in cfg_text.splitlines():
        line = raw.strip()
        if line.startswith('['):
            cur = line.upper().startswith('[' + key.upper() + ']')
            found = found or cur
            continue
        m = re.match(r'^Cheat(\d+)=(\d+)', line) if cur else None
        if m and m.group(2) != '0':
            active.add(int(m.group(1)))
    return active if found else None


def norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def mod_cheats(section, mod_name, include=None):
    """The mod's own cheats in a big Project64 database: those under a top-level folder named like the mod
    ("TNA vs ROH" -> "TNAvsROH\\..."), or matching the include regex."""
    out = []
    for c in section['cheats']:
        top = c['name'].split(BS)[0]
        if include is not None:
            if re.search(include, c['name']):
                out.append(c)
        elif norm(top) == norm(mod_name):
            out.append(c)
    return out


def codes_txt(cheats, rom_tag, title, defaults_on=True):
    """codes.txt: one [Name] section per cheat (Project64's folder path kept), its codes one per line."""
    lines = ['# %s: GameShark codes for %s' % (title, rom_tag), '# rom: %s' % rom_tag, '']
    for c in cheats:
        if not c['codes']:
            lines.append('# skipped (no codes): %s' % c['name'])
            continue
        if any('?' in v for _, v in c['codes']):
            lines.append('# skipped (needs a choice value): %s' % c['name'])
            continue
        lines.append('[%s]' % c['name'])
        if c['note']:
            lines.append('# %s' % c['note'])
        lines.append('# default: %s' % ('on' if defaults_on else 'off'))
        lines += ['%08X %s' % (a, v) for a, v in c['codes']]
        lines.append('')
    return '\n'.join(lines) + '\n'


def code_types(cheats):
    t = {}
    for c in cheats:
        for a, _ in c['codes']:
            t['%02X' % (a >> 24)] = t.get('%02X' % (a >> 24), 0) + 1
    return t


# ------------------------------------------------------------------------------------ revision translation

def translate_address(addr24, ranges):
    """An RDRAM offset (low 24 bits of a code address) through [(start, end, delta)] ranges (end exclusive);
    None when no range covers it (the code can't be translated)."""
    for start, end, delta in ranges:
        if start <= addr24 < end:
            return addr24 + delta
    return None


def code_units(lines):
    """Group code lines into units: conditionals (D0-D3) and repeaters (50) govern the code after them, so a unit is
    those prefixes plus the one code they govern. Non-code lines are their own unit (kept as they are)."""
    units, cur = [], []
    for line in lines:
        m = re.match(r'^([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\s+([0-9A-Fa-f]{4})', line.strip())
        if not m:
            if cur:
                units.append(cur)
                cur = []
            units.append([line])
            continue
        cur.append(line)
        if m.group(1).upper() not in ('D0', 'D1', 'D2', 'D3', '50'):
            units.append(cur)
            cur = []
    if cur:
        units.append(cur)
    return units


def translate_unit(unit, ranges):
    """A unit's lines through the map, or None when an address in it has no mapping."""
    out = []
    for line in unit:
        m = re.match(r'^([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\s+([0-9A-Fa-f]{4})(.*)$', line.strip())
        if not m or m.group(1).upper() == '50':  # a repeater holds a count, not an address
            out.append(line)
            continue
        new = translate_address(int(m.group(2), 16), ranges)
        if new is None:
            return None
        out.append('%s%06X %s%s' % (m.group(1).upper(), new, m.group(3).upper(), m.group(4)))
    return out


def translate_codes(text, ranges, overlays=None, guard=None):
    """Rewrite code addresses through the map, a whole unit at a time: if any address in a unit can't be mapped,
    the unit (its conditionals or repeater and the code they govern) is dropped, so a conditional never ends up
    governing the wrong code.

    Overlay RAM is shared: different overlays sit at the same addresses at different times, and when they moved by
    different amounts between revisions the plain map can't place a code there. With `overlays` ({name: ranges},
    the map with only that overlay, from overlay_ranges) and `guard` (a function (overlays in, overlays out) ->
    (address, halfword) or None, from overlay_guard), such a unit is translated once per group of overlays that give
    the same result, and each version is emitted behind a 16-bit conditional on a halfword that tells its group's
    code is loaded (D1 = equal; with two groups and one guard the other version gets D3 = not equal). A conditional
    governs the whole next unit in the port's engine, so the guard stacks with the unit's own conditionals.
    Returns (text, translated codes, dropped lines); the split counts are in translate_codes.last."""
    out, done, bad = [], 0, []
    split = 0
    count = lambda u: sum(1 for l in u if re.match(r'^[0-9A-Fa-f]{8}\s', l.strip()))
    for unit in code_units(text.splitlines()):
        new_lines = translate_unit(unit, ranges)
        if new_lines is not None:
            out += new_lines
            done += count(unit)
            continue
        versions = guarded_versions(unit, overlays, guard) if overlays and guard else []
        if versions:
            for g, lines in versions:
                out += [g] + lines
            done += count(unit)
            split += 1
            continue
        bad += unit
        out += ['# dropped (no mapping): ' + l for l in unit]
    translate_codes.last = {'units_split': split}
    return '\n'.join(out) + '\n', done, bad


def guarded_versions(unit, overlays, guard):
    """[(guard line, translated lines)] for a unit only overlay-specific maps can place, or [] when they can't (some
    group has no guard, or no overlay maps it)."""
    groups = {}
    for name in sorted(overlays):
        t = translate_unit(unit, overlays[name])
        if t is not None:
            groups.setdefault(tuple(t), []).append(name)
    if len(groups) < 2:
        return []  # one result would have come from the plain map; none means nothing covers the address
    names = sorted(overlays)
    out, missing = [], []
    for lines, members in groups.items():
        g = guard(tuple(members), tuple(n for n in names if n not in members))
        if g is None:
            missing.append((lines, members))
        else:
            out.append(('D1%06X %04X' % (g[0] & 0xFFFFFF, g[1]), list(lines), g))
    if missing:
        if len(groups) == 2 and len(out) == 1:
            g = out[0][2]
            out.append(('D3%06X %04X' % (g[0] & 0xFFFFFF, g[1]), list(missing[0][0]), g))
        else:
            return []
    return [(gl, lines) for gl, lines, _ in out]


def overlay_ranges(m):
    """{overlay name: the map with only that overlay} for translate_codes(overlays=...)."""
    return {name: map_from_derelink(dict(m, overlays={name: rs})) for name, rs in m.get('overlays', {}).items()}


def overlay_guard(layout, port_rom, patched_rom=None):
    """A guard finder for translate_codes from the port's layout (derelink's layouts/<game>.json: overlay sections
    with their ROM offset and RAM text..data range, in the port's revision) and the port's ROM. For (in, out) overlay
    names it returns (RAM address, halfword) at the first even address the `in` overlays all cover with the same
    nonzero halfword, which every `out` overlay covering it differs from. Overlay code is DMA'd into RDRAM as it is in
    the ROM, so the halfword is there when that overlay is loaded. With `patched_rom` (the conversion's own patch
    applied, same layout) the halfword must also be unchanged by the patch."""
    secs = {s['name']: s for s in layout['sections'] if s.get('slot') is not None}
    cache = {}

    def at(name, addr, rom):
        s = secs[name]
        if not (s['text'] <= addr < s['data'] - 1):
            return None
        off = s['rom'] + (addr - s['text'])
        return rom[off:off + 2]

    def find(ins, outs):
        if not ins or any(n not in secs for n in ins + outs):
            return None
        lo = max(secs[n]['text'] for n in ins)
        hi = min(secs[n]['data'] for n in ins)
        # Best: an address every `out` overlay covers too (each of them is told apart by its own code). Otherwise one
        # at least one of them covers: when the others are loaded the address holds something else.
        for need_all in (True, False):
            for addr in range((lo + 1) & ~1, hi - 1, 2):
                vals = {at(n, addr, port_rom) for n in ins}
                if len(vals) != 1:
                    continue
                v = vals.pop()
                if v is None or v == b'\0\0':
                    continue
                if patched_rom is not None and any(at(n, addr, patched_rom) != v for n in ins):
                    continue
                theirs = [at(n, addr, port_rom) for n in outs]
                covered = [t for t in theirs if t is not None]
                if v in covered or not covered or (need_all and len(covered) != len(outs)):
                    continue
                return addr, struct.unpack('>H', v)[0]
        return None

    def guard(ins, outs):
        key = (ins, outs)
        if key not in cache:
            cache[key] = find(list(ins), list(outs))
        return cache[key]
    return guard


def find_layout(derelink_path, port_rom):
    """The derelink layout (layouts/*.json next to the tool) whose base_sha1 is the port's ROM, or None."""
    sha1 = hashlib.sha1(port_rom).hexdigest()
    for path in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(derelink_path)), 'layouts', '*.json'))):
        layout = json.load(open(path))
        if layout.get('base_sha1', '').lower() == sha1:
            return layout
    return None


def map_from_derelink(m, unsafe=((0x062C28, 0x0647A0),)):
    """Track A's derelink.py map JSON -> [(start, end, delta)] over 24-bit RDRAM offsets, for GameShark codes.
    main ranges always apply; overlay ranges only where every overlay that covers an address agrees on the delta
    (the slots hold different overlays at different times); the unsafe main bss (the Rev 0/Rev 1 save buffers,
    whose arrays changed size) is left out; identity ranges map to themselves."""
    def cut(lo, hi, d):
        pieces = [(lo, hi)]
        for ulo, uhi in unsafe:
            nxt = []
            for a, b in pieces:
                if b <= ulo or a >= uhi:
                    nxt.append((a, b))
                    continue
                if a < ulo:
                    nxt.append((a, ulo))
                if b > uhi:
                    nxt.append((uhi, b))
            pieces = nxt
        return [(a, b, d) for a, b in pieces if b > a]

    ranges = []
    for lo, hi, d, kind in m.get('main', []):
        ranges += cut(lo & 0xFFFFFF, hi & 0xFFFFFF if hi & 0xFFFFFF else 0x1000000, d)
    for lo, hi in m.get('identity', []):
        lo24, hi24 = lo - 0x80000000, hi - 0x80000000
        ranges.append((max(lo24, 0), min(hi24, 0x800000), 0))
    # Overlays: split into elementary intervals; keep those where all covering overlays agree.
    ovl = [(lo & 0xFFFFFF, hi & 0xFFFFFF, d) for name, rs in m.get('overlays', {}).items() for lo, hi, d, kind in rs]
    points = sorted({p for lo, hi, _ in ovl for p in (lo, hi)})
    for a, b in zip(points, points[1:]):
        ds = {d for lo, hi, d in ovl if lo <= a and b <= hi}
        if len(ds) == 1:
            ranges += cut(a, b, ds.pop())
    # main wins over overlays where they overlap (it always applies); merge adjacent equal deltas.
    ranges.sort()
    merged = []
    for lo, hi, d in ranges:
        if merged and lo < merged[-1][1]:
            lo = merged[-1][1]
            if lo >= hi:
                continue
        if merged and merged[-1][1] == lo and merged[-1][2] == d:
            merged[-1] = (merged[-1][0], hi, d)
        else:
            merged.append((lo, hi, d))
    return merged


# ------------------------------------------------------------------------------------ packaging

def ts_name(s):
    """Thunderstore package names are [A-Za-z0-9_]."""
    return re.sub(r'[^A-Za-z0-9_]', '', s.replace(' ', '_')) or 'Package'


def icon_png(text_seed):
    """A plain 256x256 PNG (Thunderstore wants one); colour from the name. Replace it with real art."""
    h = hashlib.sha1(text_seed.encode()).digest()
    rgb = bytes((h[0] | 0x40, h[1] | 0x40, h[2] | 0x40))
    raw = b''.join(b'\x00' + rgb * 256 for _ in range(256))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 256, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def write_package(path, ts_manifest, readme, aki, files):
    """files: {zip path: bytes or a filesystem path}. Deterministic: fixed timestamps and sorted entries, so the same
    inputs give the same zip (and sha256), which is what a conversion pins its texture package by."""
    entries = {'manifest.json': json.dumps(ts_manifest, indent=2).encode(), 'icon.png': icon_png(ts_manifest['name']),
               'README.md': readme.encode()}
    if aki is not None:
        entries['aki.json'] = json.dumps(aki, indent=2).encode()
    entries.update(files)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            data = entries[name]
            if not isinstance(data, (bytes, bytearray)):
                data = open(data, 'rb').read()
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def load_tool(name, subdir_glob):
    """A helper next to this file (aki_save.py for aki-save.py), else a depot checkout matching subdir_glob."""
    sibling = os.path.join(TOOLS, name.replace('-', '_'))
    if os.path.isfile(sibling):
        spec = importlib.util.spec_from_file_location(name.replace('-', '_').replace('.py', ''), sibling)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    for root in sorted(glob.glob(os.path.join(DEPOT, subdir_glob))):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location(name.replace('-', '_').replace('.py', ''), path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def stock_sha1(game):
    if game in PORT_SHA1:
        return PORT_SHA1[game]
    repos = {'nomercy': ('NoMercyRecomp', 'nomercy.z64'), 'vpw2': ('Vpw2Recomp', 'vpw2.z64'), 'wm2k': ('Wm2kRecomp', 'wm2k.z64'),
             'wt': ('WcwNwoWorldTour', 'wcw.z64'), 'revenge': ('WcwRevengeRecomp', 'revenge.z64'), 'vpw64': ('Vpw64Recomp', 'vpw64.z64')}
    repo, rom = repos[game]
    path = os.path.join(DEPOT, repo, rom)
    return hashlib.sha1(open(path, 'rb').read()).hexdigest().upper() if os.path.isfile(path) else ''


def cht_key(rom):
    """Project64's cheat section for a ROM: CRC1-CRC2-C:<country> from the .z64 header."""
    crc1, crc2 = struct.unpack('>II', rom[0x10:0x18])
    return '%08X-%08X-C:%02X' % (crc1, crc2, rom[0x3E])


def stock_cht_key(game):
    if game in PORT_CHT_KEYS:
        return PORT_CHT_KEYS[game]
    repos = {'nomercy': ('NoMercyRecomp', 'nomercy.z64'), 'vpw2': ('Vpw2Recomp', 'vpw2.z64'), 'wm2k': ('Wm2kRecomp', 'wm2k.z64'),
             'wt': ('WcwNwoWorldTour', 'wcw.z64'), 'revenge': ('WcwRevengeRecomp', 'revenge.z64'), 'vpw64': ('Vpw64Recomp', 'vpw64.z64')}
    path = os.path.join(DEPOT, *repos.get(game, ('', '')))
    if not os.path.isfile(path):
        return ''
    with open(path, 'rb') as f:
        return cht_key(f.read(0x40))


# ------------------------------------------------------------------------------------ NINJA2 (.rup) patches

# Base ROMs a patch may name by MD5 (the whole cartridge image, .z64 byte order).
KNOWN_ROM_MD5 = {'04c492be7f89fc6f425238bd67629544': 'nomercy-rev0', '66b8ec24557a50514a814f15429bd559': 'nomercy-rev1'}
JABO_NAME = re.compile(r'^[0-9a-fA-F]{32}\.(png|dds)$')


def parse_rup(b):
    """A NINJA2 patch (the .rup Showdown 64 ships), as RomPatcher.js reads it: "NINJA2", header text up to 0x800, then
    commands 0x01 open file (name, ROM type, source and target size, source and target MD5, overflow mode + data when
    the sizes differ), 0x02 XOR record (offset, bytes), 0x00 end. Numbers are a byte count then little-endian bytes."""
    if b[:6] != b'NINJA2':
        raise ValueError('not a NINJA2 patch')
    pos = [0x800]

    def take(n):
        pos[0] += n
        return b[pos[0] - n:pos[0]]

    def vlv():
        n = take(1)[0]
        return int.from_bytes(take(n), 'little') if n else 0

    files = []
    while True:
        cmd = take(1)[0]
        if cmd == 0x00:
            return files
        if cmd == 0x01:
            f = {'name': take(vlv()).decode('latin-1'), 'type': take(1)[0], 'src': vlv(), 'dst': vlv()}
            f['src_md5'] = take(16).hex()
            f['dst_md5'] = take(16).hex()
            f['overflow_mode'], f['overflow'] = '', b''
            if f['src'] != f['dst']:
                f['overflow_mode'] = take(1).decode('latin-1')
                f['overflow'] = take(vlv())
            f['records'] = []
            files.append(f)
        elif cmd == 0x02:
            if not files:
                raise ValueError('XOR record before any file')
            off = vlv()
            files[-1]['records'].append((off, take(vlv())))
        else:
            raise ValueError('unknown NINJA2 command %#x at %#x' % (cmd, pos[0] - 1))


def apply_rup(source, patch):
    """source bytes + NINJA2 patch -> target bytes, checking both MD5s. Appended data ('A') extends the source before
    the XOR records apply; 'M' cuts it to the target size."""
    files = parse_rup(patch)
    if len(files) != 1:
        raise ValueError('patch holds %d files; one expected' % len(files))
    f = files[0]
    if hashlib.md5(source).hexdigest() != f['src_md5']:
        raise ValueError('this ROM is not the one the patch was made for (MD5 %s, the patch wants %s)' % (
            hashlib.md5(source).hexdigest(), f['src_md5']))
    out = bytearray(source[:f['dst']])
    if f['dst'] > len(out):
        out += f['overflow'] if f['overflow_mode'] == 'A' else b''
        out += bytes(max(0, f['dst'] - len(out)))
        del out[f['dst']:]
    for off, data in f['records']:
        if off + len(data) > len(out):
            raise ValueError('XOR record at %#x runs past the target' % off)
        for i, x in enumerate(data):
            out[off + i] ^= x
    out = bytes(out)
    if hashlib.md5(out).hexdigest() != f['dst_md5']:
        raise ValueError('patched ROM MD5 %s, the patch promises %s' % (hashlib.md5(out).hexdigest(), f['dst_md5']))
    return out


def rup_summary(f):
    by_mb = {}
    for off, data in f['records']:
        by_mb[off >> 20] = by_mb.get(off >> 20, 0) + len(data)
    return {'source_md5': f['src_md5'], 'base': KNOWN_ROM_MD5.get(f['src_md5'], 'unknown'), 'source_size': f['src'],
            'target_size': f['dst'], 'target_md5': f['dst_md5'], 'xor_records': len(f['records']),
            'changed_bytes': sum(len(d) for _, d in f['records']),
            'changed_bytes_per_mb': {str(k): v for k, v in sorted(by_mb.items())}}


# ------------------------------------------------------------------------------------ import-pj64

def cmd_import_pj64(a):
    folder = a.folder
    files = [os.path.join(d, f) for d, _, fs in os.walk(folder) for f in fs]
    rel = lambda p: os.path.relpath(p, folder)
    name = a.name
    cid = a.id
    report = {'id': cid, 'name': name, 'parts': {}}
    aki = {'format': 'aki-conversion', 'format_version': 1, 'id': cid, 'name': name, 'version': a.version,
           'authors': a.author or ['unknown'], 'game': a.game,
           'base_rom': {'sha1': stock_sha1(a.game), 'revision': PORT_TAG.get(a.game, a.game)},
           'description': a.description or ('%s, imported from its Project64 bundle.' % name),
           'credits': a.credits, 'permission': a.permission, 'links': [], 'thumb': 'icon.png',
           'components': {}, 'source': {'mode': 'byo', 'imported_from': 'project64-bundle'}}
    pkg_files = {}

    # Codes.
    chts = [p for p in files if p.lower().endswith(('.cht', '.ini')) and os.path.getsize(p) > 0]
    for cht in chts:
        sections = parse_cht(open(cht, encoding='latin-1').read())
        for key, sec in sections.items():
            tag = CHT_SECTIONS.get(key)
            if tag is None and key.upper() == stock_cht_key(a.game):
                tag = PORT_TAG.get(a.game, a.game)  # the section of the port's own ROM (any game)
            if tag is None or GAME_OF_TAG.get(tag, tag.split('-rev')[0]) != a.game:
                continue
            cheats = mod_cheats(sec, name, a.include)
            if not cheats:
                continue
            # The bundle's own selection (Project64 2.x Config\Project64.cfg): only the cheats its player had ticked.
            # The port's engine runs every code in codes.txt, and some bundles carry alternatives meant to be picked
            # from (NJPW 2017's Blackout 1/2, Sells 1/2: all of them on froze the game).
            selection = None
            for cfg in [p for p in files if os.path.basename(p).lower() == 'project64.cfg']:
                got = pj64_active_cheats(open(cfg, encoding='latin-1').read(), key)
                if got is not None:
                    selection = (selection or set()) | got
            if selection is not None and not getattr(a, 'all_cheats', False):
                picked = [c for c in cheats if c['index'] in selection]
                report['parts']['codes_selection'] = {'source': 'Project64.cfg', 'ticked': len(picked), 'of': len(cheats)}
                if picked:
                    cheats = picked
            text = codes_txt(cheats, tag, name)
            port = PORT_TAG.get(a.game, a.game)
            if tag != port and a.map:
                ranges = load_map(a.map)
                overlays, guard, guard_note = overlay_guard_setup(a)
                text, done, bad = translate_codes(text, ranges, overlays, guard)
                report['parts']['codes_translated'] = {'codes': done, 'dropped_lines': len(bad),
                                                       'units_split_by_overlay': translate_codes.last['units_split'],
                                                       'overlay_guards': guard_note}
                # Every code left is at the port's addresses now: tag it so, or the engine would translate it again.
                text = text.replace('# rom: %s' % tag, '# rom: %s\n# translated from %s by aki-conv (revision map)' % (port, tag), 1)
                tag = port
            pkg_files['codes.txt'] = text.encode()
            aki['components']['codes'] = {'file': 'codes.txt', 'format': 'aki-codes', 'rom': tag}
            report['parts']['codes'] = {'source': rel(cht), 'section': key, 'rom': tag, 'cheats': len(cheats),
                                        'lines': sum(len(c['codes']) for c in cheats), 'types': code_types(cheats),
                                        'categories': sorted({BS.join(c['name'].split(BS)[:2]) for c in cheats})}
            if tag != port:
                report['parts']['codes']['note'] = ('written for %s; the port is %s. They stay off until translated '
                                                    '(--map, Track A\'s Rev0->Rev1 map)' % (tag, port))
            break

    # Saves.
    saves_mod = load_tool('aki-save.py', 'AkiTools-fullconv-saves')
    # Not the extras that texture packs carry in their own folders (Challenge 64 has a pak per wrestler there); the
    # Save folder's copy comes first when two map to the same port file.
    tex_dirs = texture_dirs(files)
    save_inputs = [p for p in files if p.lower().endswith(('.fla', '.sra', '.mpk', '.eep', '.srm'))
                   and not in_dirs(p, tex_dirs)
                   and not any(x.lower() in ('plugin', 'plugins') for x in rel(p).split(os.sep)[:-1])]
    # Project64 names a save after the ROM's internal name ("WWF No Mercy.fla"): an exact match is the one the mod
    # plays with; copies like "aaaaWWF No Mercy.fla" or "backup save file.fla" come after it.
    internal = ''
    if a.port_rom:
        with open(a.port_rom, 'rb') as f:
            internal = f.read(0x40)[0x20:0x34].decode('latin-1').strip().lower()
    stem = lambda p: os.path.splitext(os.path.basename(p))[0].lower().split('_cont_')[0]  # exact: "WWF No Mercy .fla" isn't it
    save_inputs.sort(key=lambda p: (not any(x.lower() in ('save', 'saves') for x in rel(p).split(os.sep)[:-1]),
                                    bool(internal) and stem(p) != internal,
                                    'backup' in os.path.basename(p).lower() or '(backup)' in p.lower(),
                                    rel(p).count(os.sep), p.lower()))   # then the shallowest (the bundle's own Save folder)
    if save_inputs:
        if saves_mod is None:
            report['parts']['saves'] = {'error': 'aki-save.py not found (AkiTools-fullconv-saves)'}
        else:
            out, skipped = [], []
            for p in save_inputs:
                src, tmp = p, None
                if p.lower().endswith('.mpk'):
                    pak, why = pak_image(open(p, 'rb').read())
                    if pak is None:
                        skipped.append({'from': rel(p), 'why': why})
                        continue
                    if why:
                        tmp = os.path.abspath(os.path.join(a.out, '_pak_' + os.path.basename(p)))
                        open(tmp, 'wb').write(pak)
                        src = tmp
                        skipped.append({'from': rel(p), 'note': why})
                try:
                    converted = port_files_compat(saves_mod, src, a.game)
                except (Exception, SystemExit) as e:  # aki-save's SaveError: e.g. another game's save in the folder
                    skipped.append({'from': rel(p), 'why': str(e)})
                    converted = []
                finally:
                    if tmp:
                        os.remove(tmp)
                for port_name, data, info in converted:
                    if 'saves/' + port_name in pkg_files:
                        skipped.append({'from': rel(p), 'why': 'another save already gives %s' % port_name})
                        continue
                    pkg_files['saves/' + port_name] = data
                    out.append({'file': 'saves/' + port_name, 'kind': info.get('kind', ''), 'from': rel(p),
                                'order': info.get('order', '')})
            if out:
                aki['components']['saves'] = [{'file': s['file'], 'kind': s['kind'], 'mode': 'seed'} for s in out]
                report['parts']['saves'] = out
            if skipped:
                report['parts']['saves_notes'] = skipped

    # Textures: a separate package (owner: big packs are their own packages). --textures names the pack folder when it
    # isn't inside the bundle folder (Showdown 64 keeps it in its Project64's Textures\).
    tex_files_in = files if not a.textures else [os.path.join(d, f) for d, _, fs in os.walk(a.textures) for f in fs]
    # A GLideN64 texture cache (.hts, e.g. N64Modderz64's mod ships only that) is exported to Rice-named PNGs.
    hts_files = [p for p in tex_files_in if p.lower().endswith('.hts')]
    if hts_files:
        rom_name = 'GAME'
        if a.port_rom:
            with open(a.port_rom, 'rb') as f:
                rom_name = f.read(0x40)[0x20:0x34].decode('latin-1').strip() or 'GAME'
        hts_dir = os.path.abspath(os.path.join(a.out, '_hts_' + cid))
        exported, hts_skipped = [], 0
        for p in hts_files:
            got, skipped_n = hts_to_rice_pngs(p, os.path.join(hts_dir, os.path.splitext(os.path.basename(p))[0]), rom_name)
            exported += got
            hts_skipped += skipped_n
        tex_files_in = list(tex_files_in) + exported
        report['parts']['hts'] = {'from': [rel(p) for p in hts_files], 'exported': len(exported), 'skipped': hts_skipped}
    pngs = [p for p in tex_files_in if p.lower().endswith('.png')]
    if pngs:
        rice = sum('#' in os.path.basename(p) for p in pngs)
        jabo = sum(bool(JABO_NAME.match(os.path.basename(p))) for p in pngs)
        naming = 'jabo' if jabo >= len(pngs) / 2 else ('rice' if rice >= len(pngs) / 2 else 'rt64')
        if naming == 'jabo':
            # Only the named images: Jabo packs also carry the artists' reference pictures. Rice-named ones stay
            # (rt64 matches both kinds in one pack).
            pngs = [p for p in pngs if JABO_NAME.match(os.path.basename(p)) or '#' in os.path.basename(p)]
        tex_name = ts_name(name) + '_Textures'
        tex_path = os.path.join(a.out, '%s-%s-%s.zip' % (ts_name(a.team), tex_name, a.version))
        common = os.path.commonpath([os.path.dirname(p) for p in pngs])
        # The images sit in a folder named after the package, with an rt64.json: the game opens that folder as a
        # texture-pack mod (a folder with rt64.json is one), so its mod id is unique per package.
        tex_dir = tex_name
        tex_files = {tex_dir + '/' + os.path.relpath(p, common).replace(os.sep, '/'): p for p in pngs}
        tex_files[tex_dir + '/rt64.json'] = json.dumps({'configuration': {'autoPath': naming, 'configurationVersion': 3,
                                                                          'hashVersion': 5}, 'textures': []}, indent=2).encode()
        tex_aki = {'format': 'aki-package', 'format_version': 1, 'kind': 'textures', 'game': a.game, 'naming': naming,
                   'folder': tex_dir, 'images': len(pngs)}
        write_package(tex_path, {'name': tex_name, 'version_number': a.version, 'website_url': a.url or '',
                                 'description': ('%s hi-res textures (%s naming, %d images)' % (name, naming, len(pngs)))[:250],
                                 'dependencies': []},
                      '# %s textures\n\n%d images, %s naming, from the mod\'s Project64 bundle.\n' % (name, len(pngs), naming),
                      tex_aki, tex_files)
        dep = '%s-%s' % (ts_name(a.team), tex_name)
        aki['components']['textures'] = {'package': dep, 'version': a.version, 'sha256': sha256_file(tex_path),
                                         'naming': naming, 'optional': False}
        report['parts']['textures'] = {'package': os.path.basename(tex_path), 'images': len(pngs), 'naming': naming,
                                       'size_mb': round(os.path.getsize(tex_path) / 1048576, 1)}
        extra_zips = [rel(p) for p in files if p.lower().endswith('.zip')]
        if extra_zips:
            report['parts']['textures']['not_imported'] = extra_zips

    # Docs the author shipped (per-wrestler CAW formulas and moves lists) go in as references.
    docs, docs_skipped = doc_files(files, folder)
    for p in docs:
        pkg_files['docs/' + rel(p).replace(os.sep, '/')] = p
    report['parts']['docs'] = {'files': len(docs), 'skipped': docs_skipped}

    # A pre-patched ROM in the bundle (Challenge 64 ships Roms\<hack>.z64): the patch is built from it against the
    # port's ROM, and the ROM itself never goes into the package.
    roms = [p for p in files if p.lower().endswith(('.z64', '.n64', '.v64')) and os.path.getsize(p) >= 0x800000]
    if roms and 'patch' not in aki['components']:
        port_rom = open(a.port_rom, 'rb').read() if a.port_rom else None
        for p in roms:
            hack = to_z64(open(p, 'rb').read())
            if hack is None or (port_rom is not None and hack == port_rom):
                continue
            summary = {'from': rel(p), 'size': len(hack), 'header': hack[0x20:0x34].decode('latin-1').strip(),
                       'header_revision': hack[0x3F], 'sha1': hashlib.sha1(hack).hexdigest().upper()}
            if port_rom is None:
                summary['note'] = 'not built: pass --port-rom (and --derelink) to build the patch from it'
            else:
                nrm, how = build_patch_nrm(a, hack, cid, name)
                summary['built_with'] = how
                pkg_files['patch.nrm'] = nrm
                aki['components']['patch'] = {'file': 'patch.nrm', 'mod_id': getattr(a, 'patch_mod_id', None) or cid + '_patch'}
                aki.setdefault('survey', {})['tier'] = 'bundled-rom'
                aki['survey']['notes'] = 'built locally from the bundled ROM (%s); the ROM itself is not included' % how
            report['parts']['patch'] = summary
            break

    # ROM patch (.rup, NINJA2). It patches the player's own cartridge: with --base-rom it is applied, and a patch for
    # another revision than the port's is de-relinked onto the port's ROM with Track A's tool (--derelink, --port-rom).
    # Without --base-rom the package records what's needed, and the import can be rerun once the player has that ROM.
    rups = [p for p in files if p.lower().endswith('.rup')]
    if rups and 'patch' not in aki['components']:
        patch = open(rups[0], 'rb').read()
        f = parse_rup(patch)[0]
        summary = rup_summary(f)
        summary['from'] = rel(rups[0])
        summary['tier'] = 'patch-data' if not any(int(mb) < 2 for mb in summary['changed_bytes_per_mb']) else 'patch-code'
        port = PORT_TAG.get(a.game, a.game)
        if a.base_rom:
            target = apply_rup(open(a.base_rom, 'rb').read(), patch)
            if summary['base'] != port and not a.derelink:
                sys.exit('the patch is for %s and the port runs %s: pass --derelink' % (summary['base'], port))
            nrm, summary['built_with'] = build_patch_nrm(a, target, cid, name)
            pkg_files['patch.nrm'] = nrm
            aki['components']['patch'] = {'file': 'patch.nrm', 'mod_id': getattr(a, 'patch_mod_id', None) or cid + '_patch'}
        else:
            aki['requires'] = {'patch': {'format': 'ninja2-rup', 'base': summary['base'], 'source_md5': summary['source_md5'],
                                         'note': 'Rerun the import with --base-rom <your %s ROM> to add the patch.' % summary['base']}}
            summary['note'] = 'not applied: needs the player\'s own %s ROM (--base-rom)' % summary['base']
        aki.setdefault('survey', {})['tier'] = summary['tier']
        aki['survey']['base'] = summary['base']
        report['parts']['patch'] = summary

    # A code pack's writes on game instructions do nothing as codes: they go into the patch (aki-roadmap#185).
    # (Only with a derelink layout for the port's ROM: without one there is nothing to bake against.)
    if 'codes.txt' in pkg_files and a.port_rom and a.derelink and aki['components']['codes']['rom'] == PORT_TAG.get(a.game, a.game) \
            and find_layout(a.derelink, open(a.port_rom, 'rb').read()) is not None:
        data, summary = bake_codes(pkg_files['codes.txt'].decode(), pkg_files.get('patch.nrm'), cid, name, a.port_rom, a.derelink, a.bake_exclude)
        report['parts']['code_bake'] = {k: v for k, v in summary.items() if k not in ('baked_groups', 'not_baked_groups')}
        if data is not None:
            baked = os.path.abspath(os.path.join(a.out, '%s.baked.nrm' % cid))
            with open(baked, 'wb') as f:
                f.write(data)
            pkg_files['patch.nrm'] = baked
            aki['components'].setdefault('patch', {'file': 'patch.nrm', 'mod_id': cid})
            aki.setdefault('survey', {})['code_bake'] = {k: summary[k] for k in ('writes_on_code', 'bytes_baked', 'not_baked', 'excluded')}

    if not aki['components']:
        sys.exit('nothing importable found in %s' % folder)
    deps = [aki['components']['textures']['package'] + '-' + a.version] if 'textures' in aki['components'] else []
    readme = '# %s\n\n%s\n\n## Credits\n%s\n\n## Permission\n%s\n\nImported with aki-conv from the Project64 bundle.\n' % (
        name, aki['description'], a.credits or '-', a.permission or '-')
    conv_path = os.path.join(a.out, '%s-%s-%s.zip' % (ts_name(a.team), ts_name(name), a.version))
    write_package(conv_path, {'name': ts_name(name), 'version_number': a.version, 'website_url': a.url or '',
                              'description': aki['description'][:250], 'dependencies': deps},
                  readme, aki, pkg_files)
    report['package'] = conv_path
    print(json.dumps(report, indent=2))


GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE, GL_TEXFMT_GZ = 0x8058, 0x1908, 0x1401, 0x80000000


def read_hts(path):
    """A GLideN64 texture storage file (.hts, TxFileStorage in src/GLideNHQ/TxCache.cpp) ->
    yields (texture crc, palette crc, n64 fmt or None, n64 siz or None, width, height, RGBA bytes or None).
    Current layout: int32 version 0x08000000, int32 config, int64 index offset; old layout (no version field): int32
    config, int64 index offset. Records: u32 width, u32 height, u32 GL format (bit 31: zlib), u16 texture format, u16
    pixel type, u8 hires flag, [u16 n64 fmt | siz << 8: current layout only], u32 data size, data. Index: int32 count,
    then (u64 checksum = palette << 32 | texture, i64 offset[:48] | fmt/siz << 48). Only RGBA8 data is decoded (the
    pixels are None for other formats)."""
    with open(path, 'rb') as f:
        first, = struct.unpack('<i', f.read(4))
        old = first != 0x08000000
        if old:
            index_pos, = struct.unpack('<q', f.read(8))
        else:
            _, index_pos = struct.unpack('<iq', f.read(12))
        f.seek(index_pos)
        count, = struct.unpack('<i', f.read(4))
        index = [struct.unpack('<Qq', f.read(16)) for _ in range(count)]
        for checksum, packed in index:
            f.seek(packed & 0xFFFFFFFFFFFF)
            if old:
                w, h, glf, tf, pt, _, size = struct.unpack('<IIIHHBI', f.read(21))
                fmt = siz = None
            else:
                w, h, glf, tf, pt, _, fs, size = struct.unpack('<IIIHHBHI', f.read(23))
                fmt, siz = fs & 0xFF, fs >> 8
            data = f.read(size)
            if glf & GL_TEXFMT_GZ:
                data = zlib.decompress(data)
            rgba = data if ((glf & ~GL_TEXFMT_GZ) == GL_RGBA8 and tf == GL_RGBA and pt == GL_UNSIGNED_BYTE
                            and len(data) == w * h * 4) else None
            yield checksum & 0xFFFFFFFF, checksum >> 32, fmt, siz, w, h, rgba


def png_rgba(w, h, rgba):
    """An RGBA8 image as PNG bytes (no filtering; zlib only, so no third-party modules)."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    stride = w * 4
    raw = b''.join(b'\0' + rgba[y * stride:(y + 1) * stride] for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def hts_to_rice_pngs(path, out_dir, rom_name):
    """Export a .hts texture cache as Rice-named PNGs rt64 matches: <rom>#crc#fmt#siz[#pal]_all.png, or crc#$#$[#pal]
    when the cache doesn't record the format and size (old layout). Returns (written paths, skipped count)."""
    os.makedirs(out_dir, exist_ok=True)
    out, skipped = [], 0
    for crc, pal, fmt, siz, w, h, rgba in read_hts(path):
        if rgba is None or not crc:
            skipped += 1
            continue
        form = '$#$' if fmt is None else '%X#%X' % (fmt, siz)
        name = '%s#%08X#%s%s_all.png' % (rom_name, crc, form, '#%08X' % pal if pal else '')
        p = os.path.join(out_dir, name)
        with open(p, 'wb') as f:
            f.write(png_rgba(w, h, rgba))
        out.append(p)
    return out, skipped


def to_z64(rom):
    """A cartridge image in .z64 (big-endian) order, from .z64, .v64 (byte-swapped) or .n64 (little-endian words);
    None when the header isn't a known one."""
    head = rom[:4]
    if head == b'\x80\x37\x12\x40':
        return rom
    if head == b'\x37\x80\x40\x12':
        b = bytearray(rom)
        b[0::2], b[1::2] = rom[1::2], rom[0::2]
        return bytes(b)
    if head == b'\x40\x12\x37\x80':
        b = bytearray(rom)
        for i in range(4):
            b[i::4] = rom[3 - i::4]
        return bytes(b)
    return None


def run_main(module, argv):
    """A tool's main(argv) in this process (one process per batch, aki-roadmap#111): (ok, its printed output)."""
    import contextlib
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            module.main(argv)
        return True, out.getvalue()
    except SystemExit as e:
        if e.code not in (None, 0):
            out.write('\n%s\n' % e.code)
            return False, out.getvalue()
        return True, out.getvalue()


def load_sibling(name):
    path = os.path.join(TOOLS, name + '.py')
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_patch_nrm(a, hack, cid, name):
    """patch.nrm for a hacked ROM (bytes, .z64 order) against --port-rom. With a derelink layout for the port's ROM,
    derelink builds it (in-place hacks, hacks with added code and relinked ones alike); otherwise make_patch_mod
    (in-place edits only; it reads the game repo's RecompiledFuncs next to --port-rom). Both run in this process. The
    hacked ROM is written to a temporary file for the tool and deleted right after. a.patch_mod_id names the patch
    mod (default <id>_patch). Returns (nrm path, 'derelink' or 'patch-mod')."""
    if not a.port_rom:
        sys.exit('pass --port-rom (the port ROM, at its game repo root)')
    port_rom = open(a.port_rom, 'rb').read()
    tmp = os.path.abspath(os.path.join(a.out, '%s.patched.z64' % cid))
    nrm = os.path.abspath(os.path.join(a.out, '%s.nrm' % cid))
    repo = os.path.dirname(os.path.abspath(a.port_rom))
    derelink = a.derelink or DERELINK
    mod_id = getattr(a, 'patch_mod_id', None) or cid + '_patch'
    use_derelink = os.path.isfile(derelink) and find_layout(derelink, port_rom) is not None
    if use_derelink:
        tool = load_sibling('derelink') if os.path.abspath(derelink) == DERELINK else _load_path(derelink)
        argv = ['build', '--base', a.port_rom, '--hack', tmp, '--id', mod_id, '--name', name, '--out', nrm]
    else:
        tool = load_sibling('make_patch_mod')
        argv = ['--base', a.port_rom, '--hack', tmp, '--game-id', GAME_MOD_ID[a.game], '--id', mod_id, '--name', name,
                '--out', nrm, '--overlays', os.path.join(repo, 'RecompiledFuncs', 'recomp_overlays.inl'),
                '--repaired', os.path.join(repo, 'RecompiledFuncs', 'repaired_functions.txt')]
    open(tmp, 'wb').write(hack)
    try:
        ok, log = run_main(tool, argv)
        if not ok or not os.path.exists(nrm):
            sys.exit('building the patch mod failed:\n' + log[-4000:])
    finally:
        os.remove(tmp)  # the hacked ROM never leaves this step
    return nrm, 'derelink' if use_derelink else 'patch-mod'


def _load_path(path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MPK_BLOCK = 0x8000


def pak_image(data):
    """A Controller Pak image from a Project64 .mpk: 32 KB as it is. Project64 also writes 256 KB files (8 x 32 KB
    banks); when every bank after the first is blank (a uniform fill, or the same freshly formatted empty pak as the
    other blank banks) the first 32 KB is the pak. Banks with data of their own are not guessed at.
    Returns (bytes, note) or (None, why)."""
    if len(data) == MPK_BLOCK:
        return data, ''
    n = len(data) // MPK_BLOCK
    if len(data) % MPK_BLOCK or n not in (2, 4, 8):
        return None, 'unexpected .mpk size %d' % len(data)
    banks = [data[i * MPK_BLOCK:(i + 1) * MPK_BLOCK] for i in range(n)]
    counts = collections.Counter(banks[1:])
    common, times = counts.most_common(1)[0]
    blank = lambda b: b.count(b[0]) == MPK_BLOCK or (b == common and times >= 2)
    used = [i for i in range(1, n) if not blank(banks[i])]
    if used:
        return None, '%d KB .mpk with data in bank(s) %s besides the first: not imported (which one the mod means is ' \
                     'unclear)' % (len(data) // 1024, ', '.join(map(str, used)))
    return banks[0], 'first 32 KB of a %d KB Project64 .mpk (the other banks are blank)' % (len(data) // 1024)


DOC_SKIP_DIRS = {'plugin', 'plugins', 'config', 'lang', 'logs', 'save', 'saves', 'screenshots', 'textures',
                 'hires_texture', 'cache', 'roms', 'tools', 'scripts'}
DOC_LIMIT = 40


def texture_dirs(files, min_images=5):
    """Folders that are part of a texture pack: those holding at least `min_images` images (a stray screenshot next to
    the readme doesn't make the bundle root one)."""
    counts = collections.Counter(os.path.dirname(p) for p in files if p.lower().endswith(('.png', '.dds', '.bmp')))
    return {d for d, n in counts.items() if n >= min_images}


def in_dirs(path, dirs):
    """path is inside one of dirs (or directly in it)."""
    d = os.path.dirname(path)
    return any(d == x or d.startswith(x + os.sep) for x in dirs)


def doc_files(files, folder):
    """The author's docs: .txt files that aren't in a texture folder (texture_dirs) or a Project64 install folder
    (Plugin, Config, Lang, ...). When there are more than DOC_LIMIT, only those nearest the top are kept.
    Returns (kept, skipped count)."""
    tex_dirs = texture_dirs(files)
    out = []
    for p in files:
        if not p.lower().endswith('.txt'):
            continue
        parts = os.path.relpath(p, folder).split(os.sep)[:-1]
        if in_dirs(p, tex_dirs) or any(d.lower() in DOC_SKIP_DIRS for d in parts):
            continue
        out.append(p)
    out.sort(key=lambda p: (os.path.relpath(p, folder).count(os.sep), p.lower()))
    return out[:DOC_LIMIT], max(0, len(out) - DOC_LIMIT) + (sum(1 for p in files if p.lower().endswith('.txt')) - len(out))


def port_files_compat(saves_mod, path, game):
    """aki-save port_files -> [(port file name, raw bytes, info)]."""
    out, info = saves_mod.port_files(path, game)
    return [(saves_mod.port_name(game, target), raw, dict(extra, order=info.get('order', ''))) for target, raw, extra in out]


def parse_links(specs):
    """["Label=https://..."] -> aki.json links ([{"label", "url"}]); the label is everything before the first '='."""
    links = []
    for spec in specs:
        label, sep, url = spec.partition('=')
        if not sep or not url.startswith(('https://', 'http://')):
            raise SystemExit('--link wants "Label=https://...", got %r' % spec)
        links.append({'label': label.strip(), 'url': url.strip()})
    return links


# ------------------------------------------------------------------------------------ bake-codes

def repaired_functions(port_rom_path):
    """[(RAM address, overlay name or None)] of the functions the port repaired offline (live recompiling one drops the
    repair), from RecompiledFuncs/repaired_functions.txt next to the port's ROM. Empty when there's no list."""
    path = os.path.join(os.path.dirname(os.path.abspath(port_rom_path)), 'RecompiledFuncs', 'repaired_functions.txt')
    if not os.path.isfile(path):
        return []
    found = []
    for line in open(path, encoding='utf-8'):
        m = re.match(r'^\w*?_([0-9A-Fa-f]{8})(?:_(ovl_\w+))?\s*$', line)
        if m:
            found.append((int(m[1], 16), m[2]))
    return found


def bake_codes(codes_text, nrm, cid, name, port_rom_path, derelink, exclude=()):
    """Bakes the writes on game instructions in codes_text into a patch (aki-roadmap#185). nrm: the pack's patch .nrm
    or None. exclude: groups whose name contains one of these stay out (a code that needs a code that can't be baked).
    Returns (the new .nrm's bytes or None when nothing changes, the summary for the report)."""
    port_rom = open(port_rom_path, 'rb').read()
    layout = find_layout(derelink, port_rom)
    if layout is None:
        sys.exit('no derelink layout for this ROM: bake-codes needs one')
    sys.path.insert(0, os.path.dirname(os.path.abspath(derelink)))
    import bps
    base = port_rom
    if nrm:
        with zipfile.ZipFile(nrm) as z:
            base = bytes(bps.bps_apply(port_rom, z.read('patch.bps')))
    units = [u for u in aki_conv_bake.parse_units(codes_text) if not any(e in u.group for e in exclude)]
    report = aki_conv_bake.bake(units, layout, base)
    gaps = {reason: sum(groups.values()) for reason, groups in report.gaps.items()}
    changed = [(0x80000000 + lo, n) for n, fs in report.functions.items() for lo, _ in fs]
    repaired = repaired_functions(port_rom_path)
    summary = {'writes_on_code': report.code_writes, 'bytes_baked': report.baked_bytes, 'excluded': list(exclude),
               'functions': sorted('%s:%08X' % (n, a) for a, n in changed),
               'baked_groups': report.baked_groups, 'not_baked': gaps, 'not_baked_groups': report.gaps, 'other_overlay_only': sum(report.inert.values()),
               'guards_flipped': report.guard_flips,
               'ram_jumps': ['%s:%08X -> %08X' % (n, 0x80000000 + a, 0x80000000 + to) for n, a, to in report.ram_jumps],
               'may_drop_repair': sorted('%s:%08X' % (n, a) for a, n in changed
                                      if any(a == ra and ro in (None, n) for ra, ro in repaired))}
    if report.patched == base:
        return None, summary
    patch = bps.bps_encode(port_rom, report.patched)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as new:
        if nrm:
            with zipfile.ZipFile(nrm) as z:
                for item in z.infolist():
                    if item.filename != 'patch.bps':
                        new.writestr(item, z.read(item.filename))
        else:
            new.writestr('mod.json', json.dumps({
                'game_id': layout['mod_game_id'], 'id': cid, 'display_name': name,
                'description': '%s: the code pack\'s edits to game instructions, as a patch to your own ROM.' % name,
                'short_description': 'ROM patch (patches your own ROM when the game starts).', 'version': '1.0.0',
                'authors': ['unknown'], 'minimum_recomp_version': '0.1.0'}, indent=4))
        new.writestr('patch.bps', patch)
        # A baked jump into code the runtime only finds at run time: a relink.json turns that on (load_relink).
        if report.ram_jumps and 'relink.json' not in new.namelist():
            new.writestr('relink.json', json.dumps({'format': 1, 'base': layout.get('manifest_base', '')}))
    return out.getvalue(), summary


def cmd_bake_codes(a):
    """An installed or unpacked pack's folder: its codes' writes on game instructions go into its patch.nrm."""
    aki_path = os.path.join(a.folder, 'aki.json')
    aki = json.load(open(aki_path, encoding='utf-8-sig'))
    comp = aki.get('components', {})
    if 'codes' not in comp:
        sys.exit('%s has no codes' % a.folder)
    port = PORT_TAG.get(aki['game'], aki['game'])
    if comp['codes'].get('rom') != port:
        sys.exit('its codes are for %s and the port runs %s: translate them first (translate-codes)' % (comp['codes'].get('rom'), port))
    text = open(os.path.join(a.folder, comp['codes']['file']), encoding='utf-8-sig', errors='replace').read()
    patch = comp.get('patch', {}).get('file')
    nrm_path = os.path.join(a.folder, patch or 'patch.nrm')
    data, summary = bake_codes(text, nrm_path if patch else None, aki['id'], aki['name'], a.port_rom, a.derelink, a.exclude)
    if a.report:
        with open(a.report, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=1)
    if data is not None and not a.dry_run:
        with open(nrm_path + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(nrm_path + '.tmp', nrm_path)
        if not patch:
            comp['patch'] = {'file': 'patch.nrm', 'mod_id': aki['id']}
        aki.setdefault('survey', {})['code_bake'] = {k: summary[k] for k in ('writes_on_code', 'bytes_baked', 'not_baked', 'excluded')}
        with open(aki_path, 'w', encoding='utf-8') as f:
            json.dump(aki, f, indent=2)
    brief = {k: v for k, v in summary.items() if k not in ('baked_groups', 'not_baked_groups')}
    print(json.dumps(dict(brief, id=aki['id'], changed=data is not None, dry_run=a.dry_run), indent=1))


def cmd_pack_nrm(a):
    """An existing patch-mod .nrm (e.g. from make_patch_mod.py) -> a patch conversion package."""
    with zipfile.ZipFile(a.nrm) as z:
        mod = json.loads(z.read('mod.json'))
    # The runtime refuses to open a mod whose version isn't x.y.z ("Invalid version string in mod.json"), and the
    # conversion would then run without its patch.
    if not re.match(r'^\d+\.\d+\.\d+$', str(mod.get('version', ''))):
        raise SystemExit('%s: mod.json version %r is not x.y.z; the game would refuse to open it (rebuild the .nrm with '
                         '--version 1.0.0)' % (a.nrm, mod.get('version')))
    aki = {'format': 'aki-conversion', 'format_version': 1, 'id': a.id, 'name': a.name, 'version': a.version,
           'authors': a.author or mod.get('authors', ['unknown']), 'game': a.game,
           'base_rom': {'sha1': stock_sha1(a.game), 'revision': PORT_TAG.get(a.game, a.game)},
           'description': a.description or mod.get('description', ''), 'credits': a.credits,
           'permission': a.permission, 'links': parse_links(a.link), 'thumb': 'icon.png',
           'survey': {'tier': a.tier}, 'components': {'patch': {'file': 'patch.nrm', 'mod_id': mod.get('id', '')}},
           'source': {'mode': 'byo'}}
    readme = '# %s\n\nROM patch conversion (patches your own ROM when the game starts).\n' % a.name
    if a.readme:
        readme += '\n' + open(a.readme, encoding='utf-8-sig').read()
    files = {'patch.nrm': a.nrm}
    if a.icon:
        files['icon.png'] = a.icon
    out = os.path.join(a.out, '%s-%s-%s.zip' % (ts_name(a.team), ts_name(a.name), a.version))
    write_package(out, {'name': ts_name(a.name), 'version_number': a.version, 'website_url': a.url or '',
                        'description': (aki['description'] or a.name)[:250], 'dependencies': []},
                  readme, aki, files)
    print(json.dumps({'package': out, 'patch_mod_id': mod.get('id')}))


def overlay_guard_setup(a):
    """(overlays, guard, note) for translate_codes from --map (a derelink map), --derelink (its layouts/) and
    --port-rom; (None, None, why not) when any is missing."""
    m = json.load(open(a.map))
    if 'overlays' not in m:
        return None, None, 'map has no overlays'
    if not (getattr(a, 'derelink', None) and getattr(a, 'port_rom', None)):
        return None, None, 'off: pass --derelink and --port-rom to split overlay-RAM codes'
    port_rom = open(a.port_rom, 'rb').read()
    layout = find_layout(a.derelink, port_rom)
    if layout is None:
        return None, None, 'off: no derelink layout matches the port ROM'
    return overlay_ranges(m), overlay_guard(layout, port_rom), 'on (layout %s)' % layout.get('game', '?')


def load_map(path):
    """A revision map: ours ({"ranges": [{"start", "end", "delta"}]}, hex strings) or Track A's derelink map.json."""
    m = json.load(open(path))
    if 'ranges' in m:
        return [(int(r['start'], 16), int(r['end'], 16), int(r['delta'], 16)) for r in m['ranges']]
    return map_from_derelink(m)


def cmd_translate(a):
    ranges = load_map(a.map)
    overlays, guard, note = overlay_guard_setup(a)
    text, done, bad = translate_codes(open(a.codes, encoding='utf-8').read(), ranges, overlays, guard)
    if a.to:
        # Every code left is at the target's addresses now: retag the file, or the engine would translate it again.
        text, n = re.subn(r'(?m)^# rom: *(\S+)', lambda m: '# rom: %s\n# translated from %s by aki-conv (revision map)'
                          % (a.to, m.group(1)), text, count=1)
        if not n:
            text = '# rom: %s\n' % a.to + text
    open(a.out, 'w', encoding='utf-8').write(text)
    print(json.dumps({'translated_codes': done, 'dropped_lines': len(bad),
                      'units_split_by_overlay': translate_codes.last['units_split'], 'overlay_guards': note}))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('import-pj64', help='Project64 mod bundle -> conversion package (+ texture package)')
    p.add_argument('folder')
    p.add_argument('--game', default='nomercy')
    p.add_argument('--id', required=True, help='conversion id ([a-z0-9_.-])')
    p.add_argument('--name', required=True, help='display name (also finds the mod\'s cheats in a big .cht)')
    p.add_argument('--team', default='Community', help='Thunderstore team (the author)')
    p.add_argument('--version', default='1.0.0')
    p.add_argument('--author', action='append')
    p.add_argument('--description', default='')
    p.add_argument('--credits', default='')
    p.add_argument('--permission', default='')
    p.add_argument('--url', default='')
    p.add_argument('--include', help='regex over cheat names instead of the mod-name folder rule')
    p.add_argument('--all-cheats', action='store_true',
                   help='import every cheat of the mod, not only those its Project64.cfg has ticked')
    p.add_argument('--map', help='revision map JSON to translate the codes: Track A\'s derelink map (with --derelink and '
                                 '--port-rom, overlay-RAM codes are split by a guard too) or {"ranges": [{"start","end","delta"}]}')
    p.add_argument('--textures', help='the hi-res texture folder, when it isn\'t inside FOLDER')
    p.add_argument('--base-rom', help='your own ROM the bundle\'s .rup patch is for (applied, MD5-checked)')
    p.add_argument('--port-rom', help='the port\'s ROM (base for de-relinking a patch made for another revision)')
    p.add_argument('--derelink', default=DERELINK, help='derelink.py (default: the one next to this file)')
    p.add_argument('--bake-exclude', action='append', default=[], help='code groups whose name contains this stay out of the patch (see bake-codes)')
    p.add_argument('--out', default='.', help='output folder for the package zip(s)')
    p.set_defaults(func=cmd_import_pj64)
    p = sub.add_parser('pack-nrm', help='an existing patch-mod .nrm -> patch conversion package')
    p.add_argument('nrm')
    p.add_argument('--game', default='nomercy')
    p.add_argument('--id', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--team', default='Community')
    p.add_argument('--version', default='1.0.0')
    p.add_argument('--author', action='append')
    p.add_argument('--description', default='')
    p.add_argument('--credits', default='')
    p.add_argument('--permission', default='')
    p.add_argument('--url', default='')
    p.add_argument('--tier', default='patch-code')
    p.add_argument('--readme', help='Markdown file appended to the package README (credits, warnings, notes)')
    p.add_argument('--link', action='append', default=[], help='"Label=https://..." for aki.json links (repeatable)')
    p.add_argument('--icon', help='a 256x256 PNG for icon.png (default: a generated one)')
    p.add_argument('--out', default='.')
    p.set_defaults(func=cmd_pack_nrm)
    p = sub.add_parser('bake-codes', help='a pack folder: its codes\' writes on game instructions go into its patch.nrm')
    p.add_argument('folder')
    p.add_argument('--port-rom', required=True, help='the port\'s ROM, at its game repo root')
    p.add_argument('--derelink', default=DERELINK, help='derelink.py (its layouts/ and bps.py; default: next to this file)')
    p.add_argument('--report', help='write the full report (per group) to this JSON file')
    p.add_argument('--dry-run', action='store_true', help='report only')
    p.add_argument('--exclude', action='append', default=[], help='leave out groups whose name contains this (repeatable)')
    p.set_defaults(func=cmd_bake_codes)
    p = sub.add_parser('translate-codes', help='rewrite codes.txt addresses through a revision map')
    p.add_argument('codes')
    p.add_argument('--map', required=True)
    p.add_argument('--derelink', default=DERELINK, help='derelink.py (its layouts/), to split overlay-RAM codes by a guard')
    p.add_argument('--port-rom', help='the port\'s ROM (finds the layout; the guards are read from it)')
    p.add_argument('--to', help='the revision tag the codes are at afterwards (e.g. nomercy-rev1): retags "# rom:"')
    p.add_argument('-o', '--out', required=True)
    p.set_defaults(func=cmd_translate)
    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
