"""aki-import: sort a pile of downloads into AKI conversions (aki-roadmap#228, phase 1: the engine without the launcher).

Subcommands (python aki_import.py <cmd> -h):
  plan   PATH...   scan files, folders and archives; say what each item is, which game, and what would be done with
                   it (a table, or --json). Reads only; with --ledger it also records the results.
  worker           the launcher's entry point: one process per batch (aki-roadmap#111: process starts leak pool on
                   this machine class). Reads one JSON job from stdin, writes one JSON line per item to stdout, then a
                   "done" line. Job: {"paths": [...], "roms": {"<game>": "<port ROM path>"} | "rom_dirs": [...],
                   "ledger": "<path>", "catalog": "<path>", "sevenzip": "<7zr.exe>", "work": "<temp folder>"}.

What an item can be (kind): rom, patch, nrm (a patch mod), package (aki-package.json / aki.json), pj64-bundle (a
Project64 folder with the mod's cheats, save and textures), texture-pack (rt64 / rice / jabo / hts naming), save,
codes, unknown. Archives (zip read in memory; 7z unpacked once by 7zr) are opened and their contents sorted.

Result status per item:
  ready        identified; `route` says which converter builds it (make_patch_mod / derelink after `aki-hack survey
               --layout`, import-pj64, texture wrap, install). Unknown hacks are `tier: "untested"` (#224, #172).
  stock        the unmodified game: nothing to convert
  have-it      the ledger already has this exact input
  needs-rom    a patch whose base ROM isn't among the ROMs given (`needs` names it when the patch says which)
  unsupported  not one of the six AKI games, another region, a format we don't read yet (`why`)
  failed       something broke (`why`)

Patch files are applied by patchfmt.py next to this file (aki-roadmap#228: detect(patch) and
apply(patch, source) -> (target, info), raising PatchError); without it patches are still recognised by their magic
but reported as failed. Stdlib only; run with python -I. Writes nothing but --ledger / --json and, for 7z archives,
files under --work. Never writes ROM bytes anywhere else.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ENGINE_VERSION = '0.1.0'

# The six ports: header game code (0x3B, media + id + region) and the port ROM's SHA-1 (.z64 order), as AKI
# Launcher's assets/games.json. A ROM with this SHA-1 is the stock game.
GAMES = {
    'wt':      dict(code='NWNE', title='WCW vs. nWo World Tour', sha1='5AD2D8359058C8BB71F08E3D3433B7A50D3BB645'),
    'revenge': dict(code='NW2E', title='WCW/nWo Revenge', sha1='E1711A2511394B9357B5F1AC8CA5CC17BD674836'),
    'wm2k':    dict(code='NWXE', title='WWF WrestleMania 2000', sha1='442D417A52ED672CA1A47E7261A5414DEBB1E27A'),
    'vpw64':   dict(code='NVPJ', title='Virtual Pro Wrestling 64', sha1='F9E9FA2ED819C3A39DB5CB6AFECA186F021DB5ED'),
    'nomercy': dict(code='NW4E', title='WWF No Mercy', sha1='91CEE3D096F4A76644D8B35B9AEAD6448909ABD1'),
    'vpw2':    dict(code='NA2J', title='Virtual Pro Wrestling 2', sha1='82DD25A044689EAB57AB362FE10C0DA6388C217A'),
}
# Other releases of the same games: refused by name (the ports need the region above).
OTHER_REGIONS = {'NWNP': ('wt', 'PAL'), 'NW2P': ('revenge', 'PAL'), 'NWXJ': ('wm2k', 'Japanese'),
                 'NWXP': ('wm2k', 'PAL'), 'NW4P': ('nomercy', 'PAL')}
# Stock dumps that aren't the port's ROM but are a known base for patches, by CRC32 / MD5 of the whole .z64 image.
OTHER_DUMPS_CRC32 = {'B33F44F0': ('nomercy', 'WWF No Mercy (USA) 1.0')}
OTHER_DUMPS_MD5 = {'04c492be7f89fc6f425238bd67629544': ('nomercy', 'WWF No Mercy (USA) 1.0')}

ROM_EXTS = ('.z64', '.n64', '.v64', '.rom', '.bin')
SAVE_EXTS = {'.fla': 'flash', '.sra': 'sram', '.eep': 'eeprom', '.mpk': 'controller-pak', '.srm': 'mupen'}
CODE_EXTS = ('.cht', '.gsc')
IMAGE_EXTS = ('.png', '.dds', '.bmp')
RICE_NAME = re.compile(r'^(?P<rom>.+?)#[0-9A-Fa-f]{8}#\d#\d(#[0-9A-Fa-f]{8})?_(all|rgb|a|ciByRGBA|allciByRGBA)\.(png|dds|bmp)$')
JABO_NAME = re.compile(r'^[0-9A-Fa-f]{32}\.(png|dds|bmp)$')
RT64_NAME = re.compile(r'^[0-9A-Fa-f]{16}\.(png|dds)$')
PJ64_MARKERS = ('project64.cht', 'project64.rdb', 'project64.cfg', 'project64.exe')
MIN_PACK_IMAGES = 5

# Fallback magic when romhack/patchfmt.py isn't there: enough to say "a patch" and its format.
PATCH_MAGIC = ((b'BPS1', 'bps'), (b'UPS1', 'ups'), (b'PATCH', 'ips'), (b'APS10', 'aps'), (b'\xd6\xc3\xc4', 'xdelta'),
               (b'NINJA', 'rup'), (b'PPF', 'ppf'))
PATCH_EXTS = ('.bps', '.ups', '.ips', '.aps', '.xdelta', '.vcdiff', '.delta', '.rup', '.ppf')


class ImportError_(Exception):
    """A problem with the job itself (bad arguments); printed without a traceback."""


# ------------------------------------------------------------------------------------ helpers

def load_patchfmt(path=None):
    """Worker Ant's romhack/patchfmt.py (detect, apply, PatchError), or None when it isn't installed."""
    candidates = [path] if path else []
    if os.environ.get('AKI_PATCHFMT'):
        candidates.append(os.environ['AKI_PATCHFMT'])
    candidates.append(os.path.join(TOOLS, 'patchfmt.py'))
    for c in candidates:
        if c and os.path.isfile(c):
            spec = importlib.util.spec_from_file_location('patchfmt', c)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


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


def rom_header(rom):
    return {'name': rom[0x20:0x34].decode('ascii', 'replace').strip(), 'code': rom[0x3B:0x3F].decode('ascii', 'replace'),
            'revision': rom[0x3F]}


def game_for_code(code):
    """(game key, None) for a port's region, (game key, region) for another release, (None, None) otherwise."""
    for key, g in GAMES.items():
        if g['code'] == code:
            return key, None
    if code in OTHER_REGIONS:
        return OTHER_REGIONS[code]
    return None, None


