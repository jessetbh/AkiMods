"""aki-save: detect, convert and install N64 save files for the AKI recomps (full-conversion saves).

    python aki-save.py detect  <file> [--game G] [--json]
    python aki-save.py convert <file> --game G -o <out> [--to port|raw|pj64|mupen] [--kind K] [--order O] [--pak N]
    python aki-save.py install <file> [<file> ...] --game G --conversion ID [--portable DIR | --saves-root DIR]
                                     [--if-missing] [--dry-run] [--json] [--kind K] [--order O] [--pak N]

Inputs (detected from size, extension and content):
  .fla  FlashRAM 128 KB      Project64 / mupen64plus: 32-bit words byte-reversed ("ws32")
  .sra  SRAM 32 KB           Project64 / mupen64plus: ws32
  .eep  EEPROM 512 B / 2 KB  raw (N64 order) everywhere
  .mpk  Controller Pak       raw; 32 KB (Project64 <game>_Cont_N.mpk) or 128 KB (mupen64plus: 4 paks)
  .srm  RetroArch mupen64plus(-next) 0x48800 bytes: eep 0x800 + 4 paks + sra 0x8000 + fla 0x20000
        (sra/fla sections ws32, the rest raw)
  .bin  the ports' own files (<game id>.bin, <game id>.pak.bin): raw N64 (big-endian) order
  anything else of a valid size: order from the game signature, else raw

The ports keep saves in <app folder>\\saves\\[<subfolder>\\]<game id>.bin (+ <game id>.pak.bin for games with a
separate Controller Pak image). The app folder is %LOCALAPPDATA%\\<Game>Recompiled, or the working directory when
it holds portable.txt. `install` writes only to saves\\<conversion id>\\, never to the normal saves, and backs up any
file it replaces into saves\\<conversion id>\\backups\\ under the game's own backup naming (so the in-game
restore menu lists it). Sources and the full write-up: depot\\plans\\fullconv-status-saves.md.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from array import array

# ------------------------------------------------------------------ sizes and the games

FLASH = 0x20000
SRAM = 0x8000
PAK = 0x8000
EEP4K = 0x200
EEP16K = 0x800
SRM = 0x48800
SRM_LAYOUT = [('eep', 0, EEP16K)] + [('pak%d' % i, EEP16K + i * PAK, PAK) for i in range(4)] + \
             [('sram', EEP16K + 4 * PAK, SRAM), ('flash', EEP16K + 4 * PAK + SRAM, FLASH)]
KINDS = ('flash', 'sram', 'pak', 'eep')
ORDERS = ('be', 'ws32', 'bs16', 'hs16')
ORDER_NAMES = {'be': 'raw N64 / big-endian (ports, cart dumps)',
               'ws32': '32-bit words byte-reversed (Project64, mupen64plus, RetroArch)',
               'bs16': '16-bit halves byte-swapped',
               'hs16': '16-bit halves of each word swapped'}

# Per game: game id (save file stem, src/main/main.cpp game.game_id), the app folder name, and what each save kind
# becomes in the port. 'main' = <game id>.bin, 'pak' = <game id>.pak.bin. WT has no cart save: its Controller Pak
# lives in <game id>.bin (wcw_pak_separate_backing = false, SaveType::Sram used as the pak's 32 KB buffer).
# Revenge and VPW64 save to cart SRAM (32 KB in <game id>.bin); their pak emulation shares that buffer, so a
# separate pak image is not supported for them.
GAMES = {
    'wt':      dict(game_id='wcw.nwo.worldtour.us', appdata='WCWRecompiled', exe='WCWRecompiled',
                    files={'pak': 'main'}, pak_note=(0x3738, b'NWNE')),
    'revenge': dict(game_id='wcw.nwo.revenge.us', appdata='RevengeRecompiled', exe='RevengeRecompiled',
                    files={'sram': 'main'}, magic=0x19971021),
    'wm2k':    dict(game_id='wwf.wm2k.us', appdata='Wm2kRecompiled', exe='Wm2kRecompiled',
                    files={'sram': 'main', 'pak': 'pak'}, magic=0x19990901),
    'vpw64':   dict(game_id='vpw.vpw64.jp', appdata='Vpw64Recompiled', exe='Vpw64Recompiled',
                    files={'sram': 'main'}, magic=0x19971021),
    'nomercy': dict(game_id='wwf.nomercy.us', appdata='NoMercyRecompiled', exe='NoMercyRecompiled',
                    files={'flash': 'main', 'pak': 'pak'}, pak_note=(0x3738, b'NW4E')),
    'vpw2':    dict(game_id='vpw.vpw2.jp', appdata='Vpw2Recompiled', exe='Vpw2Recompiled',
                    files={'sram': 'main', 'pak': 'pak'}, magic=0x19991101, pak_note=(0x4538, b'NA2J')),
}
NM_FLASH_TAG = b'WWF2 AKI'          # No Mercy flash header page (see aki-caw.py / aki-roadmap#51)
NM_PAGE, NM_PAGES = 0x80, 0x3FE     # No Mercy flash log store: 0x80-byte pages, 0..0x3FD used


def nm_page_sum(pg):
    """No Mercy flash page checksum (func_80004054): XOR of the 32 big-endian words, word 1 taken as 0, seed ~0."""
    w = list(struct.unpack('>32I', pg))
    w[1] = 0
    c = 0xFFFFFFFF
    for x in w:
        c ^= x
    return c


def nm_valid_headers(b):
    """Number of No Mercy header pages (type 0x4000, tags, checksum ok) in a raw-order flash image."""
    n = 0
    start = 0
    while True:
        i = b.find(NM_FLASH_TAG, start)
        if i < 0:
            return n
        start = i + 1
        o = i - 0x10
        if o < 0 or o % NM_PAGE or o // NM_PAGE >= NM_PAGES:
            continue
        pg = b[o:o + NM_PAGE]
        if pg[2:4] == b'\x40\x00' and pg[0x20:0x28] == b'NO MERCY' and be32(pg, 4) == nm_page_sum(pg):
            n += 1


class SaveError(Exception):
    """A problem the user has to fix; printed without a traceback."""


# ------------------------------------------------------------------ byte orders

def reorder(data, order):
    """Convert between raw N64 order and `order`. Every order is its own inverse."""
    if order == 'be':
        return bytes(data)
    if len(data) % 4:
        raise SaveError('size %d is not a multiple of 4; cannot apply %s' % (len(data), order))
    if order == 'ws32':
        a = array('I', data)
        if a.itemsize != 4:
            raise SaveError('array("I") is not 32-bit on this platform')
        a.byteswap()
        return a.tobytes()
    if order == 'bs16':
        a = array('H', data)
        a.byteswap()
        return a.tobytes()
    if order == 'hs16':
        # swap 16-bit halves of each 32-bit word = ws32 followed by bs16
        return reorder(reorder(data, 'ws32'), 'bs16')
    raise SaveError('unknown byte order %r' % order)


# ------------------------------------------------------------------ content checks (raw order)

def be16(b, o):
    return (b[o] << 8) | b[o + 1]


def be32(b, o):
    return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]


def is_blank(b):
    return not b or b.count(b[0]) == len(b) and b[0] in (0x00, 0xFF)


def pak_id_ok(b, o):
    """libultra Controller Pak ID block (32 bytes): u16 sum of the first 14 words, then 0xFFF2 - sum."""
    s = sum(be16(b, o + i) for i in range(0, 28, 2)) & 0xFFFF
    return be16(b, o + 28) == s and be16(b, o + 30) == (0xFFF2 - s) & 0xFFFF


def is_pak(b):
    """A formatted Controller Pak: any of the four ID block copies (0x20, 0x60, 0x80, 0xC0) checks out."""
    return len(b) == PAK and any(pak_id_ok(b, o) for o in (0x20, 0x60, 0x80, 0xC0))


def pak_notes(b):
    """(company, game code) of each used note in a raw pak (note table: pages 3-4)."""
    out = []
    for e in range(16):
        ent = b[0x300 + e * 32:0x300 + (e + 1) * 32]
        company = be16(ent, 4)
        if company and ent[0:4] != b'\0\0\0\0':
            out.append((company, bytes(ent[0:4])))
    return out


def aki_magic(b):
    """AKI cart SRAM header: 8 identical BCD-date words 0x19YYMMDD at offset 0. Returns the word or None."""
    if len(b) < 32:
        return None
    w = be32(b, 0)
    if w >> 24 != 0x19 or any(be32(b, o) != w for o in range(4, 32, 4)):
        return None
    if not all((x >> 4) <= 9 and (x & 15) <= 9 for x in w.to_bytes(4, 'big')):
        return None
    return w


def game_signature(b, kind):
    """Which game(s) a raw-order save belongs to, from content alone ([] = unknown)."""
    if kind == 'flash' and NM_FLASH_TAG in b:
        return ['nomercy']
    if kind == 'sram':
        m = aki_magic(b)
        return [g for g, f in GAMES.items() if f.get('magic') == m] if m else []
    if kind == 'pak' and is_pak(b):
        notes = pak_notes(b)
        return [g for g, f in GAMES.items() if f.get('pak_note') in notes]
    return []


def signature_known(b, kind):
    """True when the raw-order content carries a structure we can recognise (game tag, AKI magic, pak ID)."""
    if kind == 'flash':
        return NM_FLASH_TAG in b
    if kind == 'sram':
        return aki_magic(b) is not None
    if kind == 'pak':
        return is_pak(b)
    return False


# ------------------------------------------------------------------ detection

EXT_KIND = {'.fla': 'flash', '.sra': 'sram', '.eep': 'eep', '.mpk': 'pak'}
EMU_EXT = ('.fla', '.sra')          # emulator files whose prior order is ws32


def kind_candidates(size, ext, name):
    """Possible kinds for a file of this size; the extension narrows it down."""
    if name.lower().endswith('.pak.bin'):
        return ['pak'] if size == PAK else []
    by_size = {FLASH: ['flash', 'pak'], SRAM: ['sram', 'pak'], EEP4K: ['eep'], EEP16K: ['eep']}.get(size, [])
    k = EXT_KIND.get(ext)
    if k:
        # Project64 may leave .fla/.sra shorter than the chip (it writes pages lazily); allow padding.
        if k == 'flash' and 0 < size <= FLASH and size % 4 == 0:
            return ['flash']
        if k == 'sram' and 0 < size <= SRAM and size % 4 == 0:
            return ['sram']
        return [k] if k in by_size else []
    return by_size


def detect_buffer(data, path='', kind='auto', order='auto', game=None):
    """Return a dict describing one save image (not .srm). Raises SaveError when it is not a usable save."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    size = len(data)
    kinds = [kind] if kind != 'auto' else kind_candidates(size, ext, name)
    if not kinds:
        raise SaveError('%s: %d bytes is not a FlashRAM (128 KB), SRAM (32 KB), Controller Pak (32/128 KB) or '
                        'EEPROM (512 B / 2 KB) size' % (name or 'input', size))
    note = []
    padded = 0
    if kinds == ['flash'] and size < FLASH:
        padded = FLASH - size
    elif kinds == ['sram'] and size < SRAM:
        padded = SRAM - size
    if padded:
        data = bytes(data) + b'\xff' * padded
        note.append('padded %d bytes of 0xFF (Project64 writes .fla/.sra lazily)' % padded)

    # 128 KB that is 4 mupen64plus paks rather than flash
    if 'pak' in kinds and len(data) == 4 * PAK and kind != 'flash':
        paks = [data[i * PAK:(i + 1) * PAK] for i in range(4)]
        if ext == '.mpk' or any(is_pak(p) for p in paks) and NM_FLASH_TAG not in data:
            return dict(path=path, size=size, kind='pak4', order='be', confidence='structure',
                        paks=[{'index': i, 'formatted': is_pak(p), 'blank': is_blank(p),
                               'notes': ['%04X/%s' % (c, g.decode('latin-1')) for c, g in pak_notes(p)]}
                              for i, p in enumerate(paks)],
                        games=sorted({g for p in paks for g in game_signature(p, 'pak')}), notes=note,
                        raw=bytes(data))
        kinds = [k for k in kinds if k != 'pak']

    # Try every (kind, order) pair; keep those whose raw-order content has a structure we recognise. Only cart
    # SRAM/flash get swapped by emulators, so paks and EEPROMs are tried raw unless --order says otherwise.
    hits = []
    for k in kinds:
        k_orders = ((ORDERS if k in ('flash', 'sram') else ('be',)) if order == 'auto' else (order,))
        for o in k_orders:
            raw = reorder(data, o)
            if signature_known(raw, k):
                hits.append((k, o, raw))
    if game is not None:
        hits.sort(key=lambda h: game not in game_signature(h[2], h[0]))   # stable: the game's own match first
    if hits:
        k, o, raw = hits[0]
        conf = 'signature'
    else:
        # No recognisable structure: the extension sets the order; for 32 KB, SRAM is assumed over a pak.
        k = kinds[0]
        if order != 'auto':
            o = order
            conf = 'given'
        elif k in ('flash', 'sram') and ext in EMU_EXT:
            o, conf = 'ws32', 'extension'
        else:
            o, conf = 'be', 'default'
        raw = reorder(data, o)
        if len(kinds) > 1 and kind == 'auto':
            note.append('size fits %s; assumed %s (pass --kind to choose)' % (' or '.join(kinds), k))
    info = dict(path=path, size=size, kind=k, order=o, confidence=conf, blank=is_blank(raw),
                games=game_signature(raw, k), notes=note, raw=raw)
    if k == 'eep':
        info['kind'] = 'eep4k' if len(raw) == EEP4K else 'eep16k'
    if k == 'pak':
        info['pak_notes'] = ['%04X/%s' % (c, g.decode('latin-1')) for c, g in pak_notes(raw)]
        info['formatted'] = is_pak(raw)
    if k == 'flash' and NM_FLASH_TAG in raw:
        info['nm_valid_headers'] = nm_valid_headers(raw)
    m = aki_magic(raw) if k == 'sram' else None
    if m:
        info['aki_magic'] = '%08X' % m
    return info


