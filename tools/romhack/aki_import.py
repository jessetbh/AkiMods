"""aki-import: sort a pile of downloads into AKI conversions (aki-roadmap#228, phase 1: the engine without the launcher).

Subcommands (python aki_import.py <cmd> -h):
  plan   PATH...   scan files, folders and archives; say what each item is, which game, and what would be done with
                   it (a table, or --json). Reads only; --ledger marks inputs imported before (have-it).
  build  PATH... --out CONVERTED
                   plan, then build every ready item into a #154 package (aki-package.json) under CONVERTED\\<game>\\
                   <id>-<version>.zip, which the launcher's package installer installs: a conversion (hack ROMs,
                   patches, Project64 bundles) or a texture-pack. Records what it built in --ledger.
  worker           the launcher's entry point: one process per batch (aki-roadmap#111: process starts leak pool on
                   this machine class). Reads one JSON job from stdin, writes one JSON line per item to stdout, then a
                   "done" line. Job: {"paths": [...], "roms": {"<game>": "<port ROM path>"} | "rom_dirs": [...],
                   "ledger": "<path>", "record": false, "catalog": "<path>", "sevenzip": "<7zr.exe>", "work": "<temp folder>",
                   "build": false, "out": "<Converted folder>", "game": "<game for packs that don't say>",
                   "version": "1.0.0", "authors": [...], "repos": {"<game>": "<game repo, developers>"}}.

What an item can be (kind): rom, patch, nrm (a patch mod), package (aki-package.json / aki.json), pj64-bundle (a
Project64 folder with the mod's cheats, save and textures), texture-pack (rt64 / rice / jabo / hts naming), save,
codes, archive (a RAR: extract it first), unknown. Archives (zip read in memory; 7z unpacked once by 7zr) are opened and their contents sorted.

Result status per item:
  ready        identified; `route` says which converter builds it (survey -> make_patch_mod / derelink, import-pj64,
               texture wrap, install). Unknown hacks are `tier: "untested"` (#224, #172).
  converted    (build) built: `package` (and `packages` when a bundle also made a texture package), `converter`,
               `aki_tier` (the catalog's tier, else "untested"), `survey_tier` for hacks
  stock        the unmodified game: nothing to convert
  have-it      the ledger already has this exact input
  needs-rom    a patch whose base ROM isn't among the ROMs given (`needs` names it when the patch says which); in a
               build, also a hack whose port ROM wasn't given (conversions are built against it)
  needs-game   (build) a texture pack or bundle that doesn't say its game: pass "game"
  unsupported  not one of the six AKI games, another region, a format we don't read yet (`why`)
  failed       something broke (`why`)

Routes (build): a hack ROM or applied patch is surveyed (aki_hack.survey_rom against the port's ROM): in-place hacks
(patch-data / patch-code) go to make_patch_mod, moved or reshaped code (rev0-layout / relinked / restructured) to
derelink, and derelink also takes what make_patch_mod refuses; different-base is refused (unless a catalog recipe for that exact ROM names derelink). A Project64 bundle goes
through aki_conv import-pj64 with an automatic id and name. The tools run in this process.

Patch files are applied by patchfmt.py next to this file (aki-roadmap#228: xdelta/VCDIFF, BPS, IPS, UPS, APS;
detect(patch) and apply(patch, source) -> (target, info), raising PatchError). .rup (NINJA2) and PPF are refused by
name here (import-pj64 applies a bundle's .rup). Stdlib only; run with python -I. Writes nothing but --ledger / --json, files under --work
(7z unpacks, build temp files, removed after each item) and, for build, the packages under --out. A hacked ROM is
on disk only while derelink reads it; packages never hold ROM bytes beyond what a patch carries.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
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
HTS_SUFFIX = '_HIRESTEXTURES.hts'
MAX_ITEM = 128 << 20      # the biggest file read whole (N64 ROMs are 64 MB at most)
MAX_RATIO = 100           # a zip member inflating more than this (past 16 MB) is refused as a possible zip bomb
MAX_DEPTH = 8             # folders walked at most this deep

# Fallback magic when romhack/patchfmt.py isn't there: enough to say "a patch" and its format.
PATCH_MAGIC = ((b'BPS1', 'bps'), (b'UPS1', 'ups'), (b'PATCH', 'ips'), (b'APS10', 'aps'), (b'\xd6\xc3\xc4', 'vcdiff'),
               (b'NINJA', 'rup'), (b'PPF', 'ppf'))
PATCH_EXTS = ('.bps', '.ups', '.ips', '.aps', '.xdelta', '.vcdiff', '.delta', '.rup', '.ppf')


class ImportError_(Exception):
    """A problem with the job itself (bad arguments); printed without a traceback."""


# ------------------------------------------------------------------------------------ helpers

def load_patchfmt(path=None):
    """romhack/patchfmt.py next to this file (detect, apply, PatchError), or None when it isn't there."""
    candidates = [path] if path else []
    if os.environ.get('AKI_PATCHFMT'):
        candidates.append(os.environ['AKI_PATCHFMT'])
    candidates.append(os.path.join(TOOLS, 'patchfmt.py'))
    for c in candidates:
        if c and os.path.isfile(c):
            return load_module(os.path.splitext(os.path.basename(c))[0], c)
    return None


def load_module(name, path=None):
    """A Python file as a module; by default the one called name.py next to this file (its folder goes on sys.path,
    since derelink imports mips and bps)."""
    path = path or os.path.join(TOOLS, name + '.py')
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_unpack():
    """Worker Ant's romhack/unpack.py (extract_7z, UnpackError, MAX_TOTAL), or None when it isn't installed."""
    path = os.path.join(TOOLS, 'unpack.py')
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location('aki_unpack', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_z64(data):
    """(big-endian ROM bytes, original order) or (None, None) when it isn't an N64 ROM (or a byte-swapped image's
    length doesn't divide into its swap unit)."""
    head = data[:4]
    if head == b'\x80\x37\x12\x40':
        return data, 'z64'
    if head == b'\x37\x80\x40\x12':
        if len(data) % 2:
            return None, None
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b), 'v64'
    if head == b'\x40\x12\x37\x80':
        if len(data) % 4:
            return None, None
        b = bytearray(len(data))
        for k in range(4):
            b[k::4] = data[3 - k::4]
        return bytes(b), 'n64'
    return None, None


ROM_MAGICS = (b'\x80\x37\x12\x40', b'\x37\x80\x40\x12', b'\x40\x12\x37\x80')


def looks_like_rom(head):
    return head[:4] in ROM_MAGICS


def looks_like_patch(head):
    return any(head.startswith(m) for m, _ in PATCH_MAGIC)


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
        suffix = '-%d' % n
        cid = base[:48 - len(suffix)].rstrip('-.') + suffix   # ids are at most 48: shorten before the suffix
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
        # GLideN64 names its texture storage after the ROM's internal name: "WWF No Mercy_HIRESTEXTURES.hts".
        hint = {b[:-len(HTS_SUFFIX)] for b in base if b.lower().endswith(HTS_SUFFIX.lower()) and len(b) > len(HTS_SUFFIX)}
        return 'hts', sum(b.endswith(('.hts', '.htc')) for b in lower), (hint.pop() if len(hint) == 1 else None)
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
    """One input: a file on disk, a zip member, or a folder (bundle / texture pack, on disk or zipped).
    Bytes are read only when the item needs them and dropped after it's classified (release)."""

    def __init__(self, label, kind_hint, reader=None, names=None, path=None, size=0, head=b''):
        self.label = label          # what the results show: path, or archive!member
        self.kind_hint = kind_hint  # 'file' or 'folder'
        self.reader = reader        # () -> bytes, for files
        self.names = names or []    # relative file names, for folders
        self.path = path            # the folder or file on disk, when there is one
        self.size = size            # bytes, for files
        self.head = head            # the first 64 bytes, for files
        self.zip_path = None        # a zipped folder: the zip on disk, or its bytes (zip_blob)
        self.zip_blob = None
        self._data = None

    def data(self):
        if self._data is None:
            if self.size > MAX_ITEM:
                raise TooBig('%d MB, more than the %d MB limit' % (self.size >> 20, MAX_ITEM >> 20))
            self._data = self.reader()
        return self._data

    def release(self):
        self._data = None


class TooBig(Exception):
    pass


