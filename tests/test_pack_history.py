"""Tests for mods/modelimport/tools/pack_history.py (aki-roadmap#149): the pack hash must match librecomp's
recomp::mods::bundled_packs::pack_hash (the shared vector below is also in librecomp's bundled_packs_tests.cpp), and
`update` records an earlier bundle's hash only for packs that changed. Stdlib only.

    python -m unittest discover -s tests
"""
import json, os, shutil, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'mods', 'modelimport', 'tools'))
import pack_history  # noqa: E402


def write_pack(root, pid, files):
    d = os.path.join(root, pid + '.akimodel')
    os.makedirs(d)
    for name, data in files.items():
        with open(os.path.join(d, name), 'wb') as f:
            f.write(data)
    return d


class PackHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_shared_vector_with_librecomp(self):
        h = pack_history.pack_hash_files([('pack.json', b'{\r\n  "id": "x"\r\n}\r\n'), ('model.bin', b'\x00\x01\r\n')])
        self.assertEqual('%016x' % h, '974f489db7b79f83')

    def test_line_endings_and_order_dont_matter_for_text_only(self):
        crlf = pack_history.pack_hash_files([('pack.json', b'{\r\n}\r\n'), ('model.bin', b'\x01\r\n')])
        lf = pack_history.pack_hash_files([('model.bin', b'\x01\r\n'), ('pack.json', b'{\n}\n')])
        self.assertEqual(crlf, lf)
        self.assertNotEqual(lf, pack_history.pack_hash_files([('model.bin', b'\x01\n'), ('pack.json', b'{\n}\n')]))

    def test_folder_hash_includes_subfolders(self):
        d = write_pack(self.tmp, 'a', {'pack.json': b'{}\n'})
        h1 = pack_history.pack_hash(d)
        os.makedirs(os.path.join(d, 'sub'))
        with open(os.path.join(d, 'sub', 'x.bin'), 'wb') as f:
            f.write(b'\x02')
        self.assertNotEqual(h1, pack_history.pack_hash(d))
        self.assertEqual(pack_history.pack_hash(d), pack_history.pack_hash_files([('pack.json', b'{}\n'), ('sub/x.bin', b'\x02')]))

    def test_update_records_only_changed_packs_once(self):
        old, cur = os.path.join(self.tmp, 'old'), os.path.join(self.tmp, 'cur')
        os.makedirs(old)
        os.makedirs(cur)
        a_old = write_pack(old, 'a', {'pack.json': b'{"version": "1.0.0"}\n', 'model.bin': b'\x01'})
        write_pack(old, 'b', {'pack.json': b'{"version": "1.0.0"}\n', 'model.bin': b'\x02'})
        write_pack(cur, 'a', {'pack.json': b'{"version": "1.1.0"}\n', 'model.bin': b'\x03'})
        write_pack(cur, 'b', {'pack.json': b'{"version": "1.0.0"}\n', 'model.bin': b'\x02'})   # unchanged
        pack_history.update(cur, old)
        pack_history.update(cur, old)   # again: nothing new
        with open(os.path.join(cur, pack_history.HISTORY_FILE), encoding='utf-8') as f:
            hist = json.load(f)
        self.assertEqual(hist['previous'], {'a': ['%016x' % pack_history.pack_hash(a_old)]})


if __name__ == '__main__':
    unittest.main()