def srm_sections(data):
    """RetroArch mupen64plus .srm: each section in raw order, plus whether it holds anything."""
    out = {}
    for name, off, size in SRM_LAYOUT:
        part = data[off:off + size]
        o = 'ws32' if name in ('sram', 'flash') else 'be'
        raw = reorder(part, o)
        if name.startswith('pak'):
            used = bool(pak_notes(raw)) if is_pak(raw) else not is_blank(raw)
        else:
            used = not is_blank(raw)
        out[name] = dict(order=o, used=used, raw=raw)
    return out


def detect(path, kind='auto', order='auto', game=None):
    data = read_file(path)
    if len(data) == SRM and kind == 'auto':
        secs = srm_sections(data)
        return dict(path=path, size=len(data), kind='srm', order='mixed', confidence='size',
                    sections={n: dict(order=s['order'], used=s['used'],
                                      games=game_signature(s['raw'], 'pak' if n.startswith('pak') else n))
                              for n, s in secs.items()},
                    notes=[], _secs=secs)
    return detect_buffer(data, path, kind, order, game)


def read_file(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError as e:
        raise SaveError('cannot read %s: %s' % (path, e))


# ------------------------------------------------------------------ conversion to the port's files

def port_files(path, game, kind='auto', order='auto', pak=None, force=False):
    """Convert one input to the port's file(s) for `game`: list of (target 'main'|'pak', raw bytes, info)."""
    g = GAMES[game]
    info = detect(path, kind, order, game)
    pieces = []      # (kind, raw, source description)
    if info['kind'] == 'srm':
        for name, s in info['_secs'].items():
            k = 'pak' if name.startswith('pak') else name
            if not s['used'] or k not in g['files']:
                continue
            if k == 'pak' and name != 'pak%d' % (pak or 0):
                continue
            pieces.append((k, s['raw'], 'srm %s' % name))
        if not pieces:
            used = [n for n, s in info['_secs'].items() if s['used']]
            raise SaveError('%s: the .srm holds nothing %s uses (used sections: %s; %s saves to %s)'
                            % (path, game, ', '.join(used) or 'none', game, ' + '.join(g['files'])))
    elif info['kind'] == 'pak4':
        idx = pak if pak is not None else next((p['index'] for p in info['paks'] if p['notes']), 0)
        pieces.append(('pak', info['raw'][idx * PAK:(idx + 1) * PAK], 'mpk pak %d' % idx))
    else:
        k = 'eep' if info['kind'].startswith('eep') else info['kind']
        pieces.append((k, info['raw'], '%s (%s)' % (k, info['order'])))

    out = []
    for k, raw, src in pieces:
        if k not in g['files']:
            raise SaveError('%s: %s does not use %s saves (it uses %s)' % (path, game, k, ' + '.join(g['files'])))
        if is_blank(raw) and not force:
            raise SaveError('%s: the %s data is blank (all 0x00/0xFF); nothing to install' % (path, k))
        if k == 'pak' and not is_pak(raw) and not force:
            raise SaveError('%s: not a formatted Controller Pak (no valid ID block); --force to use it anyway' % path)
        sig = game_signature(raw, k)
        if k in ('flash', 'sram'):
            if game not in sig and not force:
                who = (' (it looks like %s)' % '/'.join(sig)) if sig else ''
                raise SaveError('%s: no %s %s signature in any byte order%s; pass --order and --force if you are '
                                'sure' % (path, game, k, who))
            if game == 'nomercy' and k == 'flash' and not nm_valid_headers(raw) and not force:
                raise SaveError('%s: No Mercy flash has no header page with a valid checksum (corrupt, or a partial '
                                'save); --force to use it anyway' % path)
        elif k == 'pak' and g.get('pak_note') and game not in sig:
            info.setdefault('notes', []).append('the pak has no %s note (notes: %s)' % (
                game, ', '.join('%04X/%s' % (c, n.decode('latin-1')) for c, n in pak_notes(raw)) or 'none'))
        out.append((g['files'][k], raw, dict(src=src, kind=k)))
    targets = [t for t, _, _ in out]
    if len(set(targets)) != len(targets):
        raise SaveError('%s: more than one input for the same port file' % path)
    return out, info


def port_name(game, target):
    gid = GAMES[game]['game_id']
    return gid + ('.bin' if target == 'main' else '.pak.bin')


def export_bytes(raw, kind, to):
    """raw -> another tool's order: raw (N64/port), pj64/mupen (ws32 for sram/flash)."""
    if to in ('port', 'raw'):
        return raw
    if to in ('pj64', 'mupen'):
        return reorder(raw, 'ws32') if kind in ('sram', 'flash') else raw
    raise SaveError('unknown target %r' % to)


# ------------------------------------------------------------------ install

ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


def check_conversion_id(cid):
    if not ID_RE.match(cid or '') or cid.lower() in ('backups',) or '..' in cid:
        raise SaveError('conversion id %r: use 1-64 letters, digits, "_", "-" or "." (not "backups"); it becomes '
                        'the saves\\<id>\\ folder' % cid)


def saves_root(game, portable=None, root=None, localappdata=None):
    if root:
        return os.path.abspath(root)
    if portable:
        return os.path.join(os.path.abspath(portable), 'saves')
    lad = localappdata or os.environ.get('LOCALAPPDATA', '')
    if not lad:
        raise SaveError('LOCALAPPDATA is not set; pass --portable or --saves-root')
    return os.path.join(lad, GAMES[game]['appdata'], 'saves')


def game_running(game):
    exe = GAMES[game]['exe'] + '.exe'
    if os.name != 'nt':
        return False
    try:
        r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % exe, '/NH'], capture_output=True, text=True,
                           timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return exe.lower() in r.stdout.lower()


