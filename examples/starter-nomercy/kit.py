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
    with open(os.path.join(KIT, 'models', 'slots.json'), encoding='utf-8') as f:
        return json.load(f)['slots']


# --- assemble ------------------------------------------------------------------------------------------------------

def make_texture_package(out):
    import make_textures
    pkg = os.path.join(out, '_packages', TEX_PACKAGE)
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)
    tex = os.path.join(pkg, TEX_FOLDER)
    if make_textures.main(['make_textures.py', tex]) != 0:
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
    if not os.path.isfile(a.nrm):
        fail('%s: build the patch first (build.ps1)' % a.nrm)
    shutil.copyfile(a.nrm, os.path.join(conv, 'patch.nrm'))
    # 2. Codes, 3. RT rig, docs and thumbnail.
    shutil.copyfile(os.path.join(KIT, 'codes.txt'), os.path.join(conv, 'codes.txt'))
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
        src = os.path.join(PACKS, pack + '.akimodel')
        if not os.path.isdir(src):
            fail('model pack %s not found in %s' % (pack, PACKS))
        shutil.copytree(src, os.path.join(models, pack + '.akimodel'))
    shutil.copyfile(os.path.join(PACKS, HD_RTZ), os.path.join(conv, 'aki_starter_packs-hd.rtz'))
    write_json(os.path.join(models, 'roster.json'), {
        'format': 1, 'game': GAME,
        'wrestlers': [{'id': s['wrestler'], 'name': s['label']} for s in slots]})

    # 6. Seed save, made from the builder's own blank save (a save file holds ROM-built data: never committed).
    manifest = json.load(open(os.path.join(KIT, 'conversion', 'aki.json'), encoding='utf-8'))
    comps = manifest['components']
    comps['textures']['sha256'] = tex_sha
    comps['models']['slots'] = [{'wrestler': s['wrestler'], 'name': s['label'], 'pack': s['pack']} for s in slots]
    if a.blank_save:
        import seed_save
        with open(a.blank_save, 'rb') as f:
            seeded = seed_save.apply_seed_edit(f.read())
        os.makedirs(os.path.join(conv, 'saves'))
        with open(os.path.join(conv, 'saves', SAVE_NAME), 'wb') as f:
            f.write(seeded)
    else:
        print('warning  no --blank-save: the conversion is built without its seed save (components.saves left out)')
        del comps['saves']
    write_json(os.path.join(conv, 'aki.json'), manifest)
    print('assembled %s\n  texture package %s\n  sha256 %s' % (conv, zip_path, tex_sha))
    return 0


def make_icon(path):
    # A 64x64 thumbnail in the kit's colours (drawn here, CC0).
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (28, 30, 44, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, 57, 57], outline=(40, 190, 180, 255), width=4)
    d.polygon([(32, 14), (37, 27), (51, 27), (40, 36), (44, 50), (32, 42), (20, 50), (24, 36), (13, 27), (27, 27)],
              fill=(125, 75, 205, 255))
    img.save(path, optimize=False)


# --- validate ------------------------------------------------------------------------------------------------------

