"""AKI Starter Kit (No Mercy): assembles, checks, installs and tests the example conversion. CC0 1.0.

build.ps1 calls this after it has built the code change (patch.nrm). You can also run the steps yourself:

    python kit.py assemble --nrm PATCH.nrm --out OUT [--blank-save BLANK.bin]
        OUT\aki_starter_kit\                 the conversion folder (aki.json and every component)
        OUT\_packages\AKI-Starter_Kit_Textures\   the texture package, unzipped (what Install puts in _packages)
        OUT\AKI-Starter_Kit_Textures-1.0.0.zip    the texture package as players add it (aki.json pins its sha256)
    python kit.py validate --out OUT [--akitools DIR]
        the format checks: our own (every file aki.json names exists, slots match the roster and the packs, the
        codes file parses, the rig is JSON with comments) plus AkiTools aki-package.py validate when it's there
    python kit.py install --out OUT --game-dir DIR
        copies the conversion and its texture package into DIR\conversions\ (DIR = a portable game checkout or the
        game's app folder) and seeds the save into DIR\saves\aki_starter_kit\ if there is none yet
    python kit.py check --run RUN_DIR [--game-dir DIR]
        reads an aki-run output folder (stderr.txt) and checks that every part of the conversion showed up

--inputs FILE (every command): build another conversion from the same kit, from a JSON file of your own (keep it
outside the repo when it names private files). Keys, all optional:
    id, name, version, description, credits, permission, license, links   aki.json fields (id also = the patch mod id)
    slots     [{"wrestler": "0x0404", "label": "...", "pack": "<id>", "akimodel": "<path to <id>.akimodel>"}]
              (no "akimodel" = one of the kit's starter packs)
    hd_rtz    [paths of .rtz files to put beside aki.json] (default: the starter packs' HD textures)
    textures  {"package": "Team-Name", "folder": "Name", "version": "x.y.z", "dir": "<folder of <rt64 hash>.png>"}
              (no "dir" = the kit's own drawn textures)
    rt, codes, patch, saves   false leaves that part out

Two things the launcher does that a plain test run doesn't (aki-roadmap#227 gaps, filed separately):
  * The RT rig: the game reads the conversion's rig only from WCW_RT_LIGHTS_FILE, which the launcher sets. A test run
    must set it itself; build.ps1 -Run does (the path kit.py install prints).
  * The seed save: only the launcher's Install copies it into saves\<id>\. kit.py install does the same (and never
    over an existing save).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

KIT = os.path.dirname(os.path.abspath(__file__))
AKIMODS = os.path.dirname(os.path.dirname(KIT))
sys.path.insert(0, os.path.join(KIT, 'save'))
sys.path.insert(0, os.path.join(KIT, 'textures'))

CONV_ID = 'aki_starter_kit'
PATCH_ID = 'aki_starter_kit'
TEX_PACKAGE = 'AKI-Starter_Kit_Textures'
TEX_FOLDER = 'Starter_Kit_Textures'
TEX_VERSION = '1.0.0'
GAME = 'nomercy'
SAVE_NAME = 'wwf.nomercy.us.bin'
PACKS = os.path.join(AKIMODS, 'mods', 'modelimport', 'packs', GAME)
HD_RTZ = 'aki_starter_packs_hd.rtz'
FIXED_TIME = (2026, 11, 8, 0, 0, 0)
INPUTS = {}  # --inputs: another conversion built from this kit


def apply_inputs(path):
    global CONV_ID, PATCH_ID, TEX_PACKAGE, TEX_FOLDER, TEX_VERSION, INPUTS
    if not path:
        return
    with open(path, encoding='utf-8-sig') as f:
        INPUTS = json.load(f)
    if 'id' in INPUTS:
        if not re.fullmatch(r'[a-z0-9_]{1,48}', INPUTS['id']):
            fail('inputs: id must be a-z, 0-9 and _ (it is also the patch mod id)')
        CONV_ID = PATCH_ID = INPUTS['id']
    tex = INPUTS.get('textures') or {}
    TEX_PACKAGE = tex.get('package', TEX_PACKAGE)
    TEX_FOLDER = tex.get('folder', TEX_FOLDER)
    TEX_VERSION = tex.get('version', TEX_VERSION)


def part_on(name):
    return INPUTS.get(name, True) is not False


def fail(msg):
    print('ERROR    ' + msg)
    sys.exit(2)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def strip_comments(text):
    """JSON with // comments (the rig format) -> plain JSON. Keeps // inside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith('//', i):
            while i < n and text[i] != '\n':
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def write_json(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write('\n')


def deterministic_zip(folder, out):
    """Zips folder's contents with sorted names and fixed times: the same files always give the same bytes."""
    names = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files):
            names.append(os.path.relpath(os.path.join(root, f), folder).replace(os.sep, '/'))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in sorted(names):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(folder, name), 'rb') as f:
                z.writestr(info, f.read())