def backup_name(file_name, when):
    """The game's own backup naming (librecomp save_backup_rules.cpp): stem without the final .bin + stamp."""
    stem = file_name[:-4] if file_name.endswith('.bin') else file_name
    return '%s.%s.bin' % (stem, when.strftime('%Y%m%d-%H%M%S'))


def write_atomic(path, data):
    tmp = path + '.aki-save.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def install(inputs, game, conversion, portable=None, root=None, kind='auto', order='auto', pak=None,
            if_missing=False, dry_run=False, force=False, ignore_running=False, now=None):
    check_conversion_id(conversion)
    if portable and not os.path.isfile(os.path.join(portable, 'portable.txt')):
        sys.stderr.write('aki-save: warning: %s has no portable.txt; the game only uses it as its folder '
                         'when it does\n' % portable)
    base = saves_root(game, portable, root)
    folder = os.path.join(base, conversion)
    if os.path.normcase(os.path.dirname(os.path.abspath(folder))) != os.path.normcase(base):
        raise SaveError('conversion folder escapes the saves folder')
    if not dry_run and not ignore_running and game_running(game):
        raise SaveError('%s is running; close it first (it would overwrite the save on its next write)'
                        % GAMES[game]['exe'])
    plan = []
    for p in inputs:
        files, info = port_files(p, game, kind, order, pak, force)
        for target, raw, meta in files:
            plan.append(dict(input=p, target=target, raw=raw, meta=meta, notes=info.get('notes', []),
                             order=info.get('order')))
    seen = [x['target'] for x in plan]
    if len(set(seen)) != len(seen):
        raise SaveError('two inputs map to the same port file (%s)' % ', '.join(sorted(seen)))

    when = now or datetime.datetime.now()
    results = []
    for x in plan:
        name = port_name(game, x['target'])
        dest = os.path.join(folder, name)
        r = dict(input=x['input'], file=dest, kind=x['meta']['kind'], source=x['meta']['src'],
                 order=x['order'], sha1=hashlib.sha1(x['raw']).hexdigest(), notes=x['notes'], action='write',
                 backup=None)
        exists = os.path.isfile(dest)
        if exists:
            with open(dest, 'rb') as f:
                cur = f.read()
            if cur == x['raw']:
                r['action'] = 'unchanged'
            elif if_missing:
                r['action'] = 'kept (exists)'
            else:
                r['action'] = 'replace'
                r['backup'] = os.path.join(folder, 'backups', backup_name(name, when))
        results.append(r)
        if dry_run or r['action'] in ('unchanged', 'kept (exists)'):
            continue
        os.makedirs(folder, exist_ok=True)
        if r['backup']:
            os.makedirs(os.path.dirname(r['backup']), exist_ok=True)
            b, n = r['backup'], 1
            while os.path.exists(b):
                b = r['backup'][:-4] + '-%d.bin' % n
                n += 1
            r['backup'] = b
            with open(dest, 'rb') as src, open(b, 'wb') as dst:
                dst.write(src.read())
        write_atomic(dest, x['raw'])
    return dict(game=game, conversion=conversion, folder=folder, dry_run=dry_run, files=results)