def read_file(path):
    with open(path, 'rb') as f:
        return f.read()


def slug(text):
    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return s[:48].strip('-') or 'import'


def unique_id(base, taken):
    """base, or base-2, base-3 ... so it's not in taken (a set, updated)."""
    cid, n = base, 2
    while cid in taken:
        cid = '%s-%d' % (base, n)
        n += 1
    taken.add(cid)
    return cid


def display_name(path):
    """A readable name from a file or folder name: no extension, separators to spaces."""
    base = os.path.basename(path.rstrip('/\\'))
    stem, ext = os.path.splitext(base)
    if ext.lower() in ROM_EXTS + PATCH_EXTS + ('.zip', '.7z', '.nrm'):
        base = stem
    return re.sub(r'[_]+', ' ', base).strip() or base


def patch_format(data, patchfmt):
    if patchfmt is not None:
        try:
            fmt = patchfmt.detect(data)
            if fmt:
                return fmt if isinstance(fmt, str) else str(fmt)
        except Exception:
            pass
    for magic, fmt in PATCH_MAGIC:
        if data.startswith(magic):
            return fmt
    return None


def texture_naming(names):
    """('rt64'|'rice'|'jabo'|'hts', count, rom name hint) for a list of file names (base names), or None."""
    base = [n.replace('\\', '/').rsplit('/', 1)[-1] for n in names]
    lower = [b.lower() for b in base]
    if 'rt64.json' in lower:
        return 'rt64', len(base), None
    if any(b.endswith(('.hts', '.htc')) for b in lower):
        return 'hts', sum(b.endswith(('.hts', '.htc')) for b in lower), None
    rice = [RICE_NAME.match(b) for b in base]
    rice = [m for m in rice if m]
    jabo = sum(bool(JABO_NAME.match(b)) for b in base)
    rt64 = sum(bool(RT64_NAME.match(b)) for b in base)
    best = max(len(rice), jabo, rt64)
    if best < MIN_PACK_IMAGES:
        return None
    if best == len(rice):
        roms = {}
        for m in rice:
            roms[m.group('rom')] = roms.get(m.group('rom'), 0) + 1
        return 'rice', len(rice), max(roms, key=roms.get)
    if best == jabo:
        return 'jabo', jabo, None
    return 'rt64', rt64, None