def is_link(path):
    """Symlinks and Windows junctions (reparse points): never followed, so a loop or a link to C:\\ can't be walked."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & 0x400)


def folder_files(root, notes=None, depth=0):
    """Relative file names under root, links skipped, at most MAX_DEPTH folders deep."""
    out = []
    try:
        entries = sorted(os.listdir(root))
    except OSError as e:
        if notes is not None:
            notes.append(dict(item=root, status='failed', why='cannot read the folder: %s' % e.strerror))
        return out
    for e in entries:
        full = os.path.join(root, e)
        if is_link(full):
            continue
        if os.path.isdir(full):
            if depth < MAX_DEPTH:
                out += [e + '/' + n for n in folder_files(full, notes, depth + 1)]
        elif os.path.isfile(full):
            out.append(e)
    return out


def strip_root(names):
    """Names relative to the one top folder they all share (a zip of 'My Mod/...'), else unchanged."""
    tops = {n.split('/', 1)[0] for n in names}
    if len(tops) == 1 and all('/' in n for n in names):
        return [n.split('/', 1)[1] for n in names]
    return names


def _pj64_marker(rel):
    return rel in PJ64_MARKERS or (rel.startswith('config/') and rel[7:] in PJ64_MARKERS)


def bundle_root(names, zipped=False):
    """Where a Project64 bundle's own folder is, relative to the names ('' = at the top; a zip's single top folder
    doesn't count as a level), or None for no bundle. Its markers sit at that folder's top or in its Config/
    (Config/Project64.cfg). Distributions often wrap it once more, beside their read-me files and a launcher exe
    (WWF Legends: Challenge 64, 'Ch64 - Public/Project64/Project64.exe', aki-roadmap#265): exactly one subfolder
    holding the markers counts too: in a zip, or in a folder with files of its own beside it (a distribution's
    read-me and launcher; a plain folder of folders is walked instead, so its bundle keeps its own name). Not deeper, and not two such subfolders: a folder of several bundles isn't one. And only when nothing outside that
    subfolder is something to import on its own (a ROM, patch, archive, mod, image or save): a downloads folder with a
    hack next to a Project64 folder is walked, so the hack stays an item of its own."""
    lower = [n.lower() for n in (strip_root(names) if zipped else names)]
    if any(_pj64_marker(n) for n in lower):
        return ''
    if not zipped and not any('/' not in n for n in lower):
        return None  # a plain folder of folders: expand() walks in and finds the bundle at its own top
    subs = {n.split('/', 1)[0] for n in lower if '/' in n and _pj64_marker(n.split('/', 1)[1])}
    if len(subs) != 1:
        return None
    sub = subs.pop()
    own = ROM_EXTS + PATCH_EXTS + IMAGE_EXTS + tuple(SAVE_EXTS) + ('.zip', '.7z', '.rar', '.nrm', '.rtz', '.hts', '.htc')
    if any(os.path.splitext(n)[1] in own for n in lower if not n.startswith(sub + '/')):
        return None
    # The original spelling of that folder (names are matched lower-case).
    for n in (strip_root(names) if zipped else names):
        if n.split('/', 1)[0].lower() == sub:
            return n.split('/', 1)[0] + '/'
    return None


def is_bundle(names, zipped=False):
    """A Project64 bundle (bundle_root)."""
    return bundle_root(names, zipped) is not None


def save_stem(name):
    """'Save/WWF No Mercy_Cont_1.mpk' -> 'wwf no mercy': Project64 names saves after the ROM's internal name."""
    base = os.path.splitext(name.replace('\\', '/').rsplit('/', 1)[-1])[0].lower()
    return base.split('_cont_')[0]


def save_bundle_rom(names, zipped=False):
    """The ROM name a textures + save mod is for (N64Modderz64's WWF No Mercy MOD: a GLideN64 .hts and a Project64
    Save folder, no Project64.cfg or cheats; aki-roadmap#172), else None. Strict, so a folder of unrelated items isn't
    taken as one: a texture pack that names its ROM, a save named after the same ROM, and nothing else to convert
    (no ROMs, patches, mods or archives; a Project64 savestate .pj.zip is fine)."""
    names = strip_root(names) if zipped else names
    lower = [n.lower() for n in names]
    if any(os.path.splitext(n)[1] in ROM_EXTS + PATCH_EXTS + ('.7z', '.rar', '.nrm') or
           (n.endswith('.zip') and not n.endswith('.pj.zip')) for n in lower):
        return None
    got = texture_naming([n for n in names if os.path.splitext(n.lower())[1] not in SAVE_EXTS])
    if not got or not got[2] or not got[2].strip():
        return None
    rom = got[2].strip().lower()
    if not any(os.path.splitext(n)[1] in SAVE_EXTS and save_stem(n) == rom for n in lower):
        return None
    return got[2].strip()


def is_texture_folder(names):
    """Texture-named images and no ROMs, patches or archives beside them (those make it a folder of items)."""
    if any(os.path.splitext(n.lower())[1] in ROM_EXTS + PATCH_EXTS + ('.zip', '.7z', '.rar', '.nrm') for n in names):
        return False
    return texture_naming(names) is not None


def file_head(path, n=64):
    with open(path, 'rb') as f:
        return f.read(n)


def expand(path, job, sources, notes, depth=0):
    """Turn one picked path into Sources. Bundles and texture packs stay one item; other folders are walked."""
    if is_link(path):
        notes.append(dict(item=path, status='unsupported', why='a link or junction: pick the folder it points to'))
        return
    if os.path.isdir(path):
        names = folder_files(path, notes)
        if is_bundle(names) or save_bundle_rom(names) or is_texture_folder(names):
            sources.append(Source(path, 'folder', names=names, path=path))
            return
        if depth >= MAX_DEPTH:
            return
        try:
            entries = sorted(os.listdir(path))
        except OSError as e:
            notes.append(dict(item=path, status='failed', why='cannot read the folder: %s' % e.strerror))
            return
        for entry in entries:
            try:
                expand(os.path.join(path, entry), job, sources, notes, depth + 1)
            except Exception as e:  # one unreadable entry doesn't hide its siblings
                notes.append(dict(item=os.path.join(path, entry), status='failed', why='%s: %s' % (type(e).__name__, e)))
        return
    if not os.path.isfile(path):
        notes.append(dict(item=path, status='failed', why='not found'))
        return
    head = file_head(path)
    if head.startswith(b'PK\x03\x04') and not path.lower().endswith('.nrm'):
        expand_zip(path, lambda p=path: open(p, 'rb'), path, sources, notes)
    elif head.startswith(b"7z\xbc\xaf\x27\x1c"):
        expand_7z(path, job, sources, notes)
    elif head.startswith(b'Rar!'):
        notes.append(dict(item=path, kind='archive', status='unsupported', why=RAR_WHY))
    else:
        sources.append(Source(path, 'file', reader=lambda p=path: read_file(p), path=path,
                              size=os.path.getsize(path), head=head))


# RAR can't be read here (7zr has no RAR codec): say what to do instead, for a .rar picked or inside a zip
# (aki-roadmap#172; a 7z's members are extracted to a folder and walked, so a RAR in one is a picked file).
RAR_WHY = ("a RAR archive, which the importer can't open yet. Extract it first with 7-Zip (free, from 7-zip.org: "
           "right-click it, 7-Zip, Extract to), then import the folder it made")


def member_problem(m):
    """Why a zip member mustn't be inflated (too big, or a zip-bomb ratio), or None."""
    if m.file_size > MAX_ITEM:
        return '%d MB, more than the %d MB limit' % (m.file_size >> 20, MAX_ITEM >> 20)
    if m.file_size > (16 << 20) and m.file_size > MAX_RATIO * max(m.compress_size, 1):
        return 'compressed %d:1, refused as a possible zip bomb' % (m.file_size // max(m.compress_size, 1))
    return None


def read_member(opener, name):
    with opener() as f, zipfile.ZipFile(f) as z:
        return z.read(name)


def expand_zip(label, opener, origin, sources, notes, depth=0, blob=None):
    """Members are read on demand (nothing is extracted, so no zip-slip), each capped (member_problem). A zip that is
    a bundle, texture pack or mod is one item; otherwise every ROM / patch / mod / zip inside is its own item.
    opener() gives a fresh file object for the zip (the file on disk, or a nested zip's bytes)."""
    fh = opener()
    try:
        expand_zip_members(label, fh, opener, origin, sources, notes, depth, blob)
    finally:
        fh.close()


def expand_zip_members(label, fh, opener, origin, sources, notes, depth, blob):
    try:
        z = zipfile.ZipFile(fh)
        members = [i for i in z.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        notes.append(dict(item=label, status='failed', why='bad zip: %s' % e))
        return
    names = [m.filename for m in members]
    top = {n.lower() for n in strip_root(names)}
    whole = blob if blob is not None else None

    def whole_reader():
        if blob is not None:
            return blob
        return read_file(origin)
    size = len(blob) if blob is not None else os.path.getsize(origin)
    if top & {'mod.json', 'aki-package.json', 'aki.json'}:
        sources.append(Source(label, 'file', reader=whole_reader, path=origin, size=size, head=b'PK\x03\x04'))
        return
    if is_bundle(names, zipped=True) or save_bundle_rom(names, zipped=True) or is_texture_folder(names):
        src = Source(label, 'folder', names=names, path=origin)
        if whole is None:
            src.zip_path = origin
        else:
            src.zip_blob = whole
        sources.append(src)
        return
    for m in members:
        member_label = '%s!%s' % (label, m.filename)
        ext = os.path.splitext(m.filename.lower())[1]
        why = member_problem(m)
        try:
            if ext == '.zip':
                if depth >= 2:
                    notes.append(dict(item=member_label, status='unsupported', why='zip nested too deep'))
                elif why:
                    notes.append(dict(item=member_label, status='unsupported', why=why))
                else:
                    inner = z.read(m)
                    expand_zip(member_label, lambda b=inner: io.BytesIO(b), origin, sources, notes, depth + 1,
                               blob=inner)
                continue
            with z.open(m) as f:
                head = f.read(64)
            if ext == '.rar' or head.startswith(b'Rar!'):
                notes.append(dict(item=member_label, kind='archive', status='unsupported', why=RAR_WHY))
                continue
            if not (ext in ROM_EXTS + PATCH_EXTS + ('.nrm',) or looks_like_rom(head) or looks_like_patch(head)):
                continue
            if why:
                notes.append(dict(item=member_label, status='unsupported', why=why))
                continue
            sources.append(Source(member_label, 'file', reader=lambda o=opener, n=m.filename: read_member(o, n),
                                  path=origin,
                                  size=m.file_size, head=head))
        except Exception as e:  # a corrupt member fails alone
            notes.append(dict(item=member_label, status='failed', why='%s: %s' % (type(e).__name__, e)))
    z.close()


def find_7zr(job):
    for c in (job.get('sevenzip'), os.path.join(TOOLS, 'bin', '7zr.exe'), shutil.which('7zr'), shutil.which('7z')):
        if c and os.path.isfile(c):
            return c
    return None


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def work_dir(job):
    """--work, or one temp folder for the batch that run_job deletes at the end."""
    if job.get('work'):
        os.makedirs(job['work'], exist_ok=True)
        return job['work']
    if not job.get('_tmp'):
        job['_tmp'] = tempfile.mkdtemp(prefix='aki-import-')
    return job['_tmp']


def expand_7z(path, job, sources, notes):
    exe = find_7zr(job)
    if not exe:
        notes.append(dict(item=path, status='unsupported', why='7z archive: 7zr.exe not found (bin\\7zr.exe or --sevenzip)'))
        return
    # Keyed by content, so a changed archive is unpacked again; a .complete marker, so a broken unpack isn't reused.
    out = os.path.join(work_dir(job), '7z-%s' % sha1_file(path)[:16])
    marker = os.path.join(out, '.aki-complete')
    if not os.path.isfile(marker):
        if os.path.isdir(out):
            shutil.rmtree(out, ignore_errors=True)
        unpack = load_unpack()
        if unpack is not None:   # romhack/unpack.py: size cap, path checks, unpacked aside and renamed into out
            try:
                unpack.extract_7z(path, out, max_total=job.get('max_unpack') or unpack.MAX_TOTAL, sevenzip=exe)
            except unpack.UnpackError as e:
                bad = e.kind in ('too-big', 'bad-path', 'need-7zip')
                notes.append(dict(item=path, status='unsupported' if bad else 'failed', why=str(e)))
                return
            r = None
        else:
            os.makedirs(out)
            r = subprocess.run([exe, 'x', '-y', '-bd', '-snl-', '-o' + out, path], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        if r is not None and r.returncode != 0:
            shutil.rmtree(out, ignore_errors=True)
            notes.append(dict(item=path, status='failed', why='7zr exit %d' % r.returncode))
            return
        with open(marker, 'w') as f:
            f.write(path)
    root = os.path.realpath(out)
    for dirpath, _, files in os.walk(out):
        for f in files:
            if not os.path.realpath(os.path.join(dirpath, f)).startswith(root + os.sep):
                notes.append(dict(item=path, status='failed', why='7z member outside the unpack folder'))
                return
    s0, n0 = len(sources), len(notes)
    for entry in sorted(os.listdir(out)):
        if entry != '.aki-complete':
            expand(os.path.join(out, entry), job, sources, notes, 1)

    def relabel(label):
        rel = os.path.relpath(label, out).replace('\\', '/')
        return path if rel == '.' else path + '!' + rel
    for s in sources[s0:]:
        s.label = relabel(s.label) if s.label.startswith(out) else s.label
    for n in notes[n0:]:
        n['item'] = relabel(n['item']) if n['item'].startswith(out) else n['item']


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
            if not os.path.isfile(path) or os.path.getsize(path) > MAX_ITEM:
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


DEFAULT_CATALOG = os.path.join(TOOLS, 'catalog.json')
TIER_LABELS = {'verified': 'Verified', 'experimental': 'Experimental', 'unsupported': 'Unsupported',
               'untested': 'Untested'}


class Catalog(dict):
    """The recipe catalog (catalog.json: the conversions the AKI team tested): {input SHA-1: entry}, plus
    outputs {built ROM SHA-1: entry}, codes {codes.txt SHA-1: entry} and the tier texts."""

    def __init__(self, cat=None):
        super().__init__()
        cat = cat or {}
        self.outputs, self.codes, self.contents = {}, {}, {}
        self.tiers = cat.get('tiers', {})
        for e in cat.get('entries', []):
            for h in e.get('inputs', {}).get('sha1', []):
                self[h.upper()] = e
            for h in e.get('outputs', {}).get('sha1', []):
                self.outputs[h.upper()] = e
            for h in e.get('outputs', {}).get('content_sha1', []):
                self.contents[h.upper()] = e
            for h in e.get('outputs', {}).get('codes_sha1', []):
                # The same codes.txt in several tested packs (No Mercy Plus V3, V3 SC, POE) says nothing alone.
                self.codes[h.upper()] = None if h.upper() in self.codes else e


def load_catalog(path=DEFAULT_CATALOG):
    """Recipes for tested conversions: {"entries": [{"id", "name", "game", "tier", "converter", "known_issues",
    "note", "inputs": {"sha1": [...]}, "outputs": {"sha1": [...], "codes_sha1": [...], "content_sha1": [...]}}]} -> Catalog. The job's
    "catalog" names another file; "" or false turns it off; without the key, catalog.json next to this file."""
    if not path or not os.path.isfile(path):
        return Catalog()
    with open(path, encoding='utf-8') as f:
        return Catalog(json.load(f))


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
        self.entries[key] = {k: result.get(k) for k in ('status', 'kind', 'game', 'id', 'name', 'route', 'tier', 'item',
                                                         'package', 'packages', 'version', 'aki_tier', 'converter')}
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
    """A folder item's content hash: every file's name and bytes (or the zip's bytes)."""
    h = hashlib.sha1()
    if src.zip_blob is not None:
        h.update(src.zip_blob)
    elif src.zip_path is not None:
        return 'F' + sha1_file(src.zip_path)
    else:
        for n in src.names:
            h.update(n.encode('utf-8') + b'\0')
            with open(os.path.join(src.path, n), 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
    return 'F' + h.hexdigest().upper()


def similar_game(rom, roms, threshold=0.3):
    """The port whose ROM shares the most aligned words with this one in the first MB of code (a hack that renamed
    its header game code, e.g. "VPW2 freem"), or None under the threshold (aki_hack's word_similarity)."""
    best = (threshold, None)
    for game, (_, stock) in roms.by_game.items():
        end = min(0x101000, len(rom), len(stock))
        total = len(range(0x1000, end, 64))
        if total:
            same = sum(rom[i:i + 4] == stock[i:i + 4] for i in range(0x1000, end, 64)) / total
            if same >= best[0]:
                best = (same, game)
    return best[1]


def classify_rom(rom, order, res, roms, catalog, base_game=None):
    """Fill res for a ROM image (z64 bytes). base_game: the game of the stock ROM a patch made it from."""
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
        return res.update(status='ready', game=entry.get('game'), route=entry.get('route', 'survey'),
                          tier=entry.get('tier', 'verified'), catalog=entry.get('id'), name=entry.get('name'),
                          id=entry.get('id'))
    game, region = game_for_code(hdr['code'])
    if game and region:
        return res.update(status='unsupported', game=game, why='%s version of %s; the port needs the %s release' % (
            region, GAMES[game]['title'], GAMES[game]['code'][-1] == 'E' and 'USA' or 'Japanese'))
    if not game:
        # A hack may rename its header (Freem: "NVFE"): the base a patch applied to, or the code, says the game.
        game = base_game or similar_game(rom, roms)
        if not game:
            return res.update(status='unsupported', why='header %r (%s) is not one of the six AKI games' % (
                hdr['code'], hdr['name']))
        res['header_note'] = 'header game code %r: a renamed %s hack' % (hdr['code'], GAMES[game]['title'])
    # A hack of a port's game. Which converter (make_patch_mod for in-place hacks, derelink for moved code) is
    # decided by `aki-hack survey --layout` in the build step.
    return res.update(status='ready', game=game, route='survey', tier='untested')


def classify_patch(data, fmt, res, roms, catalog, patchfmt, keep=False):
    sha1 = hashlib.sha1(data).hexdigest().upper()
    res.update(kind='patch', format=fmt, sha1=sha1)
    if fmt in ('ppf', 'rup'):
        return res.update(status='unsupported', why='%s patches are not read by the importer yet%s' % (
            fmt, ' (in a Project64 bundle, import the bundle)' if fmt == 'rup' else ''))
    if patchfmt is None:
        return res.update(status='failed', why='the patch engine (patchfmt.py) is not installed')
    # Every base is tried: formats without a source check (IPS, APS) "apply" to anything, so the result whose header
    # names the base's own game wins, else the first that applied.
    tried, errors, needs, found = [], [], None, None
    for game, name, base in roms.candidates():
        try:
            target, info = patchfmt.apply(data, base)
        except patchfmt.PatchError as e:
            if getattr(e, 'kind', 'wrong-rom') not in ('wrong-rom', 'already-patched'):
                return res.update(status='failed', why=str(e))   # damaged, unsupported, too large: no base helps
            tried.append(name)
            needs = needs or base_named_by(e) or base_named_by_footer(data, fmt)
            continue
        except Exception as e:
            errors.append('%s: %s: %s' % (name, type(e).__name__, e))
            continue
        out, _ = to_z64(bytes(target))
        if out is None:
            errors.append('%s: the result is not an N64 ROM' % name)
            continue
        out_game, _ = game_for_code(rom_header(out)['code'])
        if found is None or (out_game == game and found[3] != found[0]):
            found = (game, name, (out, info), out_game)
        if out_game == game:
            break
    if found is None:
        if errors and not tried:
            return res.update(status='failed', why='; '.join(errors))
        if not roms.candidates():
            return res.update(status='needs-rom', needs=needs or 'a stock ROM of the game it was made for',
                              why='no stock ROMs were given (roms / rom_dirs)')
        return res.update(status='needs-rom', needs=needs or 'the ROM it was made for',
                          why='it does not apply to: %s' % ', '.join(tried + [e.split(':')[0] for e in errors]))
    game, name, (out, info), _ = found
    res.update(base=name, patch_info=_jsonable(info))
    sub = {}
    classify_rom(out, 'z64', sub, roms, catalog, base_game=game)
    for k in ('status', 'game', 'route', 'tier', 'catalog', 'why', 'id', 'name', 'header_note'):
        if k in sub and sub[k] is not None:
            res[k] = sub[k]
    res['result_sha1'] = sub.get('sha1')
    if keep and res.get('status') == 'ready':
        res['_rom'] = out       # for the build step; never emitted
    if catalog.get(sha1) and res.get('status') == 'ready':
        e = catalog[sha1]
        res.update(catalog=e.get('id'), tier=e.get('tier', 'verified'), id=e.get('id'), name=e.get('name'))
    return res


def base_named_by_footer(patch, fmt):
    """BPS and UPS end with the source ROM's CRC32: the known dump it names, or that CRC."""
    if fmt not in ('bps', 'ups') or len(patch) < 12:
        return None
    crc = '%08X' % int.from_bytes(patch[-12:-8], 'little')
    hit = OTHER_DUMPS_CRC32.get(crc)
    return hit[1] if hit else 'the ROM with CRC32 %s' % crc


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
    zipped = src.zip_path is not None or src.zip_blob is not None
    saves_for = None if is_bundle(names, zipped) else save_bundle_rom(names, zipped)
    if saves_for or is_bundle(names, zipped):
        res.update(kind='pj64-bundle', status='ready', route='import-pj64',
                   parts=sorted({p for p in (part_of(n) for n in names) if p}))
        if saves_for:
            # Textures + a save, no cheats (aki-roadmap#172): import-pj64 builds it; the game is the ROM both name.
            res.update(bundle='textures-save', rom_name=saves_for)
            tops = {n.split('/', 1)[0] for n in names}
            if not zipped and len(tops) == 1 and all('/' in n for n in names):
                res['name'] = display_name(tops.pop())   # the extracted archive's own folder, not the one picked
            game = roms.header_names().get(saves_for.lower())
            if game:
                res['game'] = game
            if any(n.lower().endswith(('.hts', '.htc')) for n in names):
                res['parts'] = sorted(set(res['parts']) | {'textures'})
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


def classify_file(src, res, roms, catalog, patchfmt, keep=False):
    """By the first bytes and the extension; the whole file is read only for ROMs, patches and mods."""
    head = src.head
    ext = os.path.splitext(src.label.lower())[1]
    if looks_like_rom(head):
        data = src.data()
        rom, order = to_z64(data)
        if rom is None:
            return res.update(kind='rom', status='failed', why='a byte-swapped ROM whose length does not divide')
        classify_rom(rom, order, res, roms, catalog)
        if keep and res.get('status') == 'ready':
            res['_rom'] = rom   # for the build step; never emitted
        return res
    if head.startswith(b'PK\x03\x04'):
        data = src.data()
        try:
            names = {n.lower() for n in strip_root(zipfile.ZipFile(io.BytesIO(data)).namelist())}
        except zipfile.BadZipFile:
            names = set()
        if 'aki-package.json' in names or 'aki.json' in names:
            return res.update(kind='package', status='ready', route='install', sha1=hashlib.sha1(data).hexdigest().upper())
        if 'mod.json' in names:
            return res.update(kind='nrm', status='ready', route='install', sha1=hashlib.sha1(data).hexdigest().upper())
    if looks_like_patch(head) or ext in PATCH_EXTS:
        data = src.data()
        fmt = patch_format(data, patchfmt)
        if not fmt:
            return res.update(kind='patch', status='failed', why='%s file without a patch header' % ext)
        return classify_patch(data, fmt, res, roms, catalog, patchfmt, keep)
    if ext in SAVE_EXTS:
        return res.update(kind='save', save=SAVE_EXTS[ext], status='unsupported',
                          why='a save on its own: import it with the conversion it belongs to')
    if ext in CODE_EXTS:
        return res.update(kind='codes', status='unsupported',
                          why='a cheat file on its own: import it with the conversion it belongs to')
    return res.update(kind='unknown', status='unsupported', why='not a ROM, patch, mod, bundle or texture pack')


def classify(src, roms, catalog, ledger, patchfmt, taken, keep=False):
    res = {'item': src.label}
    try:
        if src.kind_hint == 'folder':
            key = folder_key(src)
            classify_folder(src, res, roms)
        else:
            classify_file(src, res, roms, catalog, patchfmt, keep)
            key = res.get('sha1')   # ROMs (.z64 order), patches, mods; loose saves and unknown files have none
    except TooBig as e:
        return dict(item=src.label, status='unsupported', why=str(e)), None
    except Exception as e:  # one bad item never stops a batch
        return dict(item=src.label, status='failed', why='%s: %s' % (type(e).__name__, e)), None
    finally:
        src.release()
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


# ------------------------------------------------------------------------------------ build (routes that build)

# The runtime's game ids (mod.json game_id, aki-package.json game_id) and the revision tag each port runs.
GAME_IDS = {'wt': 'wcwnwoworldtour', 'revenge': 'wcwnworevenge', 'wm2k': 'wwfwm2k', 'vpw64': 'vpw64',
            'nomercy': 'wwfnomercy', 'vpw2': 'vpw2'}
PORT_TAGS = {'nomercy': 'nomercy-rev1', 'wt': 'wt-rev0', 'revenge': 'revenge-rev0', 'wm2k': 'wm2k-rev0',
             'vpw64': 'vpw64-rev0', 'vpw2': 'vpw2-rev0'}
# Codes for another revision than the port's are translated through that revision's derelink map: a shipped one
# (maps/<tag>.json next to this file: address ranges only, no ROM bytes), else one made with derelink map from a stock
# dump of that revision (the player's, or one in the bundle; OTHER_DUMPS names it).
REVISION_DUMPS = {'nomercy-rev0': 'WWF No Mercy (USA) 1.0'}
MAPS_DIR = os.path.join(TOOLS, 'maps')
# --map-range overrides per revision, as the hand-made packs were translated (AkiTools README, aki-roadmap#205): No
# Mercy Rev 0 lists' high RAM (their code caves) moves -0x1000; at the map's own shift there Legends crashed. A catalog
# entry's "map_range" (a list, [] for none) wins.
REVISION_MAP_RANGES = {'nomercy-rev0': ['200800,400000,-1000']}
# aki-hack survey tiers -> converter. patch-* run in place (make_patch_mod); moved or reshaped code goes to derelink.
IN_PLACE_TIERS = ('patch-data', 'patch-code')
DERELINK_TIERS = ('rev0-layout', 'relinked', 'restructured')
REFUSED_TIERS = ('different-base',)
UNTESTED = 'untested'      # the fourth tier (#224, #172): built, not tested by us
MAX_EXTRACT = 4 << 30      # a zipped bundle unpacked for import-pj64 may take at most this much disk
TEXTURE_EXTS = ('.png', '.dds')
PACKAGE_ID = re.compile(r'^[a-z0-9_.-]{1,48}$')


def revision_label(tag):
    """'nomercy-rev0' -> 'Rev 0'."""
    return 'Rev %s' % tag.rsplit('-rev', 1)[1] if '-rev' in tag else tag


VERSION_SUFFIX = re.compile(r'[\s._-]+[\(\[]?\s*v(?:er(?:sion)?)?\.?\s*\d+(?:\.\d+)*[a-z]?\s*[\)\]]?\s*$'
                            r'|[\s_-]+[\(\[]?\d+(?:\.\d+)+[a-z]?[\)\]]?\s*$', re.I)


def without_version(name):
    """'Showdown 64 V6.0.4' -> 'Showdown 64', 'AEW 64 (v1.2)' -> 'AEW 64', 'Mod 1.0.5' -> 'Mod'. A bare number
    stays ('TNA No Mercy 2015', 'Showdown 64')."""
    return VERSION_SUFFIX.sub('', name).strip()


class BuildError(Exception):
    """One item couldn't be built (the message says why); the batch goes on."""


DEVICE_NAMES = {'con', 'prn', 'aux', 'nul'} | {'com%d' % i for i in range(10)} | {'lpt%d' % i for i in range(10)}
VERSION = re.compile(r'^\d+\.\d+\.\d+$')


def package_id(cid):
    """An id the launcher's valid_id takes: a-z 0-9 _ . -, starting with a letter or digit, at most 48, no trailing
    '.', not a Windows device name."""
    cid = re.sub(r'[^a-z0-9_.-]+', '-', cid.lower())
    cid = re.sub(r'^[^a-z0-9]+', '', cid)[:48].rstrip('.-_') or 'import'
    if cid.split('.')[0] in DEVICE_NAMES:
        cid = (cid[:44] + '-pkg')
    return cid


def zip_write(path, files):
    """files: {name in the zip: bytes or a path on disk}. Deterministic (sorted, fixed times), written to a temp file
    and moved into place, so a half-written package never sits in Converted.
    files: values may also be (zip, ZipInfo): that member is streamed, so a big pack is never held in memory."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                data = files[name]
                if isinstance(data, (bytes, bytearray)):
                    z.writestr(info, data)
                    continue
                src = data[0].open(data[1]) if isinstance(data, tuple) else open(data, 'rb')
                with src, z.open(info, 'w', force_zip64=True) as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def sha256_path(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def nrm_with_id(nrm, mod_id):
    """The .nrm with mod.json's id set to mod_id (a #154 conversion's patch mod id is the conversion id)."""
    src = zipfile.ZipFile(io.BytesIO(nrm))
    mod = json.loads(src.read('mod.json'))
    if mod.get('id') == mod_id:
        return nrm
    mod['id'] = mod_id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            z.writestr(info.filename, json.dumps(mod, indent=4) if info.filename == 'mod.json' else src.read(info))
    return buf.getvalue()


class Builder:
    """Builds ready items into #154 packages under <out>\\<game>\\ (aki-roadmap#228 phase 1, #154 one package format):
      - a hack ROM (or a patch, applied): aki_hack.survey_rom decides the converter (make_patch_mod for in-place hacks,
        derelink for moved or reshaped code; derelink also when make_patch_mod refuses) -> a conversion package
      - a Project64 bundle: aki_conv import-pj64 with an automatic id and name -> a conversion package (+ a texture
        package when the bundle has textures)
      - a texture pack (rt64 / Rice / Jabo / .hts): -> a texture-pack package
    Everything runs in this process. Hacked ROM bytes go to a temp file only for derelink, deleted right after; no
    package holds ROM bytes beyond what the patch carries."""

    def __init__(self, job, roms, catalog):
        if not job.get('out'):
            raise ImportError_('build needs "out" (the Converted folder)')
        self.out = job['out']
        self.job = job
        self.roms = roms
        self.catalog = catalog
        self.version = job.get('version') or '1.0.0'
        if not VERSION.match(self.version):
            raise ImportError_('version %r is not x.y.z (the launcher refuses other versions)' % self.version)
        self.written = {}                           # (game, id) -> input key, for this batch
        self.authors = job.get('authors') or ['unknown']
        self.repos = job.get('repos') or {}        # game -> a game repo root (developers: its RecompiledFuncs)
        self.game_hint = job.get('game')
        self._mods = {}
        self._tables = {}
        self._maps = {}                             # (game, revision tag) -> revision_map's answer

    def cleanup(self):
        """The batch's build temp folder (a byte-swapped port ROM's .z64 copy among others) goes at the end."""
        shutil.rmtree(os.path.join(work_dir(self.job), 'build'), ignore_errors=True)

    def target(self, game, pid, res):
        """Converted\\<game>\\<id>-<version>.zip for this item: an id another item of this batch wrote, or a package
        on disk built from another input, gets a -2, -3 ... suffix instead of being overwritten."""
        key = res.get('key') or res.get('sha1')
        base, n = pid, 2
        while True:
            path = os.path.join(self.out, game, '%s-%s.zip' % (pid, self.version))
            owner = self.written.get((game, pid))
            if owner is None and os.path.isfile(path):
                owner = package_input(path) or '?'
            if owner is None or owner == key:
                self.written[(game, pid)] = key
                return pid, path
            suffix = '-%d' % n
            pid = base[:48 - len(suffix)].rstrip('-.') + suffix
            n += 1

    def mod(self, name):
        if name not in self._mods:
            self._mods[name] = load_module(name)
        return self._mods[name]

    def tmp(self, name):
        d = os.path.join(work_dir(self.job), 'build')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    # -- what the converters need from the port

    def port(self, game):
        """(path or None, z64 bytes) of the port's ROM, or BuildError."""
        got = self.roms.by_game.get(game)
        if not got:
            raise NeedsRom('the %s ROM the port uses (the conversion is built against it)' % GAMES[game]['title'])
        return got

    def port_file(self, game):
        """A path holding the port's ROM in .z64 order (the given file, or a temp copy for a byte-swapped one)."""
        path, rom = self.port(game)
        if path and os.path.isfile(path) and file_head(path, 4) == ROM_MAGICS[0]:
            return path
        tmp = self.tmp('port-%s.z64' % game)
        if not os.path.isfile(tmp):
            with open(tmp, 'wb') as f:
                f.write(rom)
        return tmp

    def tables(self, game):
        """(sections, repaired names) for make_patch_mod / aki_hack, or (None, set()). From a game repo's
        RecompiledFuncs (job "repos", developers: it also lists the functions the port repaired), else from the
        derelink layout for the port's ROM (layouts/, or job "layouts"); with neither, survey can't tell in-place
        hacks apart and derelink builds them."""
        if game not in self._tables:
            mpm = self.mod('make_patch_mod')
            sections, repaired = None, set()
            repo = self.repos.get(game)
            if repo:
                inl = os.path.join(repo, 'RecompiledFuncs', 'recomp_overlays.inl')
                if os.path.isfile(inl):
                    sections = mpm.load_sections(inl)
                    repaired = mpm.load_repaired(os.path.join(repo, 'RecompiledFuncs', 'repaired_functions.txt')) or set()
            if sections is None and game in self.roms.by_game:
                layout = mpm.find_layout(self.roms.by_game[game][1], self.job.get('layouts'))
                if layout:
                    sections = mpm.layout_sections(layout)
            self._tables[game] = (sections, repaired)
        return self._tables[game]

    # -- one item

    def build(self, src, res):
        """Build one ready item; res gets status converted (package / packages, converter, aki_tier) or failed /
        needs-rom / needs-game with why. Never raises."""
        rom = res.pop('_rom', None)
        self._key = res.get('key') or res.get('sha1')
        try:
            kind = res.get('kind')
            if kind in ('rom', 'patch'):
                self.build_hack(res, rom)
            elif kind == 'pj64-bundle':
                self.build_bundle(src, res)
            elif kind == 'texture-pack':
                self.build_textures(src, res)
            else:
                return res   # .nrm and packages: the launcher installs them as they are
        except NeedsRom as e:
            res.update(status='needs-rom', needs=str(e), why='needs %s' % e)
        except NeedsGame as e:
            res.update(status='needs-game', why=str(e))
        except BuildError as e:
            res.update(status='failed', why=str(e))
        except Exception as e:  # one broken build never stops the batch
            res.update(status='failed', why='%s: %s' % (type(e).__name__, e))
        return res

    def catalog_entry(self, res):
        return self.catalog.get((res.get('sha1') or '').upper()) or self.catalog.get((res.get('result_sha1') or '').upper())

    def judge(self, res, game, files):
        """The tier a built conversion shows (#172, #224): the catalog's, only when what was built is what the AKI
        team tested (its patch makes the same ROM from the port ROM, or, without a patch, the same codes.txt);
        otherwise Untested, with a note when the input is a known hack that built differently.
        Returns (tier, catalog entry or None, note)."""
        outputs = getattr(self.catalog, 'outputs', {})
        codes = getattr(self.catalog, 'codes', {})
        contents = getattr(self.catalog, 'contents', {})
        got = None
        nrm = files.get('conversion/patch.nrm')
        if 'conversion/codes.txt' in files:
            res['codes_sha1'] = hashlib.sha1(files['conversion/codes.txt']).hexdigest().upper()
        content = self.content_sha1(files)
        if content:
            res['content_sha1'] = content
        if nrm is not None:
            # A patch decides; one that can't be checked (no port ROM) leaves the pack Untested.
            if outputs and game in self.roms.by_game:
                bps = zipfile.ZipFile(io.BytesIO(nrm)).read('patch.bps')
                target = self.mod('make_patch_mod').bps_apply(self.roms.by_game[game][1], bps)
                res['output_sha1'] = hashlib.sha1(target).hexdigest().upper()
                del target
                got = outputs.get(res['output_sha1'])
                # Same patch, other codes (a codes translation that changed, #205): not the tested build.
                want = (got or {}).get('outputs', {}).get('codes_sha1')
                if got is not None and want and res.get('codes_sha1') not in [h.upper() for h in want]:
                    got = None
        elif res.get('codes_sha1'):
            got = codes.get(res['codes_sha1'])
            if got is not None and got.get('outputs', {}).get('sha1'):
                got = None      # the tested pack had a patch too: codes alone aren't that build
        elif content:
            # Textures and saves only (aki-roadmap#172): the same saves and texture images as the tested pack.
            got = contents.get(content)
            if got is not None and (got.get('outputs', {}).get('sha1') or got.get('outputs', {}).get('codes_sha1')):
                got = None      # the tested pack had a patch or codes too
        # An entry that names its saves and textures matches only with those, whatever else matched.
        want = (got or {}).get('outputs', {}).get('content_sha1')
        if got is not None and want and content not in [h.upper() for h in want]:
            got = None
        if got is not None and got.get('game') in (None, game):
            return got.get('tier') or UNTESTED, got, None
        known = self.catalog_entry(res)
        if known:
            return UNTESTED, known, ('built differently from the build the AKI team tested as %s (a newer or older '
                                     'converter?)' % TIER_LABELS.get(known.get('tier'), known.get('tier')))
        return UNTESTED, None, None

    def catalog_identity(self, res, game, cid, name, aki, files, entry):
        """(id, name, files) for a build the catalog matched by its saves and textures alone (aki-roadmap#172): the
        entry's id and name instead of the ones taken from the archive's folder ("WWF MOD - Public Master V1.0" ->
        "WWF No Mercy MOD (n64modderz)"). Builds with a patch or codes keep theirs: the catalog named those already
        from the input's hash, and a patch carries its id inside."""
        new_id, new_name = entry.get('id'), entry.get('name')
        if not (new_id or new_name) or not entry.get('outputs', {}).get('content_sha1'):
            return cid, name, files
        if 'conversion/patch.nrm' in files or 'conversion/codes.txt' in files:
            return cid, name, files
        if new_id and package_id(new_id) != cid:
            cid, _ = self.target(game, package_id(new_id), res)
        if new_name and new_name != name:
            files = dict(files)
            readme = files.get('conversion/README.md')
            if isinstance(readme, bytes):
                files['conversion/README.md'] = readme.replace(name.encode('utf-8'), new_name.encode('utf-8'))
            if isinstance(aki.get('description'), str) and not entry.get('description'):
                aki['description'] = aki['description'].replace(name, new_name)
            name = new_name
        if entry.get('description'):
            aki['description'] = entry['description']
        aki.update(id=cid, name=name)
        return cid, name, files

    @classmethod
    def content_sha1(cls, files):
        """A conversion's saves and texture images as one SHA-1 (aki-roadmap#172: a textures + save mod has no patch or
        codes to match by): SHA-1 of the sorted lines "<path> NUL <file SHA-1> LF" for conversion/saves/* and the
        images under conversion/textures/ (not rt64.json, which the importer writes). None when there are neither."""
        lines = []
        for n in sorted(files):
            low = n.lower()
            if not (low.startswith('conversion/saves/') or
                    (low.startswith('conversion/textures/') and low.endswith(TEXTURE_EXTS))):
                continue
            v = files[n]
            h = hashlib.sha1()
            if isinstance(v, str):
                with open(v, 'rb') as f:
                    for chunk in iter(lambda: f.read(1 << 20), b''):
                        h.update(chunk)
            else:
                h.update(cls.member_bytes(v))
            lines.append(n + '\0' + h.hexdigest().upper() + '\n')
        if not lines:
            return None
        return hashlib.sha1(''.join(lines).encode('utf-8')).hexdigest().upper()

    # -- hack ROMs and patches

    def build_hack(self, res, rom):
        if rom is None:
            raise BuildError('the ROM bytes were not kept for the build')
        game = res.get('game')
        if game not in GAMES:
            raise BuildError('no port for this game')
        port_path, port_rom = self.port(game)
        entry = self.catalog_entry(res) or {}
        aki_hack = self.mod('aki_hack')
        stock = aki_hack.Stock(roms={game: port_rom}, tables=self.tables)
        survey = aki_hack.survey_rom(res['item'].split('!')[-1], rom, stock) or {}
        tier = survey.get('tier', 'unknown')
        res.update(survey_tier=tier, survey_reasons=survey.get('reasons', [])[:6])
        if tier == 'stock':
            res.update(status='stock', why='the stock ROM: nothing to convert')
            return
        # A catalog recipe for this exact ROM (inputs.sha1) that names derelink overrides a different-base verdict:
        # the AKI team built and tested it that way (9 Underground: World Tour rev 1 code, de-relinked onto the rev 0
        # port; the survey finds too few port functions and calls it another release).
        refused = tier in REFUSED_TIERS and entry.get('converter') != 'derelink'
        if refused or survey.get('game') not in (None, game):
            raise BuildError('%s: %s' % (tier, '; '.join(survey.get('reasons', [])) or 'another release'))
        converter = entry.get('converter')
        if converter not in ('make_patch_mod', 'derelink'):
            converter = 'make_patch_mod' if tier in IN_PLACE_TIERS else 'derelink'
        if converter == 'make_patch_mod' and self.tables(game)[0] is None:
            converter = 'derelink'      # make_patch_mod needs function tables; derelink has a layout per game
        cid, _ = self.target(game, package_id(res['id']), res)
        name = res.get('name') or cid
        log, codes = [], None
        nrm = None
        if converter == 'make_patch_mod':
            sections, repaired = self.tables(game)
            try:
                nrm, report = self.mod('make_patch_mod').make_mod(
                    port_rom, rom, sections, repaired, GAME_IDS[game], cid, name, self.version, self.authors,
                    log=log.append)
                res['converter_report'] = report
            except self.mod('make_patch_mod').PatchModError as e:
                log.append('make_patch_mod: %s; trying derelink' % e)
                res['fallback'] = 'make_patch_mod refused (%s); built with derelink' % e
                converter = 'derelink'
        if converter == 'derelink':
            nrm, codes = self.derelink(game, rom, cid, name, log)
        res['converter'] = converter
        res['converter_log'] = log[-12:]
        aki = self.conversion_manifest(res, game, cid, name, imported_from=res['kind'])
        files = {'conversion/patch.nrm': nrm_with_id(nrm, cid)}
        aki['components']['patch'] = {'file': 'patch.nrm', 'mod_id': cid}
        if codes:
            files['conversion/codes.txt'] = codes.encode('utf-8')
            aki['components']['codes'] = {'file': 'codes.txt', 'format': 'aki-codes', 'rom': PORT_TAGS[game]}
        aki['survey'].update(tier=tier, converter=converter)
        self.write_conversion(res, game, cid, name, aki, files)

    def derelink(self, game, rom, cid, name, log):
        """(nrm bytes, codes.txt text or None) from derelink build against the port's ROM, in this process."""
        dl = self.mod('derelink')
        port_path = self.port_file(game)
        if dl.find_layout(self.port(game)[1]) is None:
            raise BuildError('derelink has no layout for this %s ROM' % GAMES[game]['title'])
        folder = self.tmp('derelink-%s' % cid)
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        hack_path = os.path.join(folder, 'hack.z64')
        nrm_path = os.path.join(folder, 'patch.nrm')
        try:
            with open(hack_path, 'wb') as f:
                f.write(rom)
            ok, out = run_main(dl, ['build', '--base', port_path, '--hack', hack_path, '--id', cid, '--name', name,
                                    '--version', self.version, '--out', nrm_path])
            log += [l for l in out.splitlines() if l.strip()][-20:]
            if not ok or not os.path.isfile(nrm_path):
                raise BuildError('derelink: %s' % (out.strip().splitlines() or ['failed'])[-1])
            with open(nrm_path, 'rb') as f:
                nrm = f.read()
            watch = os.path.join(folder, dl.CODE_WATCH_FILE)
            codes = None
            if os.path.isfile(watch):
                with open(watch, encoding='utf-8') as f:
                    codes = f.read()
            return nrm, codes
        finally:
            shutil.rmtree(folder, ignore_errors=True)   # the hacked ROM never stays on disk

    # -- packages

    def conversion_manifest(self, res, game, cid, name, imported_from):
        entry = self.catalog_entry(res) or {}
        return {'format': 'aki-conversion', 'format_version': 1, 'id': cid, 'name': name, 'version': self.version,
                'authors': list(self.authors), 'game': game,
                'base_rom': {'sha1': GAMES[game]['sha1'].upper(), 'revision': PORT_TAGS[game]},
                'description': entry.get('description') or '%s, converted by aki-import from your own files.' % name,
                'components': {}, 'source': {'mode': 'byo', 'imported_from': imported_from},
                'survey': {'aki_tier': UNTESTED, 'catalog': entry.get('id'), 'engine': ENGINE_VERSION,
                           'input_sha1': res.get('sha1') or res.get('key'), 'result_sha1': res.get('result_sha1')}}

    def package_manifest(self, kind, game, pid, name, payload, base_sha1=None, requires=None):
        m = {'format': 1, 'kind': kind, 'id': pid, 'version': self.version, 'game_id': GAME_IDS[game],
             'display_name': name, 'authors': list(self.authors), 'payload': payload,
             'source': 'aki-import %s' % ENGINE_VERSION, 'source_key': self._key}
        if base_sha1:
            m['base_rom'] = {'sha1': [base_sha1.lower()]}
        if requires:
            m['requires'] = {'packages': requires}
        return m

    def write_conversion(self, res, game, cid, name, aki, files, requires=None):
        tier, entry, note = self.judge(res, game, files)
        if entry and tier != UNTESTED:
            cid, name, files = self.catalog_identity(res, game, cid, name, aki, files, entry)
        survey = aki.setdefault('survey', {})
        survey.update(aki_tier=tier, aki_tier_label=TIER_LABELS.get(tier, tier), catalog=(entry or {}).get('id'))
        for k in ('output_sha1', 'codes_sha1', 'content_sha1'):
            if res.get(k):
                survey[k] = res[k]
        if entry and tier != UNTESTED:
            for k in ('known_issues', 'note'):
                if entry.get(k):
                    survey[k] = entry[k]
            res.update(known_issues=entry.get('known_issues', []), note=entry.get('note'))
        if note:
            survey['tier_note'] = note
            res['tier_note'] = note
        man = self.package_manifest('conversion', game, cid, name, {'folder': 'conversion/'}, GAMES[game]['sha1'],
                                    requires)
        if tier == UNTESTED:
            man['short_description'] = 'Untested: built by aki-import, not tested by the AKI team; it may crash.'
        else:
            text = getattr(self.catalog, 'tiers', {}).get(tier) or TIER_LABELS.get(tier, tier)
            man['short_description'] = ('%s %s' % (text, (entry or {}).get('note') or '')).strip()
        files = dict(files)
        files['aki-package.json'] = json.dumps(man, indent=2).encode('utf-8')
        files['conversion/aki.json'] = json.dumps(aki, indent=2).encode('utf-8')
        cid2, path = self.target(game, cid, res)
        assert cid2 == cid, 'the id was chosen before the package was made'
        path = zip_write(path, files)
        res.setdefault('packages', []).append(path)
        res.update(status='converted', package=path, game=game, id=cid, name=name, version=self.version,
                   aki_tier=tier, aki_tier_label=TIER_LABELS.get(tier, tier))

    # -- texture packs

    def texture_game(self, res):
        game = res.get('game') or self.game_hint
        if game not in GAMES:
            raise NeedsGame('which game this texture pack is for (pass "game")')
        return game

    def texture_files(self, src):
        """{relative name: path or bytes reader} of the pack's images and rt64.json, and a cleanup list."""
        if src.zip_path is None and src.zip_blob is None:
            return {n: os.path.join(src.path, n) for n in src.names}
        z = zipfile.ZipFile(src.zip_path if src.zip_path is not None else io.BytesIO(src.zip_blob))
        out = {}
        members = [i for i in z.infolist() if not i.is_dir()]
        names = strip_root([i.filename.replace('\\', '/') for i in members])
        for info, rel in zip(members, names):
            if '..' in rel.split('/') or rel.startswith('/') or ':' in rel:
                continue
            if os.path.splitext(rel.lower())[1] in TEXTURE_EXTS + ('.json', '.hts', '.htc'):
                if member_problem(info):
                    continue
                out[rel] = (z, info)
        return out

    def build_textures(self, src, res, name=None, pid=None):
        game = self.texture_game(res)
        files = self.texture_files(src)
        naming = res.get('naming')
        payload = {}
        hts = [n for n in files if n.lower().endswith('.hts')]
        export = None
        if naming == 'hts' or (hts and not any(n.lower().endswith(TEXTURE_EXTS) for n in files)):
            export = self.tmp('hts-%s' % package_id(res['id']))
            shutil.rmtree(export, ignore_errors=True)
            conv = self.mod('aki_conv')
            rom_name = rom_header(self.roms.by_game[game][1])['name'] if game in self.roms.by_game else 'GAME'
            count = 0
            for n in hts:
                path = self.materialise(files[n], os.path.join(export, '_in', os.path.basename(n)))
                got, _ = conv.hts_to_rice_pngs(path, os.path.join(export, os.path.splitext(os.path.basename(n))[0]),
                                               rom_name)
                for p in got:
                    payload[os.path.relpath(p, export).replace(os.sep, '/')] = p
                    count += 1
            if not count:
                raise BuildError('the .hts cache has no RGBA8 textures to export')
            naming = 'rice'
        else:
            for n, v in files.items():
                n = n.replace('\\', '/')
                low = n.lower()
                if low == 'rt64.json':
                    payload['rt64.json'] = v      # the launcher looks for exactly textures/rt64.json
                elif low.endswith(TEXTURE_EXTS):
                    payload[n] = v
        try:
            if 'rt64.json' not in payload:
                payload['rt64.json'] = json.dumps({'configuration': {'autoPath': naming, 'configurationVersion': 3,
                                                                     'hashVersion': 5}, 'textures': []},
                                                  indent=2).encode('utf-8')
            images = sum(n.lower().endswith(TEXTURE_EXTS) for n in payload)
            name = name or res.get('name') or 'Textures'
            pid, path = self.target(game, package_id(pid or res['id']), res)
            man = self.package_manifest('texture-pack', game, pid, name, {'folder': 'textures/'})
            out = {'aki-package.json': json.dumps(man, indent=2).encode('utf-8')}
            for n, v in payload.items():
                out['textures/' + n] = v      # paths and zip members are streamed by zip_write
            path = zip_write(path, out)
        finally:
            if export:
                shutil.rmtree(export, ignore_errors=True)
        res.setdefault('packages', []).append(path)
        res.update(status='converted', package=path, game=game, id=pid, version=self.version, images=images,
                   converter='texture-pack', naming=naming)
        return path

    @staticmethod
    def member_bytes(v):
        if isinstance(v, tuple):      # (zip, info): read now, one member at a time
            z, info = v
            return z.read(info)
        return v                      # a path (streamed by zip_write) or bytes

    def materialise(self, v, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(v, str):
            return v
        with open(path, 'wb') as f:
            f.write(self.member_bytes(v))
        return path

    # -- Project64 bundles

    def bundle_folder(self, src):
        """The bundle on disk: its folder, or a zipped bundle unpacked (safe names only, MAX_EXTRACT in all). For a
        bundle wrapped in a distribution folder (bundle_root, aki-roadmap#265), the bundle's own folder inside it."""
        if src.zip_path is None and src.zip_blob is None:
            sub = bundle_root(src.names) if src.names else ''
            return (os.path.join(src.path, *sub.strip('/').split('/')) if sub else src.path), None
        dest = self.tmp('bundle-%s' % hashlib.sha1(src.label.encode('utf-8')).hexdigest()[:12])
        shutil.rmtree(dest, ignore_errors=True)
        z = zipfile.ZipFile(src.zip_path if src.zip_path is not None else io.BytesIO(src.zip_blob))
        members = [i for i in z.infolist() if not i.is_dir()]
        names = strip_root([i.filename for i in members])
        # Only the bundle's own folder (aki-roadmap#265): a distribution's read-me tree beside it can hold paths over
        # Windows' 260 characters once under the work folder (WWF Legends: "Help Files & Setup/Troubleshooting/...").
        sub = bundle_root([i.filename for i in members], zipped=True) or ''
        total = 0
        for info, rel in zip(members, names):
            rel = rel.replace('\\', '/')
            if sub and not rel.lower().startswith(sub.lower()):
                continue
            parts = [p for p in rel.split('/') if p not in ('', '.')]
            if not parts or '..' in parts or ':' in rel or rel.startswith('/'):
                continue
            why = member_problem(info)
            if why:
                continue
            total += info.file_size
            if total > MAX_EXTRACT:
                shutil.rmtree(dest, ignore_errors=True)
                raise BuildError('the bundle unpacks to more than %d GB' % (MAX_EXTRACT >> 30))
            path = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with z.open(info) as fin, open(path, 'wb') as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
        return (os.path.join(dest, *sub.strip('/').split('/')) if sub else dest), dest

    def bundle_game(self, folder, conv, named=None):
        """The game a bundle's cheats are for (their Project64 section), else a ROM header in it, else the game its
        textures and save name (named, aki-roadmap#172), else "game"."""
        keys = {k: g for k, g in ((k, conv.GAME_OF_TAG.get(t)) for k, t in conv.CHT_SECTIONS.items()) if g}
        for g, (_, rom) in self.roms.by_game.items():
            keys[conv.cht_key(rom)] = g
        found = set()
        for d, _, fs in os.walk(folder):
            for f in fs:
                p = os.path.join(d, f)
                low = f.lower()
                if low.endswith(('.cht', '.ini')) and os.path.getsize(p) < (8 << 20):
                    with open(p, encoding='latin-1') as fh:
                        for key in conv.parse_cht(fh.read()):
                            if key.upper() in keys:
                                found.add(keys[key.upper()])
                elif low.endswith(ROM_EXTS) and os.path.getsize(p) >= 0x1000:
                    rom, _ = to_z64(file_head(p, 0x40))
                    if rom is not None:
                        g, region = game_for_code(rom_header(rom)['code'])
                        if g and not region:
                            found.add(g)
        if len(found) == 1:
            return found.pop()
        if not found and named in GAMES:
            return named
        if self.game_hint in GAMES:
            return self.game_hint
        raise NeedsGame('which game this Project64 bundle is for (%s; pass "game")' % (
            'its cheats name ' + ', '.join(sorted(found)) if found else 'no cheats or ROM in it name a game'))

    def game_cheat_sections(self, folder, conv, game):
        """[(revision tag, section)]: the bundle's cheat sections for `game`, in import-pj64's order."""
        out = []
        for d, _, fs in os.walk(folder):
            for f in fs:
                p = os.path.join(d, f)
                if f.lower().endswith(('.cht', '.ini')) and 0 < os.path.getsize(p) < (8 << 20):
                    with open(p, encoding='latin-1') as fh:
                        for key, sec in conv.parse_cht(fh.read()).items():
                            tag = conv.CHT_SECTIONS.get(key)
                            if tag is None and key.upper() == conv.stock_cht_key(game):
                                tag = PORT_TAGS[game]
                            if tag is None or conv.GAME_OF_TAG.get(tag, tag.split('-rev')[0]) != game:
                                continue
                            out.append((tag, sec))
        return out

    def bundle_code_tag(self, folder, conv, game, name, include):
        """The revision tag of the cheat section import-pj64 will take (the first, in its order, with the mod's
        cheats: e.g. 'nomercy-rev0'), or None. A bundle's other sections (a full Project64.cht) don't count."""
        for tag, sec in self.game_cheat_sections(folder, conv, game):
            if conv.mod_cheats(sec, name, include):
                return tag
        return None

    def bundle_cheats(self, folder, conv, game, name, include):
        """(cheat folder name, revision tag, None) for the mod's own cheats, or (name, None, why none). The cheats sit
        in a top-level folder named like the mod; a bundle folder often carries a version the cheat folder doesn't
        ("Showdown 64 V6.0.4" -> "Showdown 64\\..."), so the name without it is tried next. With none of those,
        a game's only cheat folder is the mod's."""
        tries = [name]
        bare = without_version(name)
        if include is None and bare and bare != name:
            tries.append(bare)
        for n in tries:
            tag = self.bundle_code_tag(folder, conv, game, n, include)
            if tag:
                return n, tag, None
        tops = sorted({c['name'].split(conv.BS)[0] for _, sec in self.game_cheat_sections(folder, conv, game)
                       for c in sec['cheats']})
        if not tops:
            return name, None, None
        # One cheat folder for the game, under another name than the mod's (WWF Legends: Challenge 64's bundle is
        # 'Ch64 - Public', its cheats 'WWF Legends\\...', aki-roadmap#265): those are the mod's cheats.
        foldered = any(conv.BS in c['name'] for _, sec in self.game_cheat_sections(folder, conv, game)
                       for c in sec['cheats'])
        if include is None and len(tops) == 1 and foldered:
            tag = self.bundle_code_tag(folder, conv, game, tops[0], include)
            if tag:
                return tops[0], tag, None
        shown = ', '.join(tops[:6]) + (', ...' if len(tops) > 6 else '')
        return name, None, ('no cheat folder is named like the mod (%s); its folders: %s' %
                            (' or '.join(repr(n) for n in tries) if include is None else 'include %r' % include, shown))

    def bundle_dump(self, folder, game, dump):
        """A stock dump named `dump` inside the bundle (Showdown 64 ships its unpatched cartridge), .z64 bytes, or None."""
        for d, _, fs in os.walk(folder):
            for f in sorted(fs):
                p = os.path.join(d, f)
                if not f.lower().endswith(ROM_EXTS) or not 0x1000 <= os.path.getsize(p) <= MAX_ITEM:
                    continue
                if to_z64(file_head(p, 0x40))[0] is None:
                    continue
                rom, _ = to_z64(read_file(p))
                if rom is None:
                    continue
                known = OTHER_DUMPS_CRC32.get('%08X' % zipcrc(rom)) or OTHER_DUMPS_MD5.get(hashlib.md5(rom).hexdigest())
                if known == (game, dump):
                    return rom
        return None

    def revision_map(self, game, tag, folder, port_path):
        """(map path, where it came from, None) for translating `tag` codes to the port's revision, or
        (None, None, why not). A map made here is kept for the batch (it is address ranges only)."""
        key = (game, tag)
        if key in self._maps:
            return self._maps[key]
        if not port_path:
            return None, None, 'needs the %s ROM the port uses' % GAMES[game]['title']
        shipped = os.path.join(MAPS_DIR, tag + '.json')
        if os.path.isfile(shipped):
            self._maps[key] = (shipped, 'maps/%s.json' % tag, None)
            return self._maps[key]
        dump = REVISION_DUMPS.get(tag)
        if dump is None:
            return None, None, 'no revision map for %s' % tag
        rom = next((r for g, n, r in self.roms.others if g == game and n == dump), None)
        where = 'your %s ROM' % dump
        if rom is None:
            rom = self.bundle_dump(folder, game, dump)
            where = "the bundle's %s ROM" % dump
        if rom is None:
            return None, None, ('needs a stock %s ROM to make the %s -> %s map (put it with your ROMs and import again)'
                                % (dump, revision_label(tag), revision_label(PORT_TAGS[game])))
        dl = self.mod('derelink')
        hack_path = self.tmp('revision-%s.z64' % tag)
        map_path = self.tmp('map-%s.json' % tag)
        try:
            with open(hack_path, 'wb') as f:
                f.write(rom)
            ok, log = run_main(dl, ['map', '--base', port_path, '--hack', hack_path, '--out', map_path])
        except Exception as e:      # a map that can't be made leaves the codes off, never fails the bundle
            ok, log = False, '%s: %s' % (type(e).__name__, e)
        finally:
            if os.path.exists(hack_path):
                os.remove(hack_path)    # the stock ROM never stays on disk
        if not ok or not os.path.isfile(map_path):
            got = (None, None, 'derelink map: %s' % ((log.strip().splitlines() or ['failed'])[-1]))
        else:
            got = (map_path, 'derelink map from %s' % where, None)
        self._maps[key] = got
        return got

    def codes_outcome(self, res, game, parts, map_from, map_why):
        """What happened to the bundle's codes, for the results JSON: res['codes'] (one line) and
        res['codes_translation'] (the numbers) when they were for another revision; the watch codes' count or note."""
        codes = parts.get('codes')
        port = PORT_TAGS[game]
        if codes:
            tr = parts.get('codes_translated')
            if tr:
                values = tr.get('values') or {}
                line = 'codes translated from %s (%d of %d lines; %d dropped)' % (
                    revision_label(tr['from']), tr['codes'], tr['lines'], tr['dropped_lines'])
                if 'off' in values:
                    line += '; values inside the code they write not translated (%s)' % values['off']
                else:
                    line += '; %d values inside the code they write translated' % values.get('values_changed', 0)
                    left = len(values.get('unmapped', [])) + len(values.get('lui_conflicts', [])) + \
                        len(values.get('stepped_bases', []))
                    if left:
                        line += ', %d left as they were' % left
                res['codes'] = line
                res['codes_translation'] = {
                    'from': tr['from'], 'to': port, 'translated': True, 'map': map_from,
                    'map_ranges': tr.get('map_ranges', []), 'lines': tr['lines'], 'translated_lines': tr['codes'],
                    'dropped_lines': tr['dropped_lines'], 'units_split_by_overlay': tr.get('units_split_by_overlay', 0),
                    'overlay_guards': tr.get('overlay_guards'),
                    'values': ({'off': values['off']} if 'off' in values else
                               {k: (len(values[k]) if isinstance(values.get(k), list) else values.get(k, 0))
                                for k in ('values_changed', 'unmapped', 'lui_conflicts', 'stepped_bases')})}
            elif codes.get('rom') != port:
                why = map_why or 'no revision map'
                res['codes'] = 'codes for %s not translated: %s; they stay off' % (revision_label(codes['rom']), why)
                res['codes_translation'] = {'from': codes['rom'], 'to': port, 'translated': False, 'why': why}
            else:
                res['codes'] = "codes for the port's revision (%d lines)" % codes.get('lines', 0)
        patch = parts.get('patch') or {}
        if 'watch_codes' in patch:
            res['watch_codes'] = patch['watch_codes']
        if patch.get('watch_codes_note'):
            res['watch_codes_note'] = patch['watch_codes_note']

    def build_bundle(self, src, res):
        conv = self.mod('aki_conv')
        folder, unpacked = self.bundle_folder(src)
        out = None
        try:
            game = self.bundle_game(folder, conv, res.get('game'))
            entry = self.catalog_entry(res) or {}
            cid, _ = self.target(game, package_id(res['id']), res)
            name = res.get('name') or cid
            port_path = self.port_file(game) if game in self.roms.by_game else None
            conv.PORT_CHT_KEYS[game] = conv.cht_key(self.roms.by_game[game][1]) if port_path else ''
            out = self.tmp('pj64-%s' % cid)
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out)
            # Codes for another revision than the port's (No Mercy Rev 0) are translated through that revision's map.
            cheat_name, tag, cheats_why = self.bundle_cheats(folder, conv, game, entry.get('cheat_name') or name,
                                                             entry.get('include'))
            map_path = map_from = map_why = None
            map_range = []
            if tag and tag != PORT_TAGS[game]:
                map_range = entry.get('map_range', REVISION_MAP_RANGES.get(tag, []))
                try:
                    if not isinstance(map_range, list) or not all(isinstance(r, str) for r in map_range):
                        raise ValueError('not a list of "LO,HI,DELTA" strings')
                    conv.parse_map_ranges(map_range)
                except ValueError as e:
                    map_why = "the catalog's map_range %r: %s" % (map_range, e)
                else:
                    map_path, map_from, map_why = self.revision_map(game, tag, folder, port_path)
            a = argparse.Namespace(
                folder=folder, game=game, id=cid, name=cheat_name, team='aki-import',
                version=self.version, author=list(self.authors), description=entry.get('description', ''),
                credits=entry.get('credits', ''), permission=entry.get('permission', ''), url=entry.get('url', ''),
                include=entry.get('include'), all_cheats=bool(entry.get('all_cheats')), map=map_path,
                map_range=list(map_range) if map_path else [], textures=None,
                base_rom=None, port_rom=port_path, derelink=conv.DERELINK, bake_exclude=list(entry.get('bake_exclude', [])),
                out=out, patch_mod_id=cid)
            ok, log = run_main(types.SimpleNamespace(main=lambda argv: conv.cmd_import_pj64(a)), [])
            if not ok:
                last = (log.strip().splitlines() or ['failed'])[-1]
                if 'pass --port-rom' in last:
                    raise NeedsRom('the %s ROM the port uses (the bundle has a ROM or patch to build)' %
                                   GAMES[game]['title'])
                raise BuildError('import-pj64: %s%s' % (last, ' (%s)' % cheats_why if cheats_why else ''))
            at = log.rfind('\n{\n')
            report = json.loads(log[at + 1:] if at >= 0 else log[log.index('{'):]) if '{' in log else {}
            res.update(game=game, converter='import-pj64', parts=sorted(report.get('parts', {})))
            self.codes_outcome(res, game, report.get('parts', {}), map_from, map_why)
            if cheats_why and 'codes' not in res:
                res['codes'] = 'no codes taken: %s' % cheats_why
            self.repack_pj64(res, game, cid, name, out, report)
        finally:
            if unpacked:
                shutil.rmtree(unpacked, ignore_errors=True)
            if out:     # by the id it was made under: a catalog match can rename the conversion (catalog_identity)
                shutil.rmtree(out, ignore_errors=True)

    def repack_pj64(self, res, game, cid, name, out, report):
        """import-pj64's Thunderstore-shaped zips -> one #154 conversion package (aki.json + its files under
        conversion/). The bundle's textures go inside it, at conversion/textures/ (components.textures.dir): the game
        reads a conversion's textures from its own folder or conversions\\_packages\\, while a separate texture-pack
        package installs to mods\\<id>.rtz, where the conversion never finds it (aki-roadmap#228)."""
        conv_zip = report.get('package')
        if not conv_zip or not os.path.isfile(conv_zip):
            raise BuildError('import-pj64 wrote no package')
        tex_zips = [os.path.join(out, f) for f in os.listdir(out) if f.endswith('.zip') and
                    os.path.join(out, f) != conv_zip]
        with zipfile.ZipFile(conv_zip) as z:
            aki = json.loads(z.read('aki.json'))
            files = {}
            for info in z.infolist():
                if info.is_dir() or info.filename in ('aki.json', 'manifest.json'):
                    continue
                files['conversion/' + info.filename] = z.read(info)
        patch = aki['components'].get('patch')
        if patch and 'conversion/' + patch.get('file', '') in files:
            files['conversion/' + patch['file']] = nrm_with_id(files['conversion/' + patch['file']], cid)
            patch['mod_id'] = cid
        aki.update(id=cid, name=name, version=self.version)
        aki.setdefault('survey', {}).update(self.conversion_manifest(res, game, cid, name, 'project64-bundle')['survey'])
        aki['survey']['converter'] = 'import-pj64'
        tex = aki['components'].get('textures')
        if not (tex and tex_zips):
            aki['components'].pop('textures', None)
            self.write_conversion(res, game, cid, name, aki, files)
            return
        with zipfile.ZipFile(tex_zips[0]) as tz:      # open while the conversion is written: its members stream
            ta = json.loads(tz.read('aki.json'))
            folder = ta.get('folder', '').rstrip('/') + '/'
            for info in tz.infolist():
                if not info.is_dir() and info.filename.startswith(folder):
                    files['conversion/textures/' + info.filename[len(folder):]] = (tz, info)
            # import-pj64 wrote the pack's rt64.json (autoPath for its naming); the game writes one if it's missing.
            aki['components']['textures'] = {'dir': 'textures', 'naming': tex.get('naming'),
                                             'optional': bool(tex.get('optional'))}
            self.write_conversion(res, game, cid, name, aki, files)


def package_input(path):
    """The input key a package in Converted was built from (its conversion's survey.input_sha1), or None."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if 'conversion/aki.json' in names:
                return json.loads(z.read('conversion/aki.json')).get('survey', {}).get('input_sha1')
            return json.loads(z.read('aki-package.json')).get('source_key')
    except Exception:
        return None


class NeedsRom(Exception):
    pass


class NeedsGame(Exception):
    pass


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


# ------------------------------------------------------------------------------------ batch

def run_job(job, emit, patchfmt=None):
    """One batch: expand every path, classify every item, emit one result dict per item, then a summary. The ledger
    is read for have-it; it is written only with job["record"] (the build step), and only for items made ready."""
    if patchfmt is None and not job.get('no_patchfmt'):
        patchfmt = load_patchfmt(job.get('patchfmt'))
    roms = Roms(job.get('roms'), job.get('rom_dirs'))
    catalog = load_catalog(job.get('catalog', DEFAULT_CATALOG))
    builder = Builder(job, roms, catalog) if job.get('build') else None
    ledger = Ledger(job.get('ledger')) if job.get('ledger') else None
    taken = ledger.ids() if ledger else set()
    sources, notes = [], []
    try:
        for p in job.get('paths', []):
            try:
                expand(p, job, sources, notes)
            except Exception as e:
                notes.append(dict(item=p, status='failed', why='%s: %s' % (type(e).__name__, e)))
        # Stock ROMs among the inputs are bases for the patches next to them (only those bytes are kept).
        for s in sources:
            if s.kind_hint == 'file' and looks_like_rom(s.head) and s.size <= MAX_ITEM:
                try:
                    roms.add(s.label, data=s.data())
                except Exception:
                    pass
                finally:
                    s.release()
        counts, seen = {}, set()
        for n in notes:
            counts[n['status']] = counts.get(n['status'], 0) + 1
            emit(dict(n, event='item'))
        for s in sources:
            res, key = classify(s, roms, catalog, ledger, patchfmt, taken, keep=builder is not None)
            if key and key in seen:
                res = dict(item=s.label, status='have-it', key=key, why='same input as another item in this batch')
            if key:
                seen.add(key)
            if builder is not None and res.get('status') == 'ready':
                builder.build(s, res)
            res = {k: v for k, v in res.items() if not k.startswith('_')}
            counts[res['status']] = counts.get(res['status'], 0) + 1
            record = job.get('record') or builder is not None
            if ledger is not None and record and key and res['status'] in ('ready', 'converted'):
                if builder is None or res['status'] == 'converted':
                    ledger.record(key, res)
            emit(dict(res, event='item'))
        if ledger is not None and (job.get('record') or builder is not None):
            ledger.save()
    finally:
        if builder is not None:
            builder.cleanup()
        if job.get('_tmp'):
            shutil.rmtree(job['_tmp'], ignore_errors=True)
    summary = dict(event='done', counts=counts, engine=ENGINE_VERSION, patch_engine=patchfmt is not None,
                   patch_engine_name=getattr(patchfmt, '__name__', None))
    emit(summary)
    return summary


def cmd_worker(args):
    job = json.loads(sys.stdin.read() or '{}')

    def emit(obj):
        sys.stdout.write(json.dumps(obj, sort_keys=True) + '\n')
        sys.stdout.flush()
    run_job(job, emit)
    return 0


def game_paths(specs, flag):
    out = {}
    for spec in specs or []:
        if '=' not in spec:
            raise ImportError_('%s takes GAME=PATH (games: %s)' % (flag, ', '.join(GAMES)))
        g, p = spec.split('=', 1)
        if g not in GAMES:
            raise ImportError_('unknown game %r in %s (games: %s)' % (g, flag, ', '.join(GAMES)))
        out[g] = p
    return out


def cmd_plan(args):
    job = dict(paths=args.paths, roms=game_paths(args.rom, '--rom'), rom_dirs=args.rom_dir or [], ledger=args.ledger,
               catalog=DEFAULT_CATALOG if args.catalog is None else args.catalog, sevenzip=args.sevenzip,
               work=args.work, patchfmt=args.patchfmt, record=getattr(args, 'record', False))
    if args.cmd == 'build':
        if args.game and args.game not in GAMES:
            raise ImportError_('unknown game %r in --game (games: %s)' % (args.game, ', '.join(GAMES)))
        job.update(build=True, out=args.out, repos=game_paths(args.repo, '--repo'), game=args.game,
                   version=args.version, authors=args.author or None)
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
        if r.get('package'):
            detail = '%s via %s -> %s' % (r.get('aki_tier') or '', r.get('converter'), r['package'])
        print('%-11s %-8s %-18s %-8s %s  [%s]' % (r['status'], r.get('game') or '-', what, r.get('tier') or '',
                                                 detail, r['item']))
    done = results[-1]
    print('-- %s' % ', '.join('%d %s' % (v, k) for k, v in sorted(done['counts'].items())) +
          ('' if done['patch_engine'] else ' (patch engine not installed)'))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('plan', 'build'):
        p = sub.add_parser(name, help='scan and classify, print what would be done' if name == 'plan' else
                           'scan, classify and build every ready item into a package under --out')
        p.add_argument('paths', nargs='+')
        p.add_argument('--rom', action='append', metavar='GAME=PATH', help='a port ROM (repeatable)')
        p.add_argument('--rom-dir', action='append', metavar='DIR', help='find stock ROMs in DIR (by SHA-1)')
        p.add_argument('--ledger', help='import ledger JSON (read; build and --record update it)')
        p.add_argument('--catalog', help='recipe catalog JSON (default: catalog.json here; "" for none)')
        p.add_argument('--sevenzip', help='7zr.exe')
        p.add_argument('--work', help='temp folder for unpacked archives and build files')
        p.add_argument('--patchfmt', help='romhack/patchfmt.py to use')
        p.add_argument('--json', help='write the results here')
        if name == 'plan':
            p.add_argument('--record', action='store_true', help='record the ready items in the ledger')
        else:
            p.add_argument('--out', required=True, help='the Converted folder: packages go to OUT\\<game>\\')
            p.add_argument('--game', help='the game for texture packs and bundles that do not say')
            p.add_argument('--version', default='1.0.0', help='package version (x.y.z)')
            p.add_argument('--author', action='append', help='package author (repeatable)')
            p.add_argument('--repo', action='append', metavar='GAME=PATH',
                           help='(developers) a game repo whose RecompiledFuncs tell in-place hacks apart')
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