# ------------------------------------------------------------------ CLI

def public(info):
    return {k: v for k, v in info.items() if k not in ('raw', '_secs')}


def cmd_detect(a):
    info = detect(a.file, a.kind, a.order, a.game)
    if a.json:
        print(json.dumps(public(info), indent=2))
        return
    if info['kind'] == 'srm':
        print('%s: RetroArch mupen64plus .srm (0x%X bytes)' % (a.file, info['size']))
        for n, s in info['sections'].items():
            print('  %-5s %-4s %s%s' % (n, s['order'], 'used' if s['used'] else 'empty',
                                        (' -> ' + '/'.join(s['games'])) if s['games'] else ''))
        return
    print('%s: %s, %d bytes, order %s [%s] (by %s)' % (a.file, info['kind'], info['size'], info['order'],
                                                        ORDER_NAMES.get(info['order'], ''), info['confidence']))
    if info['kind'] == 'pak4':
        for p in info['paks']:
            print('  pak %d: %s%s' % (p['index'], 'formatted' if p['formatted'] else 'blank' if p['blank'] else
                                      'unformatted', (' notes ' + ', '.join(p['notes'])) if p['notes'] else ''))
    if info.get('games'):
        print('  game: %s' % ', '.join(info['games']))
    if 'nm_valid_headers' in info:
        print('  No Mercy flash: %d valid header page(s)' % info['nm_valid_headers'])
    if info.get('aki_magic'):
        print('  AKI SRAM magic %s' % info['aki_magic'])
    if info.get('pak_notes'):
        print('  pak notes: %s' % ', '.join(info['pak_notes']))
    if info.get('blank'):
        print('  blank (all 0x00/0xFF)')
    for n in info.get('notes', []):
        print('  note: %s' % n)