def cmd_validate(a):
    out = os.path.abspath(a.out)
    conv = os.path.join(out, CONV_ID)
    errors, notes = [], []
    m = json.load(open(os.path.join(conv, 'aki.json'), encoding='utf-8'))
    c = m['components']
    if m['id'] != CONV_ID:
        errors.append('aki.json id %s != folder %s' % (m['id'], CONV_ID))
    for key in ('patch', 'codes'):
        if not os.path.isfile(os.path.join(conv, c[key]['file'])):
            errors.append('%s file %s missing' % (key, c[key]['file']))
    # The patch .nrm's mod id must be the one aki.json names.
    with zipfile.ZipFile(os.path.join(conv, c['patch']['file'])) as z:
        mod_json = json.loads(z.read('mod.json').decode('utf-8'))
    if mod_json.get('id') != c['patch']['mod_id']:
        errors.append('patch.nrm id %s != components.patch.mod_id %s' % (mod_json.get('id'), c['patch']['mod_id']))
    # Codes: every code line is 8 + 4 hex digits; the rom tag is the port's revision.
    codes = [l.strip() for l in open(os.path.join(conv, c['codes']['file']), encoding='utf-8')]
    code_lines = [l for l in codes if l and not l.startswith('#') and not l.startswith('[')]
    bad = [l for l in code_lines if not re.fullmatch(r'[0-9A-Fa-f]{8} [0-9A-Fa-f]{4}', l)]
    if bad:
        errors.append('codes.txt: malformed lines %s' % bad[:3])
    if not any(re.match(r'#\s*rom:\s*nomercy-rev1\s*$', l) for l in codes):
        errors.append('codes.txt: no "# rom: nomercy-rev1" tag')
    notes.append('codes: %d code lines' % len(code_lines))
    # RT rig: JSON with comments, a default and at least one venue.
    rig = json.loads(strip_comments(open(os.path.join(conv, c['rt']['lights']), encoding='utf-8').read()))
    if 'default' not in rig or not rig.get('venues'):
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
        r = subprocess.run([sys.executable, tool, 'package', '--from', conv, '-o', pkg_zip, '--license', 'CC0-1.0',
                            '--base-name', 'WWF No Mercy (USA) (Rev 1)'], capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode == 2:
            errors.append('aki-package package --from refused the conversion (above)')
        else:
            r = subprocess.run([sys.executable, tool, 'validate', pkg_zip], capture_output=True, text=True)
            print(r.stdout.strip())
            if r.returncode == 2:
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
    print('installed %s\n  run it with AKI_CONVERSION=%s and WCW_RT_LIGHTS_FILE=%s' %
          (dst, CONV_ID, os.path.join(dst, m['components']['rt']['lights'])))
    return 0


# --- check a run ---------------------------------------------------------------------------------------------------

def cmd_check(a):
    log = open(os.path.join(a.run, 'stderr.txt'), encoding='utf-8', errors='replace').read()
    slots = load_slots()
    checks = [
        ('conversion starts with every part', r'\[conversion\] running %s \([^)]*\): patch codes saves textures rt models' % CONV_ID),
        ('code change (patch.nrm) loads', r'\[conversion\] mod %s enabled for this session' % PATCH_ID),
        ('texture package loads', r'\[conversion\] mod %s enabled for this session' % TEX_FOLDER),
        ('codes run', r'\[conversion\] codes: [1-9]\d* active'),
        ('RT rig is the conversion\'s', r'\[wcw rt\] light presets loaded from [^\n]*%s[^\n]*: 1 venue' % CONV_ID),
        ('model import on', r'\[conversion\] aki_modelimport: on'),
        ('all slots served', r'\[mod\] models.txt: %d lines' % len(slots)),
    ]
    bad = 0
    for name, rx in checks:
        ok = re.search(rx, log) is not None
        bad += not ok
        print('%s %s' % ('ok      ' if ok else 'MISSING ', name))
    drawn = sorted(set(re.findall(r'\[mod\] slot \d+ \(wrestler ([0-9a-fA-F]{4})\): (q-[a-z]+)', log)))
    print('info     slots the mod reported: %s' % (', '.join('%s=%s' % d for d in drawn) or 'none'))
    if a.game_dir:
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
    s = sub.add_parser('assemble'); s.add_argument('--nrm', required=True); s.add_argument('--out', required=True)
    s.add_argument('--blank-save')
    s = sub.add_parser('validate'); s.add_argument('--out', required=True); s.add_argument('--akitools')
    s = sub.add_parser('install'); s.add_argument('--out', required=True); s.add_argument('--game-dir', required=True)
    s = sub.add_parser('check'); s.add_argument('--run', required=True); s.add_argument('--game-dir')
    a = p.parse_args(argv)
    return {'assemble': cmd_assemble, 'validate': cmd_validate, 'install': cmd_install, 'check': cmd_check}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