# ------------------------------------------------------------------------------------ inputs

class Source:
    """One input: a file on disk, a member of a zip (read in memory), or a folder (bundle / texture pack)."""

    def __init__(self, label, kind_hint, reader=None, names=None, path=None):
        self.label = label          # what the results show: path, or archive!member
        self.kind_hint = kind_hint  # 'file' or 'folder'
        self.reader = reader        # () -> bytes, for files
        self.names = names or []    # relative file names, for folders
        self.path = path            # the folder or file on disk, when there is one
        self._data = None

    def data(self):
        if self._data is None:
            self._data = self.reader()
        return self._data


def folder_files(root):
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), root).replace('\\', '/'))
    return sorted(out)


def is_bundle(names):
    """A Project64 folder: its markers at the top or in Config/ (Config/Project64.cfg), not deeper, so a
    folder that holds several bundles isn't taken as one."""
    lower = [n.lower() for n in names]
    return any(n in PJ64_MARKERS or (n.startswith('config/') and n[7:] in PJ64_MARKERS) for n in lower)


def is_texture_folder(names):
    """Texture-named images and no ROMs, patches or archives beside them (those make it a folder of items)."""
    if any(os.path.splitext(n.lower())[1] in ROM_EXTS + PATCH_EXTS + ('.zip', '.7z', '.rar', '.nrm') for n in names):
        return False
    return texture_naming(names) is not None


def expand(path, job, sources, notes):
    """Turn one picked path into Sources. Bundles and texture packs stay one item; other folders are walked."""
    if os.path.isdir(path):
        names = folder_files(path)
        if is_bundle(names) or is_texture_folder(names):
            sources.append(Source(path, 'folder', names=names, path=path))
            return
        for entry in sorted(os.listdir(path)):
            expand(os.path.join(path, entry), job, sources, notes)
        return
    if not os.path.isfile(path):
        notes.append(dict(item=path, status='failed', why='not found'))
        return
    with open(path, 'rb') as f:
        head = f.read(8)
    lower = path.lower()
    if head.startswith(b'PK\x03\x04') and not lower.endswith('.nrm'):
        expand_zip(path, read_file(path), path, sources, notes)
    elif head.startswith(b"7z\xbc\xaf\x27\x1c"):
        expand_7z(path, job, sources, notes)
    elif head.startswith(b'Rar!'):
        notes.append(dict(item=path, status='unsupported', why='RAR archive: extract it first, then import the folder'))
    else:
        sources.append(Source(path, 'file', reader=lambda p=path: read_file(p), path=path))


