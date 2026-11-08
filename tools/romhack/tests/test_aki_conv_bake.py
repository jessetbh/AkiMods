"""Tests for baking GameShark writes on game instructions into a patch (aki-roadmap#185). Synthetic ROM and layout only.
Run: python tests/test_aki_conv_bake.py"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import aki_conv_bake as bake  # noqa: E402

spec = importlib.util.spec_from_file_location('aki_conv', os.path.join(os.path.dirname(HERE), 'aki_conv.py'))
aki_conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aki_conv)

# main at 0x1000; overlays: E covers both slots, A the low slot, B and C the high one (No Mercy's shape).
LOW, HIGH, TOP = 0x80100000, 0x80110000, 0x80120000


def section(name, rom, text, data, end, funcs, slot=None):
    return dict(name=name, rom=rom, text=text, data=data, bss=data, end=end, funcs=funcs, slot=slot)


LAYOUT = {'mod_game_id': 'testgame', 'sections': [
    section('main', 0x1000, 0x80001000, 0x80003000, 0x80004000, [[0, 0x100]]),
    section('ovl_e', 0x10000, LOW, LOW + 0x4000, TOP, [[0, 0x100]], LOW),
    section('ovl_a', 0x20000, LOW, LOW + 0x2000, HIGH, [[0, 0x100]], LOW),
    section('ovl_b', 0x30000, HIGH, HIGH + 0x2000, TOP, [[0, 0x100]], HIGH),
    section('ovl_c', 0x40000, HIGH, HIGH + 0x2000, TOP, [[0, 0x80]], HIGH),
]}


def rom():
    """Every overlay's first halfword names it, so guards can tell them apart."""
    r = bytearray(0x60000)
    for at, mark in ((0x1000, 0x1111), (0x10000, 0xEEEE), (0x20000, 0xAAAA), (0x30000, 0xBBBB), (0x40000, 0xCCCC)):
        r[at:at + 2] = mark.to_bytes(2, 'big')
    return bytes(r)


def run(text, base=None):
    base = base or rom()
    return bake.bake(bake.parse_units(text), LAYOUT, base), base


def word(data, at):
    return int.from_bytes(data[at:at + 2], 'big')


class ParseUnits(unittest.TestCase):
    def test_write_sizes_and_conditions(self):
        units = bake.parse_units('[G]\n80001003 00FF\nD1001000 1111\nD2001002 0012\n81001005 BEEF\n')
        self.assertEqual(units[0], bake.Unit('G', (), [(0x1003, 1, 0xFF)]))
        self.assertEqual(units[1].conditions, ((0x1000, 2, True, 0x1111), (0x1002, 1, False, 0x12)))
        self.assertEqual(units[1].writes, [(0x1004, 2, 0xBEEF)])  # a 16-bit address is even

    def test_conditional_governs_across_groups(self):
        units = bake.parse_units('[One]\nD1001000 1111\n[Two]\n81001004 0001\n')
        self.assertEqual(units[0].conditions, ((0x1000, 2, True, 0x1111),))
        self.assertEqual(units[0].group, 'Two')

    def test_repeater(self):
        units = bake.parse_units('50000202 0001\n81001000 0005\n')
        self.assertEqual(units[0].writes, [(0x1000, 2, 5), (0x1002, 2, 6)])

    def test_past_ram(self):
        self.assertEqual(bake.parse_units('81900000 0001\nD1900000 0000\n81001000 0001\n'), [])

    def test_other_kinds_ignored(self):
        self.assertEqual(bake.parse_units('# rom: x\nF1000000 0001\nDE000000 0000\nnot a code\n'), [])


class Worlds(unittest.TestCase):
    def test_overlays_that_fit_together(self):
        self.assertEqual(sorted(bake.worlds_of(LAYOUT)), [['ovl_a', 'ovl_b'], ['ovl_a', 'ovl_c'], ['ovl_e']])