def load_slots():
    if 'slots' in INPUTS:
        return INPUTS['slots']
    with open(os.path.join(KIT, 'models', 'slots.json'), encoding='utf-8') as f:
        return json.load(f)['slots']


# --- assemble ------------------------------------------------------------------------------------------------------

def make_texture_package(out):
    import make_textures
    pkg = os.path.join(out, '_packages', TEX_PACKAGE)
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)
    tex = os.path.join(pkg, TEX_FOLDER)
    src_dir = (INPUTS.get('textures') or {}).get('dir')
    if src_dir:
        # Someone else's images: copied as they are (<rt64 hash>.png/.dds), with an rt64.json that matches by name.
        os.makedirs(tex)
        for n in sorted(os.listdir(src_dir)):
            if n.lower().endswith(('.png', '.dds')):
                shutil.copyfile(os.path.join(src_dir, n), os.path.join(tex, n))
        if os.path.isfile(os.path.join(src_dir, 'rt64.json')):
            shutil.copyfile(os.path.join(src_dir, 'rt64.json'), os.path.join(tex, 'rt64.json'))  # its own settings
        else:
            write_json(os.path.join(tex, 'rt64.json'), make_textures.RT64_JSON)
    elif make_textures.main(['make_textures.py', tex]) != 0:
        fail('make_textures.py failed')
    images = len([n for n in os.listdir(tex) if n.endswith('.png')])
    write_json(os.path.join(pkg, 'aki.json'), {
        'format': 'aki-package', 'format_version': 1, 'kind': 'textures', 'game': GAME,
        'naming': 'rt64', 'folder': TEX_FOLDER, 'images': images})
    write_json(os.path.join(pkg, 'manifest.json'), {
        'name': TEX_FOLDER, 'version_number': TEX_VERSION,
        'website_url': 'https://github.com/jessetbh/AkiMods/tree/main/examples/starter-nomercy',
        'description': 'AKI Starter Kit textures: ring mat, apron logo, turnbuckle pads (original, CC0, rt64 naming)',
        'dependencies': []})
    with open(os.path.join(pkg, 'README.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('# AKI Starter Kit textures\n\n%d original images (CC0 1.0), rt64 naming: each file is named by the rt64 hash '
                'of the texture it replaces. Drawn by examples/starter-nomercy/textures/make_textures.py in AkiMods.\n' % images)
    zip_path = os.path.join(out, '%s-%s.zip' % (TEX_PACKAGE, TEX_VERSION))
    deterministic_zip(pkg, zip_path)
    return zip_path, sha256(zip_path)


def cmd_assemble(a):
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    conv = os.path.join(out, CONV_ID)
    if os.path.isdir(conv):
        shutil.rmtree(conv)
    os.makedirs(conv)

    # 1. Code change: the .nrm build.ps1 built from patch\.
    if part_on('patch'):
        if not a.nrm or not os.path.isfile(a.nrm):
            fail('%s: build the patch first (build.ps1)' % a.nrm)
        shutil.copyfile(a.nrm, os.path.join(conv, 'patch.nrm'))
    # 2. Codes, 3. RT rig, docs and thumbnail.
    if part_on('codes'):
        shutil.copyfile(os.path.join(KIT, 'codes.txt'), os.path.join(conv, 'codes.txt'))
    if part_on('rt'):
        shutil.copyfile(os.path.join(KIT, 'rt', 'rt_lights.json'), os.path.join(conv, 'rt_lights.json'))
    shutil.copyfile(os.path.join(KIT, 'conversion', 'README.md'), os.path.join(conv, 'README.md'))
    shutil.copyfile(os.path.join(KIT, 'COPYING.CC0'), os.path.join(conv, 'COPYING.CC0'))
    make_icon(os.path.join(conv, 'icon.png'))

    # 4. Textures: a separate package, pinned by sha256.
    zip_path, tex_sha = make_texture_package(out)

    # 5. Models: the eight starter packs, their HD textures beside aki.json, slots + roster.
    slots = load_slots()
    models = os.path.join(conv, 'models')
    os.makedirs(models)
    for pack in sorted({s['pack'] for s in slots}):
        given = [s['akimodel'] for s in slots if s['pack'] == pack and s.get('akimodel')]
        src = given[0] if given else os.path.join(PACKS, pack + '.akimodel')
        if not os.path.isdir(src):
            fail('model pack %s not found (%s)' % (pack, src))
        shutil.copytree(src, os.path.join(models, pack + '.akimodel'))
    for rtz in INPUTS.get('hd_rtz', [os.path.join(PACKS, HD_RTZ)]):
        name = os.path.basename(rtz)
        if name == HD_RTZ:
            name = 'aki_starter_packs-hd.rtz'
        shutil.copyfile(rtz, os.path.join(conv, name))
    write_json(os.path.join(models, 'roster.json'), {
        'format': 1, 'game': GAME,
        'wrestlers': [{'id': s['wrestler'], 'name': s['label']} for s in slots]})

    # 6. Seed save, made from the builder's own blank save (a save file holds ROM-built data: never committed).
    manifest = json.load(open(os.path.join(KIT, 'conversion', 'aki.json'), encoding='utf-8'))
    for key in ('name', 'version', 'description', 'credits', 'permission', 'license', 'links'):
        if key in INPUTS:
            manifest[key] = INPUTS[key]
    manifest['id'] = CONV_ID
    comps = manifest['components']
    comps['patch']['mod_id'] = PATCH_ID
    comps['textures'].update({'package': TEX_PACKAGE, 'version': TEX_VERSION})
    for part in ('patch', 'codes', 'rt'):
        if not part_on(part):
            del comps[part]
    comps['textures']['sha256'] = tex_sha
    comps['models']['slots'] = [{'wrestler': s['wrestler'], 'name': s['label'], 'pack': s['pack']} for s in slots]
    if a.blank_save and part_on('saves'):
        import seed_save
        with open(a.blank_save, 'rb') as f:
            seeded = seed_save.apply_seed_edit(f.read())
        os.makedirs(os.path.join(conv, 'saves'))
        with open(os.path.join(conv, 'saves', SAVE_NAME), 'wb') as f:
            f.write(seeded)
    else:
        if part_on('saves'):
            print('warning  no --blank-save: the conversion is built without its seed save (components.saves left out)')
        del comps['saves']
    write_json(os.path.join(conv, 'aki.json'), manifest)
    print('assembled %s\n  texture package %s\n  sha256 %s' % (conv, zip_path, tex_sha))
    return 0


def make_icon(path):
    # A 256x256 thumbnail in the kit's colours (drawn here, CC0).
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (28, 30, 44, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, 57, 57], outline=(40, 190, 180, 255), width=4)
    d.polygon([(32, 14), (37, 27), (51, 27), (40, 36), (44, 50), (32, 42), (20, 50), (24, 36), (13, 27), (27, 27)],
              fill=(125, 75, 205, 255))
    img.resize((256, 256), Image.NEAREST).save(path, optimize=False)