def expand_zip(label, blob, origin, sources, notes, depth=0):
    """Members are read in memory (nothing is extracted, so no zip-slip). A zip that is a bundle or texture pack is
    one item; otherwise every ROM / patch / mod inside is its own item."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        members = [i for i in z.infolist() if not i.is_dir()]
    except zipfile.BadZipFile as e:
        notes.append(dict(item=label, status='failed', why='bad zip: %s' % e))
        return
    names = [m.filename for m in members]
    lower = {n.lower().rsplit('/', 1)[-1] for n in names}
    if 'mod.json' in lower or 'aki-package.json' in lower or 'aki.json' in lower:
        sources.append(Source(label, 'file', reader=lambda b=blob: b, path=origin))
        return
    if is_bundle(names) or is_texture_folder(names):
        src = Source(label, 'folder', names=names, path=origin)
        src.zip_blob = blob
        sources.append(src)
        return
    for m in members:
        member_label = '%s!%s' % (label, m.filename)
        ext = os.path.splitext(m.filename.lower())[1]
        if ext == '.zip' and depth < 2:
            expand_zip(member_label, z.read(m), origin, sources, notes, depth + 1)
        elif ext in ROM_EXTS + PATCH_EXTS + ('.nrm',) or m.file_size >= 0x100000:
            sources.append(Source(member_label, 'file', reader=lambda z=z, m=m: z.read(m), path=origin))


def find_7zr(job):
    for c in (job.get('sevenzip'), os.path.join(TOOLS, 'bin', '7zr.exe'), shutil.which('7zr'), shutil.which('7z')):
        if c and os.path.isfile(c):
            return c
    return None


def expand_7z(path, job, sources, notes):
    exe = find_7zr(job)
    if not exe:
        notes.append(dict(item=path, status='unsupported', why='7z archive: 7zr.exe not found (bin\\7zr.exe or --sevenzip)'))
        return
    work = job.get('work') or tempfile.mkdtemp(prefix='aki-import-')
    out = os.path.join(work, '7z-%s' % hashlib.sha1(os.path.abspath(path).encode('utf-8')).hexdigest()[:12])
    if not os.path.isdir(out):
        os.makedirs(out, exist_ok=True)
        r = subprocess.run([exe, 'x', '-y', '-bd', '-o' + out, path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            notes.append(dict(item=path, status='failed', why='7zr exit %d' % r.returncode))
            return
    root = os.path.realpath(out)
    for dirpath, _, files in os.walk(out):
        for f in files:
            if not os.path.realpath(os.path.join(dirpath, f)).startswith(root + os.sep):
                notes.append(dict(item=path, status='failed', why='7z member outside the unpack folder'))
                return
    before = len(sources)
    expand(out, job, sources, notes)
    for s in sources[before:]:
        s.label = path + '!' + os.path.relpath(s.label, out).replace('\\', '/') if s.label != out else path


# ------------------------------------------------------------------------------------ ROMs, catalog, ledger

class Roms:
    """The stock ROMs the importer may patch against: the ports' ROMs (by SHA-1) and other known dumps."""

    def __init__(self, paths=None, dirs=None):
        self.by_game = {}   # game -> (path, z64 bytes) for port ROMs
        self.others = []    # (game, name, z64 bytes) for other known dumps (e.g. No Mercy 1.0)
        for game, p in (paths or {}).items():
            self.add(p, expect=game)
        for d in dirs or []:
            for dirpath, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(ROM_EXTS):
                        self.add(os.path.join(dirpath, f))

    def add(self, path, expect=None, data=None):
        if data is None:
            if not os.path.isfile(path) or os.path.getsize(path) > 128 * 1024 * 1024:
                return None
            data = read_file(path)
        rom, _ = to_z64(data)
        if rom is None:
            return None
        sha1 = hashlib.sha1(rom).hexdigest().upper()
        for game, g in GAMES.items():
            if g['sha1'] == sha1:
                self.by_game.setdefault(game, (path, rom))
                return game
        crc = '%08X' % (zipcrc(rom))
        md5 = hashlib.md5(rom).hexdigest()
        known = OTHER_DUMPS_CRC32.get(crc) or OTHER_DUMPS_MD5.get(md5)
        if known and not any(o[1] == known[1] for o in self.others):
            self.others.append((known[0], known[1], rom))
            return known[0]
        return None

    def candidates(self):
        """(game, name, rom) for every base to try a patch on: port ROMs first."""
        out = [(g, GAMES[g]['title'], rom) for g, (_, rom) in self.by_game.items()]
        return out + list(self.others)

    def header_names(self):
        return {rom_header(rom)['name'].lower(): g for g, (_, rom) in self.by_game.items()}