def cmd_convert(a):
    files, info = port_files(a.file, a.game, a.kind, a.order, a.pak, a.force)
    if len(files) > 1 and not os.path.isdir(a.out):
        raise SaveError('the input gives %d files (%s); pass a folder as -o'
                        % (len(files), ', '.join(port_name(a.game, t) for t, _, _ in files)))
    for target, raw, meta in files:
        out = os.path.join(a.out, port_name(a.game, target)) if os.path.isdir(a.out) else a.out
        data = export_bytes(raw, meta['kind'], a.to)
        if os.path.exists(out) and not a.overwrite:
            raise SaveError('%s exists; pass --overwrite' % out)
        write_atomic(out, data)
        print('wrote %s (%s from %s, %s order, port name %s)' % (
            out, meta['kind'], meta['src'], 'ws32' if data != raw else 'raw', port_name(a.game, target)))
    for n in info.get('notes', []):
        print('note: %s' % n)


def cmd_install(a):
    res = install(a.files, a.game, a.conversion, a.portable, a.saves_root, a.kind, a.order, a.pak,
                  a.if_missing, a.dry_run, a.force, a.ignore_running)
    if a.json:
        print(json.dumps(res, indent=2))
        return
    print('%s%s -> %s' % ('(dry run) ' if a.dry_run else '', a.game, res['folder']))
    for r in res['files']:
        print('  %-14s %s  <- %s [%s]' % (r['action'], os.path.basename(r['file']), r['input'], r['source']))
        if r['backup']:
            print('  %-14s %s' % ('backed up to', r['backup']))
        for n in r['notes']:
            print('  note: %s' % n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p, game_required):
        p.add_argument('--game', choices=sorted(GAMES), required=game_required)
        p.add_argument('--kind', choices=('auto',) + KINDS, default='auto', help='save type (default: detect)')
        p.add_argument('--order', choices=('auto',) + ORDERS, default='auto', help='input byte order')
        p.add_argument('--pak', type=int, choices=range(4), help='which pak of a 4-pak .mpk / .srm (default: '
                       'the first with notes)')
        p.add_argument('--force', action='store_true', help='accept blank, unrecognised or other-game saves')

    p = sub.add_parser('detect', help='describe a save file')
    p.add_argument('file')
    common(p, False)
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_detect)

    p = sub.add_parser('convert', help='write the port file(s) for one input')
    p.add_argument('file')
    common(p, True)
    p.add_argument('-o', '--out', required=True, help='output file, or a folder (port file names)')
    p.add_argument('--to', choices=('port', 'raw', 'pj64', 'mupen'), default='port')
    p.add_argument('--overwrite', action='store_true')
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser('install', help="put saves into a conversion's saves\\<id>\\ folder")
    p.add_argument('files', nargs='+')
    common(p, True)
    p.add_argument('--conversion', required=True, help='conversion id = the saves subfolder')
    loc = p.add_mutually_exclusive_group()
    loc.add_argument('--portable', help='portable app folder (has portable.txt); saves go to <dir>\\saves\\<id>')
    loc.add_argument('--saves-root', help='explicit saves folder (default %%LOCALAPPDATA%%\\<Game>Recompiled\\saves)')
    p.add_argument('--if-missing', action='store_true', help="keep existing files (seed a fresh install only)")
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--json', action='store_true')
    p.add_argument('--ignore-running', action='store_true', help='skip the "game is running" check')
    p.set_defaults(fn=cmd_install)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except SaveError as e:
        sys.exit('aki-save: %s' % e)


if __name__ == '__main__':
    main()
