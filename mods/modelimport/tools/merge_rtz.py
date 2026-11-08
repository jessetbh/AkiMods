"""Merges model packs' HD texture packs (<id>-hd.rtz, written by AkiTools aki-model.py convert) into one .rtz, so the
bundled starter packs show up as one texture-pack mod instead of one per pack (aki-roadmap#145).

    python merge_rtz.py OUT.rtz IN1.rtz IN2.rtz ...

Every input must have the same rt64.json configuration (aki-model writes them all alike); texture files that two packs
share by name must be identical (a name is the texture's hash). Stdlib only."""
import json
import sys
import zipfile


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    out_path, inputs = argv[1], argv[2:]
    config = None
    textures = []
    files = {}
    for path in inputs:
        with zipfile.ZipFile(path) as z:
            db = json.loads(z.read('rt64.json'))
            if config is None:
                config = db['configuration']
            elif db['configuration'] != config:
                sys.exit('%s: its rt64.json configuration differs from the first input\'s' % path)
            for name in z.namelist():
                if name == 'rt64.json':
                    continue
                data = z.read(name)
                if name in files and files[name] != data:
                    sys.exit('%s: %s differs from the file of that name in an earlier input' % (path, name))
                files[name] = data
            for entry in db['textures']:
                if entry not in textures:
                    textures.append(entry)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_STORED) as z:
        # Fixed timestamps: the same inputs give the same bytes, so a rebuild doesn't change the bundled file.
        stamp = (2026, 1, 1, 0, 0, 0)
        z.writestr(zipfile.ZipInfo('rt64.json', date_time=stamp),
                   json.dumps({'configuration': config, 'textures': textures}, indent=4))
        for name in sorted(files):
            z.writestr(zipfile.ZipInfo(name, date_time=stamp), files[name])
    print('%s: %d textures from %d packs' % (out_path, len(textures), len(inputs)))


if __name__ == '__main__':
    main(sys.argv)