def zipcrc(data):
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


def load_catalog(path):
    """Recipes for tested conversions, keyed by input hash: {"entries": [{"id", "name", "game", "tier", "route",
    "inputs": {"sha1": [...]}, ...}]} -> {SHA1: entry}."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as f:
        cat = json.load(f)
    out = {}
    for e in cat.get('entries', []):
        for h in e.get('inputs', {}).get('sha1', []):
            out[h.upper()] = e
    return out


class Ledger:
    """Every input seen, by content hash: re-running an import skips what it already did."""

    def __init__(self, path):
        self.path = path
        self.entries = {}
        if path and os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                self.entries = json.load(f).get('entries', {})

    def ids(self):
        return {e['id'] for e in self.entries.values() if e.get('id')}

    def record(self, key, result):
        self.entries[key] = {k: result.get(k) for k in ('status', 'kind', 'game', 'id', 'name', 'route', 'tier', 'item')}
        self.entries[key].update(engine=ENGINE_VERSION, time=time.strftime('%Y-%m-%dT%H:%M:%S'))

    def save(self):
        if not self.path:
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'format': 'aki-import-ledger', 'version': 1, 'entries': self.entries}, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


# ------------------------------------------------------------------------------------ classification

def folder_key(src):
    h = hashlib.sha1()
    if getattr(src, 'zip_blob', None) is not None:
        h.update(src.zip_blob)
    else:
        for n in src.names:
            h.update(n.encode('utf-8'))
            h.update(str(os.path.getsize(os.path.join(src.path, n))).encode())
    return 'F' + h.hexdigest().upper()


def classify_rom(rom, order, res, roms, catalog):
    """Fill res for a ROM image (z64 bytes)."""
    sha1 = hashlib.sha1(rom).hexdigest().upper()
    hdr = rom_header(rom)
    res.update(kind='rom', order=order, sha1=sha1, header=hdr)
    for game, g in GAMES.items():
        if g['sha1'] == sha1:
            return res.update(status='stock', game=game, why='the stock %s ROM: nothing to convert' % g['title'])
    crc = '%08X' % zipcrc(rom)
    other = OTHER_DUMPS_CRC32.get(crc) or OTHER_DUMPS_MD5.get(hashlib.md5(rom).hexdigest())
    if other:
        return res.update(status='stock', game=other[0], why='%s, a stock ROM (a base for patches)' % other[1])
    entry = catalog.get(sha1)
    if entry:
        return res.update(status='ready', game=entry.get('game'), route=entry.get('route', 'recipe'),
                          tier=entry.get('tier', 'verified'), catalog=entry.get('id'), name=entry.get('name'),
                          id=entry.get('id'))
    game, region = game_for_code(hdr['code'])
    if game and region:
        return res.update(status='unsupported', game=game, why='%s version of %s; the port needs the %s release' % (
            region, GAMES[game]['title'], GAMES[game]['code'][-1] == 'E' and 'USA' or 'Japanese'))
    if not game:
        return res.update(status='unsupported', why='header %r (%s) is not one of the six AKI games' % (
            hdr['code'], hdr['name']))
    # A hack of a port's game. Which converter (make_patch_mod for in-place hacks, derelink for moved code) is
    # decided by `aki-hack survey --layout` in the build step.
    return res.update(status='ready', game=game, route='survey', tier='untested')


def classify_patch(data, fmt, res, roms, catalog, patchfmt):
    sha1 = hashlib.sha1(data).hexdigest().upper()
    res.update(kind='patch', format=fmt, sha1=sha1)
    if fmt in ('ppf', 'rup') and patchfmt is None:
        return res.update(status='unsupported', why='%s patches are not read by the importer yet' % fmt)
    if patchfmt is None:
        return res.update(status='failed', why='the patch engine (romhack/patchfmt.py) is not installed')
    tried, needs = [], None
    for game, name, base in roms.candidates():
        try:
            target, info = patchfmt.apply(data, base)
        except patchfmt.PatchError as e:
            tried.append(name)
            needs = needs or base_named_by(e)
            continue
        out, order = to_z64(target)
        if out is None:
            return res.update(status='failed', why='the %s patch on %s does not give an N64 ROM' % (fmt, name))
        res.update(base=name, patch_info=_jsonable(info))
        sub = {}
        classify_rom(out, 'z64', sub, roms, catalog)
        for k in ('status', 'game', 'route', 'tier', 'catalog', 'why', 'id', 'name'):
            if k in sub and sub[k] is not None:
                res[k] = sub[k]
        res['result_sha1'] = sub.get('sha1')
        if catalog.get(sha1) and res.get('status') == 'ready':
            e = catalog[sha1]
            res.update(catalog=e.get('id'), tier=e.get('tier', 'verified'), id=e.get('id'), name=e.get('name'))
        return res
    if not roms.candidates():
        return res.update(status='needs-rom', needs=needs or 'a stock ROM of the game it was made for',
                          why='no stock ROMs were given (roms / rom_dirs)')
    return res.update(status='needs-rom', needs=needs or 'the ROM it was made for',
                      why='it does not apply to: %s' % ', '.join(tried))


def base_named_by(err):
    """A base ROM's name when a PatchError says which source it wanted (crc32 / md5 / sha1 attributes)."""
    for attr, table in (('source_crc32', OTHER_DUMPS_CRC32), ('source_md5', OTHER_DUMPS_MD5)):
        v = getattr(err, attr, None)
        if v is None:
            continue
        key = ('%08X' % v) if isinstance(v, int) else str(v)
        hit = table.get(key.upper()) or table.get(key.lower())
        if hit:
            return hit[1]
        if attr == 'source_crc32':
            return 'the ROM with CRC32 %s' % key.upper()
    return None