# --- validate ------------------------------------------------------------------------------------------------------

def cmd_validate(a):
    out = os.path.abspath(a.out)
    conv = os.path.join(out, CONV_ID)
    errors, notes = [], []
    if not os.path.isfile(os.path.join(conv, 'aki.json')):
        fail('%s has no aki.json: run assemble first' % conv)
    m = json.load(open(os.path.join(conv, 'aki.json'), encoding='utf-8'))
    c = m['components']
    if m['id'] != CONV_ID:
        errors.append('aki.json id %s != folder %s' % (m['id'], CONV_ID))
    for key in ('patch', 'codes'):
        if key in c and not os.path.isfile(os.path.join(conv, c[key]['file'])):
            errors.append('%s file %s missing' % (key, c[key]['file']))
    # The patch .nrm's mod id must be the one aki.json names.
    if 'patch' in c:
        with zipfile.ZipFile(os.path.join(conv, c['patch']['file'])) as z:
            mod_json = json.loads(z.read('mod.json').decode('utf-8'))
        if mod_json.get('id') != c['patch']['mod_id']:
            errors.append('patch.nrm id %s != components.patch.mod_id %s' % (mod_json.get('id'), c['patch']['mod_id']))
    # Codes: every code line is 8 + 4 hex digits; the rom tag is the port's revision.
    codes = [l.strip() for l in open(os.path.join(conv, c['codes']['file']), encoding='utf-8')] if 'codes' in c else []
    code_lines = [l for l in codes if l and not l.startswith('#') and not l.startswith('[')]
    bad = [l for l in code_lines if not re.fullmatch(r'[0-9A-Fa-f]{8} [0-9A-Fa-f]{4}', l)]
    if bad:
        errors.append('codes.txt: malformed lines %s' % bad[:3])
    if 'codes' in c and not any(re.match(r'#\s*rom:\s*nomercy-rev1\s*$', l) for l in codes):
        errors.append('codes.txt: no "# rom: nomercy-rev1" tag')
    notes.append('codes: %d code lines' % len(code_lines))
    # RT rig: JSON with comments, a default and at least one venue.
    rig = {'default': {}, 'venues': []}
    if 'rt' in c and not os.path.isfile(os.path.join(conv, c['rt']['lights'])):
        errors.append('rt lights file %s missing' % c['rt']['lights'])
    elif 'rt' in c:
        try:
            rig = json.loads(strip_comments(open(os.path.join(conv, c['rt']['lights']), encoding='utf-8').read()))
        except ValueError as e:
            errors.append('rt_lights.json is not valid JSON (with comments): %s' % e)
    if 'rt' in c and ('default' not in rig or not rig.get('venues')):
        errors.append('rt_lights.json: needs "default" and "venues"')
    notes.append('rig: %d venue(s)' % len(rig.get('venues', [])))
    # Saves.
    for sv in c.get('saves', []):
        p = os.path.join(conv, sv['file'])
        if not os.path.isfile(p) or os.path.getsize(p) != 0x20000:
            errors.append('save %s missing or not 128 KB' % sv['file'])
    if 'saves' not in c:
        notes.append('no seed save (built without --blank-save)')
    # Textures: the package zip's sha256 is the one aki.json pins.
    zip_path = os.path.join(out, '%s-%s.zip' % (TEX_PACKAGE, TEX_VERSION))
    if sha256(zip_path) != c['textures']['sha256']:
        errors.append('texture package sha256 differs from aki.json')
    # Models: every slot's pack is there and has an AKP1 model; every wrestler id is in the roster.
    md = os.path.join(conv, c['models']['dir'])
    roster = json.load(open(os.path.join(conv, c['models']['roster']), encoding='utf-8'))
    ids = {w['id'].lower() for w in roster['wrestlers']}
    for s in c['models']['slots']:
        mb = os.path.join(md, s['pack'] + '.akimodel', 'model.bin')
        if not os.path.isfile(mb):
            errors.append('slot %s: pack %s missing' % (s['wrestler'], s['pack']))
        elif open(mb, 'rb').read(4) != b'AKP1':
            errors.append('slot %s: %s is not an AKP1 model' % (s['wrestler'], s['pack']))
        if s['wrestler'].lower() not in ids:
            errors.append('slot %s not in roster.json' % s['wrestler'])
    notes.append('models: %d slots' % len(c['models']['slots']))

    # AkiTools: the same checks the launcher's Install makes (schema, models vs roster, relink refusal ...).
    tools = a.akitools or os.path.join(os.path.dirname(AKIMODS), 'AkiTools')
    tool = os.path.join(tools, 'aki-package.py')
    if os.path.isfile(tool):
        stage = os.path.join(out, '_package-check')
        if os.path.isdir(stage):
            shutil.rmtree(stage)
        os.makedirs(stage)
        pkg_zip = os.path.join(stage, '%s-%s.zip' % (CONV_ID, m['version']))
        r = subprocess.run([sys.executable, tool, 'package', '--from', conv, '-o', pkg_zip, '--license', m.get('license', 'CC0-1.0'),
                            '--base-name', 'WWF No Mercy (USA) (Rev 1)'], capture_output=True, text=True)
        print(r.stdout.strip())
        if r.stderr.strip():
            print(r.stderr.strip())
        if r.returncode not in (0, 1) or not os.path.isfile(pkg_zip):
            errors.append('aki-package package --from refused the conversion (above)')
        else:
            r = subprocess.run([sys.executable, tool, 'validate', pkg_zip], capture_output=True, text=True)
            print(r.stdout.strip())
            if r.stderr.strip():
                print(r.stderr.strip())
            if r.returncode not in (0, 1):
                errors.append('aki-package validate failed (above)')
            else:
                shutil.copyfile(pkg_zip, os.path.join(out, os.path.basename(pkg_zip)))
                notes.append('player package: %s' % os.path.join(out, os.path.basename(pkg_zip)))
        shutil.rmtree(stage, ignore_errors=True)
    else:
        notes.append('AkiTools not found (%s): skipped aki-package validate' % tools)

    for n in notes:
        print('ok       ' + n)
    for e in errors:
        print('ERROR    ' + e)
    print('validate: %s' % ('FAILED' if errors else 'passed'))
    return 2 if errors else 0