class Bake(unittest.TestCase):
    def test_unconditional_goes_into_every_overlay_it_lands_on(self):
        r, base = run('81100010 C250\n')
        self.assertEqual(word(r.patched, 0x10010), 0xC250)
        self.assertEqual(word(r.patched, 0x20010), 0xC250)
        self.assertEqual(r.gaps, {})
        self.assertEqual(sorted(r.functions), ['ovl_a', 'ovl_e'])
        self.assertEqual(word(base, 0x10010), 0)  # the input is untouched

    def test_write_outside_functions_is_not_baked(self):
        r, base = run('81100200 C250\n')  # text, but no function there
        self.assertEqual(r.patched, base)
        self.assertEqual(r.code_writes, 0)

    def test_overlay_guard_picks_the_overlay(self):
        r, _ = run('D1100000 EEEE\n81100010 C250\n')
        self.assertEqual(word(r.patched, 0x10010), 0xC250)
        self.assertEqual(word(r.patched, 0x20010), 0)
        self.assertEqual(r.gaps, {})

    def test_negative_guard(self):
        r, _ = run('D3100000 EEEE\n81100010 C250\n')
        self.assertEqual(word(r.patched, 0x10010), 0)
        self.assertEqual(word(r.patched, 0x20010), 0xC250)

    def test_game_state_condition_is_a_gap(self):
        r, base = run('[Pin]\nD1159A30 0003\n81100010 C250\n')
        self.assertEqual(r.patched, base)
        self.assertEqual(r.gaps, {'condition on game state': {'Pin': 1}})

    def test_overlay_guard_plus_game_state_is_a_gap(self):
        r, base = run('[Pin]\nD1100000 EEEE\nD1159A30 0003\n81100010 C250\n')
        self.assertEqual(r.patched, base)
        self.assertEqual(r.gaps, {'condition on game state': {'Pin': 1}})

    def test_main_code_written_only_with_one_overlay_loaded(self):
        r, base = run('[M]\nD1100000 EEEE\n81001010 C250\n')
        self.assertEqual(r.patched, base)
        self.assertEqual(r.gaps, {'differs between overlays': {'M': 1}})

    def test_guard_false_on_the_overlay_it_lands_on(self):
        # A function only in ovl_e, guarded on ovl_a: in ovl_a's world the address is data, and that runs as a code.
        r, base = run('[D]\nD1100000 AAAA\n81103000 0001\n')
        self.assertEqual(r.patched, base)
        self.assertEqual(r.code_writes, 0)

    # Pass 1 ends with both set, pass 2 with both clear: the game sees them alternate, frame by frame.
    FLIP = 'D1100010 0000\n81100020 0001\nD1100010 0001\n81100020 0000\nD1100020 0001\n81100010 0001\nD1100020 0000\n81100010 0000\n'

    def test_a_list_that_never_settles(self):
        r, _ = run('[Flip]\n' + self.FLIP)
        self.assertEqual(word(r.patched, 0x10010), 0)
        self.assertEqual(word(r.patched, 0x10020), 0)
        self.assertEqual(r.gaps, {'never settles': {'Flip': 4}})
        self.assertTrue(r.unsettled)

    def test_unsettled_bytes_dont_stop_the_others(self):
        r, _ = run(self.FLIP + '81100030 C250\n')
        self.assertEqual(word(r.patched, 0x10030), 0xC250)

    def test_a_flip_undone_in_the_same_pass_is_what_the_game_sees(self):
        # The game runs between passes, so the byte it sees is always 0.
        r, base = run('D1100010 0000\n81100010 0001\nD1100010 0001\n81100010 0000\n')
        self.assertEqual(r.patched, base)
        self.assertEqual(r.gaps, {})

    def test_guard_chain_settles_over_passes(self):
        # The last write enables the one before it, and so on: one step per pass, as on hardware.
        text = 'D1100020 0002\n81100030 0003\nD1100010 0001\n81100020 0002\n81100010 0001\n'
        r, _ = run(text)
        self.assertEqual(word(r.patched, 0x10030), 3)

    def test_guard_reads_what_a_code_wrote(self):
        r, _ = run('81100010 0007\nD1100010 0007\n81100020 0008\n')
        self.assertEqual(word(r.patched, 0x10020), 8)

    def test_a_guard_baked_away_is_reported(self):
        # A data write behind a guard a later code changes: on hardware it fires once, baked it never does.
        r, _ = run('[G]\nD1100000 EEEE\n80300000 0001\n81100000 1234\n')
        self.assertEqual(r.guard_flips, {'G': 1})

    def test_last_write_wins(self):
        r, _ = run('81100010 0001\n81100010 0002\n')
        self.assertEqual(word(r.patched, 0x10010), 2)

    def test_byte_write(self):
        r, _ = run('80100011 00AB\n')
        self.assertEqual(r.patched[0x10011], 0xAB)
        self.assertEqual(r.baked_bytes, 2)  # ovl_e and ovl_a

    def test_a_jump_to_code_the_list_writes_stays_out(self):
        # j 0x80400008, and a code puts something there: but not before the list first runs, and the hook is live from boot.
        r, _ = run('[Hook]\nD1100000 EEEE\n81100010 0810\n81100012 0002\n[Hook]\n81400008 1234\n')
        self.assertEqual(word(r.patched, 0x10010), 0)
        self.assertEqual(r.ram_jumps, [])
        self.assertEqual(list(r.gaps), [bake.NO_TARGET])

    def test_a_jump_into_text_needs_nothing_written(self):
        r, _ = run('D1100000 EEEE\n81100010 0C04\n81100012 0100\n')  # jal 0x80100400: text, no known function
        self.assertEqual(r.ram_jumps, [('ovl_e', 0x100010, 0x100400)])
        self.assertEqual(word(r.patched, 0x10010), 0x0C04)
        self.assertEqual(r.gaps, {})

    def test_a_jump_to_code_nobody_provides_leaves_its_whole_group_out(self):
        text = '[Hook]\n81100010 0810\n81100012 0002\n81100020 0001\n[Other]\n81100030 0002\n'
        r, _ = run(text)
        self.assertEqual(word(r.patched, 0x10010), 0)
        self.assertEqual(word(r.patched, 0x10020), 0)  # its neighbour in the group too
        self.assertEqual(word(r.patched, 0x10030), 2)
        self.assertEqual(r.ram_jumps, [])
        self.assertEqual(r.gaps, {bake.NO_TARGET: {'Hook': 3}})

    def test_a_baked_jump_to_a_known_function_is_not(self):
        r, _ = run('D1100000 EEEE\n81100010 0C04\n81100012 0000\n')  # jal 0x80100000, ovl_e's first function
        self.assertEqual(word(r.patched, 0x10010), 0x0C04)
        self.assertEqual(r.ram_jumps, [])

    def test_a_function_the_recompiler_would_refuse_stays_stock(self):
        # The same address in two overlays: ovl_a holds sw ra,0x18(sp) there, and the write makes its offset odd.
        base = bytearray(rom())
        base[0x20010:0x20014] = bytes.fromhex('AFBF0018')
        r, _ = run('[W]\n81100012 0019\n', bytes(base))
        self.assertEqual(r.patched[0x20010:0x20014], bytes.fromhex('AFBF0018'))
        self.assertEqual(word(r.patched, 0x10012), 0x0019)  # ovl_e's copy is fine
        self.assertEqual(r.gaps, {bake.NO_COMPILE: {'W': 1}})
        self.assertEqual(list(r.functions), ['ovl_e'])

    def test_recompiler_refuses(self):
        self.assertTrue(bake.recompiler_refuses(0xAFBF0019))   # sw ra,0x19(sp)
        self.assertTrue(bake.recompiler_refuses(0x8FBFFFF8))   # lw ra,-8(sp)
        self.assertTrue(bake.recompiler_refuses(0x44C1F800 & ~0xF800 | 5 << 11))   # ctc1 at,$5
        self.assertFalse(bake.recompiler_refuses(0xAFBF0018))  # sw ra,0x18(sp)
        self.assertFalse(bake.recompiler_refuses(0xAC820019))  # not the stack: an unaligned offset is its own business
        self.assertFalse(bake.recompiler_refuses(0x44C1F800))  # ctc1 at,$31

    def test_on_a_hack_keeps_the_hacks_bytes(self):
        hack = bytearray(rom())
        hack[0x10040:0x10042] = b'\x12\x34'
        hack[0x50000] = 0x77  # data the hack changed
        r, _ = run('81100010 C250\n', bytes(hack))
        self.assertEqual(word(r.patched, 0x10040), 0x1234)
        self.assertEqual(r.patched[0x50000], 0x77)
        self.assertEqual(word(r.patched, 0x10010), 0xC250)

    def test_a_write_the_hack_already_has_changes_nothing(self):
        hack = bytearray(rom())
        hack[0x10010:0x10012] = b'\xC2\x50'
        hack[0x20010:0x20012] = b'\xC2\x50'
        r, base = run('81100010 C250\n', bytes(hack))
        self.assertEqual(r.patched, base)
        self.assertEqual(r.baked_bytes, 0)