def _jsonable(info):
    if isinstance(info, dict):
        return {str(k): _jsonable(v) for k, v in info.items()}
    if isinstance(info, (list, tuple)):
        return [_jsonable(v) for v in info]
    if isinstance(info, (bytes, bytearray)):
        return info.hex() if len(info) <= 64 else '<%d bytes>' % len(info)
    return info if isinstance(info, (str, int, float, bool)) or info is None else str(info)


def classify_folder(src, res, roms):
    names = src.names
    if is_bundle(names):
        res.update(kind='pj64-bundle', status='ready', route='import-pj64',
                   parts=sorted({p for p in (part_of(n) for n in names) if p}))
        hint = [n for n in names if os.path.splitext(n.lower())[1] in ROM_EXTS + PATCH_EXTS]
        if hint:
            res['contains'] = hint[:8]
        res['tier'] = 'untested'
        return res
    naming, count, rom_name = texture_naming(names)
    res.update(kind='texture-pack', naming=naming, images=count, status='ready',
               route={'rt64': 'install', 'rice': 'wrap-rice', 'jabo': 'wrap-jabo', 'hts': 'hts-export'}[naming])
    if rom_name:
        res['rom_name'] = rom_name
        game = roms.header_names().get(rom_name.lower())
        if game:
            res['game'] = game
    if 'game' not in res:
        res['ask'] = 'which game'
    return res