# --- install -------------------------------------------------------------------------------------------------------

def cmd_install(a):
    out = os.path.abspath(a.out)
    game = os.path.abspath(a.game_dir)
    if not os.path.isdir(game):
        fail('%s is not a folder' % game)
    convs = os.path.join(game, 'conversions')
    dst = os.path.join(convs, CONV_ID)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(out, CONV_ID), dst)
    pkg_dst = os.path.join(convs, '_packages', TEX_PACKAGE)
    if os.path.isdir(pkg_dst):
        shutil.rmtree(pkg_dst)
    shutil.copytree(os.path.join(out, '_packages', TEX_PACKAGE), pkg_dst)
    # What the launcher writes when it adds a package zip: the zip's sha256, so it can tell the pinned version.
    with open(os.path.join(pkg_dst, '.package.sha256'), 'w', newline='') as f:
        f.write(sha256(os.path.join(out, '%s-%s.zip' % (TEX_PACKAGE, TEX_VERSION))))
    # The seed save: the launcher's Install copies it into saves\<id>\ once; never over the player's progress.
    m = json.load(open(os.path.join(dst, 'aki.json'), encoding='utf-8'))
    saves = os.path.join(game, 'saves', CONV_ID)
    for sv in m['components'].get('saves', []):
        os.makedirs(saves, exist_ok=True)
        to = os.path.join(saves, os.path.basename(sv['file']))
        if os.path.exists(to):
            print('kept     %s (a save is already there)' % to)
        else:
            shutil.copyfile(os.path.join(dst, sv['file']), to)
            print('seeded   %s' % to)
    rig = m['components'].get('rt', {}).get('lights')
    print('installed %s\n  run it with AKI_CONVERSION=%s%s' %
          (dst, CONV_ID, ' and WCW_RT_LIGHTS_FILE=%s' % os.path.join(dst, rig) if rig else ''))
    return 0