FAKE_BPS = '''
def bps_encode(source, target, metadata=b'', max_target_read=None, stats=None):
    return b'FAKE' + bytes(target)
def bps_apply(source, patch):
    assert patch[:4] == b'FAKE'
    return patch[4:]
'''


class Command(unittest.TestCase):
    """The pack folder end to end, with a stand-in for bps.py (the real one is the game repo's, with its own tests)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        tools = os.path.join(self.tmp, 'romhack')
        os.makedirs(os.path.join(tools, 'layouts'))
        self.derelink = os.path.join(tools, 'derelink.py')
        open(self.derelink, 'w').close()
        with open(os.path.join(tools, 'bps.py'), 'w') as f:
            f.write(FAKE_BPS)
        self.rom = os.path.join(self.tmp, 'game.z64')
        with open(self.rom, 'wb') as f:
            f.write(rom())
        import hashlib
        layout = dict(LAYOUT, base_sha1=hashlib.sha1(rom()).hexdigest())
        with open(os.path.join(tools, 'layouts', 'test.json'), 'w') as f:
            json.dump(layout, f)
        self.pack = os.path.join(self.tmp, 'pack')
        os.makedirs(self.pack)
        sys.modules.pop('bps', None)
        self.addCleanup(sys.modules.pop, 'bps', None)
        self.addCleanup(lambda: sys.path.remove(tools) if tools in sys.path else None)

    def write_pack(self, codes, components=None):
        comp = dict({'codes': {'file': 'codes.txt', 'format': 'aki-codes', 'rom': 'nomercy-rev1'}}, **(components or {}))
        with open(os.path.join(self.pack, 'aki.json'), 'w') as f:
            json.dump({'id': 'mypack', 'name': 'My Pack', 'game': 'nomercy', 'components': comp, 'survey': {'tier': 'x'}}, f)
        with open(os.path.join(self.pack, 'codes.txt'), 'w') as f:
            f.write(codes)

    def run_command(self, **extra):
        args = dict(folder=self.pack, port_rom=self.rom, derelink=self.derelink, report=None, dry_run=False, exclude=[])
        args.update(extra)
        stdout, sys.stdout = sys.stdout, open(os.devnull, 'w')
        try:
            aki_conv.cmd_bake_codes(argparse.Namespace(**args))
        finally:
            sys.stdout.close()
            sys.stdout = stdout

    def aki(self):
        return json.load(open(os.path.join(self.pack, 'aki.json')))

    def test_a_pack_with_no_patch_gets_one_under_the_conversions_id(self):
        self.write_pack('81100010 C250\n')
        self.run_command()
        with zipfile.ZipFile(os.path.join(self.pack, 'patch.nrm')) as z:
            mod = json.loads(z.read('mod.json'))
            patched = z.read('patch.bps')[4:]
        self.assertEqual((mod['id'], mod['game_id']), ('mypack', 'testgame'))
        self.assertRegex(mod['version'], r'^\d+\.\d+\.\d+$')  # the runtime refuses anything else
        self.assertEqual(word(patched, 0x10010), 0xC250)
        aki = self.aki()
        self.assertEqual(aki['components']['patch'], {'file': 'patch.nrm', 'mod_id': 'mypack'})
        self.assertEqual(aki['survey']['tier'], 'x')
        self.assertEqual(aki['survey']['code_bake']['bytes_baked'], 4)
        self.assertEqual(open(os.path.join(self.pack, 'codes.txt')).read(), '81100010 C250\n')  # the codes stay

    def test_a_jump_into_ram_code_turns_on_the_runtimes_ram_compile(self):
        self.write_pack('D1100000 EEEE\n81100010 0C04\n81100012 0100\n')
        self.run_command()
        with zipfile.ZipFile(os.path.join(self.pack, 'patch.nrm')) as z:
            self.assertEqual(json.loads(z.read('relink.json'))['format'], 1)

    def test_no_such_jump_no_relink_json(self):
        self.write_pack('81100010 C250\n')
        self.run_command()
        with zipfile.ZipFile(os.path.join(self.pack, 'patch.nrm')) as z:
            self.assertNotIn('relink.json', z.namelist())

    def test_a_pack_with_a_patch_keeps_the_rest_of_its_nrm(self):
        hack = bytearray(rom())
        hack[0x50000] = 0x77
        with zipfile.ZipFile(os.path.join(self.pack, 'patch.nrm'), 'w') as z:
            z.writestr('mod.json', json.dumps({'id': 'mypack_patch'}))
            z.writestr('relink.json', '{"format": 1}')
            z.writestr('patch.bps', b'FAKE' + bytes(hack))
        self.write_pack('81100010 C250\n', {'patch': {'file': 'patch.nrm', 'mod_id': 'mypack_patch'}})
        self.run_command()
        with zipfile.ZipFile(os.path.join(self.pack, 'patch.nrm')) as z:
            self.assertEqual(json.loads(z.read('mod.json'))['id'], 'mypack_patch')
            self.assertEqual(z.read('relink.json'), b'{"format": 1}')
            patched = z.read('patch.bps')[4:]
        self.assertEqual(patched[0x50000], 0x77)
        self.assertEqual(word(patched, 0x10010), 0xC250)
        self.assertEqual(self.aki()['components']['patch']['mod_id'], 'mypack_patch')

    def test_exclude_leaves_a_group_out(self):
        self.write_pack('[Keep]\n81100010 0001\n[Leave\\Out]\n81100020 0002\n')
        self.run_command(exclude=['Out'])
        with zipfile.ZipFile(os.path.join(self.pack, 'patch.nrm')) as z:
            patched = z.read('patch.bps')[4:]
        self.assertEqual((word(patched, 0x10010), word(patched, 0x10020)), (1, 0))
        self.assertEqual(self.aki()['survey']['code_bake']['excluded'], ['Out'])  # a rebuild needs the same choice

    def test_rerun_changes_nothing(self):
        self.write_pack('81100010 C250\n')
        self.run_command()
        before = open(os.path.join(self.pack, 'patch.nrm'), 'rb').read()
        self.run_command()
        self.assertEqual(open(os.path.join(self.pack, 'patch.nrm'), 'rb').read(), before)

    def test_nothing_to_bake_adds_no_patch(self):
        self.write_pack('[Data]\n80300000 0001\n')
        self.run_command()
        self.assertFalse(os.path.exists(os.path.join(self.pack, 'patch.nrm')))
        self.assertNotIn('patch', self.aki()['components'])

    def test_dry_run_writes_only_the_report(self):
        self.write_pack('[Pin]\nD1159A30 0003\n81100010 C250\n81100020 C250\n')
        report = os.path.join(self.tmp, 'report.json')
        self.run_command(dry_run=True, report=report)
        self.assertFalse(os.path.exists(os.path.join(self.pack, 'patch.nrm')))
        self.assertEqual(json.load(open(report))['not_baked_groups'], {'condition on game state': {'Pin': 1}})

    def test_codes_for_another_revision_are_refused(self):
        self.write_pack('81100010 C250\n', {'codes': {'file': 'codes.txt', 'rom': 'nomercy-rev0'}})
        with self.assertRaises(SystemExit):
            self.run_command()

    def test_a_rom_with_no_layout_is_refused(self):
        self.write_pack('81100010 C250\n')
        with open(self.rom, 'ab') as f:
            f.write(b'x')
        with self.assertRaises(SystemExit):
            self.run_command()

    def test_repaired_function_named(self):
        funcs = os.path.join(self.tmp, 'RecompiledFuncs')
        os.makedirs(funcs)
        with open(os.path.join(funcs, 'repaired_functions.txt'), 'w') as f:
            f.write('# a comment\nfunc_80100000\nfunc_80110000_ovl_c\nstatic_5_80001000\n')
        self.write_pack('81100010 C250\n')
        report = os.path.join(self.tmp, 'report.json')
        self.run_command(report=report)
        self.assertEqual(json.load(open(report))['may_drop_repair'], ['ovl_a:80100000', 'ovl_e:80100000'])


if __name__ == '__main__':
    unittest.main(warnings='ignore')