def part_of(name):
    lower = name.lower()
    ext = os.path.splitext(lower)[1]
    if ext in SAVE_EXTS:
        return 'save'
    if lower.rsplit('/', 1)[-1] in ('project64.cht', 'project64.rdb', 'project64.cfg'):
        return 'codes'
    if ext in IMAGE_EXTS:
        return 'textures'
    if ext in ROM_EXTS:
        return 'rom'
    if ext in PATCH_EXTS:
        return 'patch'
    return None


def classify_file(src, res, roms, catalog, patchfmt):
    data = src.data()
    lower = src.label.lower()
    ext = os.path.splitext(lower)[1]
    rom, order = to_z64(data)
    if rom is not None:
        return classify_rom(rom, order, res, roms, catalog)
    if data.startswith(b'PK\x03\x04'):
        try:
            names = {n.lower().rsplit('/', 1)[-1] for n in zipfile.ZipFile(io.BytesIO(data)).namelist()}
        except zipfile.BadZipFile:
            names = set()
        if 'aki-package.json' in names or 'aki.json' in names:
            return res.update(kind='package', status='ready', route='install', sha1=hashlib.sha1(data).hexdigest().upper())
        if 'mod.json' in names:
            return res.update(kind='nrm', status='ready', route='install', sha1=hashlib.sha1(data).hexdigest().upper())
    fmt = patch_format(data, patchfmt)
    if fmt or ext in PATCH_EXTS:
        if not fmt:
            return res.update(kind='patch', status='failed', why='%s file without a patch header' % ext)
        return classify_patch(data, fmt, res, roms, catalog, patchfmt)
    if ext in SAVE_EXTS:
        return res.update(kind='save', save=SAVE_EXTS[ext], status='unsupported',
                          why='a save on its own: import it with the conversion it belongs to')
    if ext in CODE_EXTS:
        return res.update(kind='codes', status='unsupported',
                          why='a cheat file on its own: import it with the conversion it belongs to')
    return res.update(kind='unknown', status='unsupported', why='not a ROM, patch, mod, bundle or texture pack')


def classify(src, roms, catalog, ledger, patchfmt, taken):
    res = {'item': src.label}
    try:
        if src.kind_hint == 'folder':
            key = folder_key(src)
            classify_folder(src, res, roms)
        else:
            data = src.data()
            rom, _ = to_z64(data)
            key = hashlib.sha1(rom if rom is not None else data).hexdigest().upper()
            classify_file(src, res, roms, catalog, patchfmt)
    except Exception as e:  # one bad item never stops a batch
        return dict(item=src.label, status='failed', why='%s: %s' % (type(e).__name__, e)), None
    res['key'] = key
    if ledger is not None and key in ledger.entries:
        prev = ledger.entries[key]
        return dict(item=src.label, status='have-it', key=key, kind=prev.get('kind'), game=prev.get('game'),
                    id=prev.get('id'), name=prev.get('name'), why='imported before (%s)' % prev.get('time', '?')), key
    if res.get('status') == 'ready' and res.get('kind') in ('rom', 'patch', 'pj64-bundle', 'texture-pack'):
        res.setdefault('name', None)
        if not res['name']:
            res['name'] = display_name(src.label.split('!')[-1])
        if not res.get('id'):
            res['id'] = unique_id(slug(res['name']), taken)
        else:
            taken.add(res['id'])
    return res, key


# ------------------------------------------------------------------------------------ batch