# --- check a run ---------------------------------------------------------------------------------------------------

def cmd_check(a):
    log = open(os.path.join(a.run, 'stderr.txt'), encoding='utf-8', errors='replace').read()
    slots = load_slots()
    parts = [p for p in ('patch', 'codes', 'saves', 'textures', 'rt', 'models') if p in ('textures', 'models') or part_on(p)]
    checks = [
        ('conversion starts with every part', r'\[conversion\] running %s \([^)]*\): %s;' % (CONV_ID, ' '.join(parts))),
        ('texture package loads', r'\[conversion\] mod %s enabled for this session' % TEX_FOLDER),
        ('model import on', r'\[conversion\] aki_modelimport: on'),
        ('all slots served', r'\[mod\] models.txt: %d lines' % len(slots)),
    ]
    if part_on('patch'):
        checks.append(('code change (patch.nrm) loads', r'\[conversion\] mod %s enabled for this session' % PATCH_ID))
    if part_on('codes'):
        checks.append(('codes run', r'\[conversion\] codes: [1-9]\d* active'))
    if part_on('rt'):
        checks.append(('RT rig is the conversion\'s', r'\[wcw rt\] light presets loaded from [^\n]*%s[^\n]*: 1 venue' % CONV_ID))
    bad = 0
    for name, rx in checks:
        ok = re.search(rx, log) is not None
        bad += not ok
        print('%s %s' % ('ok      ' if ok else 'MISSING ', name))
    drawn = sorted(set(re.findall(r'\[mod\] slot \d+ \(wrestler ([0-9a-fA-F]{4})\): ([\w.-]+)', log)))
    print('info     slots the mod reported: %s' % (', '.join('%s=%s' % d for d in drawn) or 'none'))
    if a.game_dir and part_on('saves'):
        seed = os.path.join(a.game_dir, 'saves', CONV_ID, SAVE_NAME)
        ok = os.path.isfile(seed)
        bad += not ok
        print('%s seed save in %s' % ('ok      ' if ok else 'MISSING ', seed))
    for hit in re.findall(r'[^\n]*(?:crash|exception)[^\n]*', log, re.I)[:3]:
        print('note     ' + hit.strip()[:160])
    print('check: %s' % ('FAILED (%d missing)' % bad if bad else 'passed'))
    return 2 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('assemble'); s.add_argument('--nrm'); s.add_argument('--out', required=True)
    s.add_argument('--blank-save')
    s = sub.add_parser('validate'); s.add_argument('--out', required=True); s.add_argument('--akitools')
    s = sub.add_parser('install'); s.add_argument('--out', required=True); s.add_argument('--game-dir', required=True)
    s = sub.add_parser('check'); s.add_argument('--run', required=True); s.add_argument('--game-dir')
    for s in sub.choices.values():
        s.add_argument('--inputs', help='JSON file: build another conversion from this kit (see the top of kit.py)')
    a = p.parse_args(argv)
    apply_inputs(a.inputs)
    return {'assemble': cmd_assemble, 'validate': cmd_validate, 'install': cmd_install, 'check': cmd_check}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