def run_job(job, emit, patchfmt=None):
    """One batch: expand every path, classify every item, emit one result dict per item, then a summary."""
    if patchfmt is None and not job.get('no_patchfmt'):
        patchfmt = load_patchfmt(job.get('patchfmt'))
    roms = Roms(job.get('roms'), job.get('rom_dirs'))
    catalog = load_catalog(job.get('catalog'))
    ledger = Ledger(job.get('ledger')) if job.get('ledger') else None
    taken = ledger.ids() if ledger else set()
    sources, notes = [], []
    for p in job.get('paths', []):
        expand(p, job, sources, notes)
    # Stock ROMs among the inputs are bases for the patches next to them.
    for s in sources:
        if s.kind_hint == 'file' and not s.label.lower().endswith(PATCH_EXTS):
            try:
                roms.add(s.label, data=s.data())
            except OSError:
                pass
    counts, seen = {}, set()
    for n in notes:
        counts[n['status']] = counts.get(n['status'], 0) + 1
        emit(dict(n, event='item'))
    for s in sources:
        res, key = classify(s, roms, catalog, ledger, patchfmt, taken)
        if key and key in seen:
            res = dict(item=s.label, status='have-it', key=key, why='same input as another item in this batch')
        seen.add(key)
        counts[res['status']] = counts.get(res['status'], 0) + 1
        if ledger is not None and key and res['status'] not in ('have-it', 'failed', 'needs-rom'):
            ledger.record(key, res)
        emit(dict(res, event='item'))
    if ledger is not None:
        ledger.save()
    summary = dict(event='done', counts=counts, engine=ENGINE_VERSION, patch_engine=patchfmt is not None)
    emit(summary)
    return summary


def cmd_worker(args):
    job = json.loads(sys.stdin.read() or '{}')

    def emit(obj):
        sys.stdout.write(json.dumps(obj, sort_keys=True) + '\n')
        sys.stdout.flush()
    run_job(job, emit)
    return 0


def cmd_plan(args):
    roms = {}
    for spec in args.rom or []:
        if '=' not in spec:
            raise ImportError_('--rom takes GAME=PATH (games: %s)' % ', '.join(GAMES))
        g, p = spec.split('=', 1)
        if g not in GAMES:
            raise ImportError_('unknown game %r in --rom (games: %s)' % (g, ', '.join(GAMES)))
        roms[g] = p
    job = dict(paths=args.paths, roms=roms, rom_dirs=args.rom_dir or [], ledger=args.ledger, catalog=args.catalog,
               sevenzip=args.sevenzip, work=args.work, patchfmt=args.patchfmt)
    results = []
    run_job(job, results.append)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=1, sort_keys=True)
    for r in results:
        if r['event'] != 'item':
            continue
        what = ' '.join(str(x) for x in (r.get('kind'), r.get('format') or r.get('naming') or '') if x)
        detail = r.get('why') or r.get('route') or ''
        if r.get('needs'):
            detail = 'needs %s' % r['needs']
        print('%-11s %-8s %-18s %-8s %s  [%s]' % (r['status'], r.get('game') or '-', what, r.get('tier') or '',
                                                 detail, r['item']))
    done = results[-1]
    print('-- %s' % ', '.join('%d %s' % (v, k) for k, v in sorted(done['counts'].items())) +
          ('' if done['patch_engine'] else ' (patch engine not installed)'))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan', help='scan and classify, print what would be done')
    p.add_argument('paths', nargs='+')
    p.add_argument('--rom', action='append', metavar='GAME=PATH', help='a port ROM (repeatable)')
    p.add_argument('--rom-dir', action='append', metavar='DIR', help='find stock ROMs in DIR (by SHA-1)')
    p.add_argument('--ledger', help='import ledger JSON (read, and updated)')
    p.add_argument('--catalog', help='recipe catalog JSON')
    p.add_argument('--sevenzip', help='7zr.exe')
    p.add_argument('--work', help='temp folder for unpacked 7z archives')
    p.add_argument('--patchfmt', help='romhack/patchfmt.py to use')
    p.add_argument('--json', help='write the results here')
    p.set_defaults(func=cmd_plan)
    w = sub.add_parser('worker', help='one batch: JSON job on stdin, JSON lines out')
    w.set_defaults(func=cmd_worker)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ImportError_ as e:
        print('aki-import: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
