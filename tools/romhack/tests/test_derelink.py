"""Tests for the de-relink tool on synthetic MIPS snippets (no ROMs needed).

    python -m unittest discover -s tools/romhack/tests -v
"""
import json, os, struct, sys, types, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import bps
import derelink as d
import mips


# --- tiny assembler --------------------------------------------------------------------------
def lui(rt, imm): return (0x0F << 26) | (rt << 16) | (imm & 0xFFFF)
def addiu(rt, rs, imm): return (0x09 << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
def ori(rt, rs, imm): return (0x0D << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
def lw(rt, imm, rs): return (0x23 << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
def sw(rt, imm, rs): return (0x2B << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
def addu(rd, rs, rt): return (rs << 21) | (rt << 16) | (rd << 11) | 0x21
def jal(t): return (3 << 26) | ((t >> 2) & 0x3FFFFFF)
def j(t): return (2 << 26) | ((t >> 2) & 0x3FFFFFF)
def jr(rs): return (rs << 21) | 0x08
def beq(rs, rt, off): return (0x04 << 26) | (rs << 21) | (rt << 16) | (off & 0xFFFF)
NOP = 0
AT, V0, V1, A0, A1, A2, T0, S0, RA = 1, 2, 3, 4, 5, 6, 8, 16, 31


def hi_lo(addr):
    return ((addr + 0x8000) >> 16) & 0xFFFF, addr & 0xFFFF


class HiLoTests(unittest.TestCase):
    def test_simple_pair(self):
        h, l = hi_lo(0x80123456)
        refs = mips.hilo_refs([lui(A0, h), addiu(A0, A0, l)])
        self.assertEqual(refs, [(0, 1, 0x80123456)])

    def test_negative_lo(self):
        h, l = hi_lo(0x8012F000)
        self.assertEqual(mips.hilo_refs([lui(AT, h), lw(V0, l, AT)])[0][2], 0x8012F000)

    def test_one_lui_many_uses(self):
        h, _ = hi_lo(0x80050000)
        refs = mips.hilo_refs([lui(S0, h), lw(V0, 0x10, S0), sw(V1, 0x20, S0)])
        self.assertEqual([a for _, _, a in refs], [0x80050010, 0x80050020])

    def test_addu_index_keeps_hi(self):
        h, l = hi_lo(0x80140000)
        refs = mips.hilo_refs([lui(AT, h), addu(AT, AT, V0), lw(V0, l, AT)])
        self.assertEqual(refs, [(0, 2, 0x80140000)])

    def test_add_index_keeps_hi(self):
        # hand-written payload code indexes with `add`, not `addu` (No Mercy Plus V3, aki-roadmap#137)
        for funct in (0x20, 0x2C):   # add, dadd
            add = (AT << 21) | (V0 << 16) | (AT << 11) | funct
            h, l = hi_lo(0x80090FA0)
            refs = mips.hilo_refs([lui(AT, h), add, addiu(AT, AT, l)])
            self.assertEqual(refs, [(0, 2, 0x80090FA0)])

    def test_add_index_then_conditional_step_is_not_the_lo(self):
        # lui; add; beq over; nop; addiu at, at, 2; over: lw v0, lo(at) (BTBW's payload, aki-roadmap#137)
        add = (AT << 21) | (V0 << 16) | (AT << 11) | 0x20
        h, l = hi_lo(0x8009102C)
        words = [lui(AT, h), add, beq(V1, 0, 2), NOP, addiu(AT, AT, 2), lw(V0, l, AT)]
        self.assertEqual(mips.hilo_refs(words), [(0, 5, 0x8009102C)])

    def test_add_index_then_lo_with_no_branch_is_the_lo(self):
        add = (AT << 21) | (V0 << 16) | (AT << 11) | 0x20
        h, l = hi_lo(0x80090FA0)
        self.assertEqual(mips.hilo_refs([lui(AT, h), add, addiu(AT, AT, l)]), [(0, 2, 0x80090FA0)])

    def test_addu_index_then_branch_keeps_its_old_reading(self):
        # compiled code is unchanged: past a branch, addiu on an addu-indexed lui is still taken as the %lo
        h, l = hi_lo(0x80140000)
        refs = mips.hilo_refs([lui(AT, h), addu(AT, AT, V0), beq(V1, 0, 2), NOP, addiu(AT, AT, l)])
        self.assertEqual(refs, [(0, 4, 0x80140000)])

    def test_jal_delay_slot_still_sees_hi(self):
        # lui a0; jal f; addiu a0, a0, lo  (the argument is formed in the delay slot)
        h, l = hi_lo(0x80057188)
        refs = mips.hilo_refs([lui(A0, h), jal(0x80000DB0), addiu(A0, A0, l)])
        self.assertEqual(refs, [(0, 2, 0x80057188)])

    def test_call_clobbers_after_delay_slot(self):
        h, l = hi_lo(0x80057188)
        refs = mips.hilo_refs([lui(A0, h), jal(0x80000DB0), NOP, addiu(A0, A0, l)])
        self.assertEqual(refs, [])

    def test_hook_return_forgets_what_the_hook_writes(self):
        # a relinked hack (aki-roadmap#239, synthetic words): `lui s3,0x8010; j hook` where the stock code had
        # `lw s3,0x2A40(s3)`; the hook does that load and jumps back. The later `addiu s2,s2,0x24` / `addiu s3,s3,0x60`
        # are strides through the loaded pointer, not %lo halves of 0x80100024 / 0x80100060.
        S2, S3, pc = 18, 19, 0x80002000
        words = [lui(S3, 0x8010), j(0x80400200), NOP, addu(S2, S3, 0), addiu(S2, S2, 0x24), addiu(S3, S3, 0x60)]
        hook = [lw(S3, 0x2A40, S3), j(pc + 12), NOP]
        writes = {pc + 12: mips.regs_written(hook)}
        self.assertEqual(writes[pc + 12], {S3})
        self.assertEqual(mips.hilo_refs(words, base_pc=pc, return_writes=writes), [])
        self.assertEqual(len(mips.hilo_refs(words, base_pc=pc)), 2)     # the old reading, without the hook's writes

    def test_hook_return_keeps_what_the_hook_leaves(self):
        # `j hook; lui at,%hi` (the lui in the jump's delay slot) and `ldc1 f2,%lo(at)` after the return: the hook
        # doesn't touch at, so the pair stays (the case a forget-everything rule got wrong)
        h, l = hi_lo(0x80123450)
        pc = 0x80001000
        ldc1 = (0x35 << 26) | (AT << 21) | (2 << 16) | l
        words = [j(0x80400300), lui(AT, h), ldc1]
        writes = {pc + 8: mips.regs_written([lw(V0, 0x10, V1), j(pc + 8), NOP])}
        self.assertEqual(mips.hilo_refs(words, base_pc=pc, return_writes=writes), [(1, 2, 0x80123450)])

    def test_return_point_reached_by_fall_through_keeps_pair(self):
        # `lui v0,0x0003; addiu v0,v0,lo` runs straight through, and a hook from elsewhere also
        # comes back at the addiu: the fall-through path's pair stays
        h, l = hi_lo(0x00031A80)
        pc = 0x80001000
        words = [NOP, lui(V0, h), addiu(V0, V0, l)]
        writes = {pc + 8: {V0}}
        self.assertEqual(mips.hilo_refs(words, base_pc=pc, return_writes=writes), [(1, 2, 0x00031A80)])

    def test_regs_written_keeps_stack_saved_registers(self):
        # a hook that saves v0/ra, calls something and restores them changes neither
        SP = 29
        hook = [addiu(SP, SP, -0x18), sw(V0, 0x10, SP), sw(RA, 0x14, SP), jal(0x80001000), NOP,
                lw(V0, 0x10, SP), lw(RA, 0x14, SP), addiu(SP, SP, 0x18)]
        got = mips.regs_written(hook)
        self.assertNotIn(V0, got)
        self.assertNotIn(RA, got)
        self.assertNotIn(SP, got)
        self.assertIn(A0, got)            # a call clobber it doesn't put back

    def test_regs_written_slot_reuse_and_sp_moves(self):
        # a save slot overwritten, a changed value saved back, or sp moved: the load gives something else (review notes)
        SP, S4, T0 = 29, 20, 8
        self.assertIn(S4, mips.regs_written([sw(S4, 0x10, SP), lw(T0, 0, A0), sw(T0, 0x10, SP), lw(S4, 0x10, SP)]))
        self.assertIn(S4, mips.regs_written([sw(S4, 0x10, SP), addiu(S4, S4, 4), sw(S4, 0x10, SP), lw(S4, 0x10, SP)]))
        self.assertIn(S4, mips.regs_written([sw(S4, 0x10, SP), addiu(SP, SP, -8), lw(S4, 0x10, SP)]))

    def test_regs_written_counts_a_calls_clobbers(self):
        self.assertTrue({V0, A0, RA} <= mips.regs_written([jal(0x80001000), NOP]))

    def test_overwrite_drops_hi(self):
        h, l = hi_lo(0x80057188)
        self.assertEqual(mips.hilo_refs([lui(A0, h), addiu(A0, 0, 5), addiu(A1, A0, l)]), [])

    def test_increment_after_base_use_is_not_a_lo(self):
        # lui v1, 0x8017; loop: sh a1, 0(v1); addiu v1, v1, 2 (a fill loop, not %hi/%lo)
        sh = (0x29 << 26) | (V1 << 21) | (A1 << 16)
        refs = mips.hilo_refs([lui(V1, 0x8017), sh, addiu(V1, V1, 2)])
        self.assertEqual([(lo, a) for hi, lo, a in refs], [(1, 0x80170000)])
        # ...while the classic in-place pair still is one
        self.assertEqual([a for hi, lo, a in mips.hilo_refs([lui(V1, 0x8017), addiu(V1, V1, 0x10)])], [0x80170010])

    def test_branch_target_after_a_b_gets_the_branch_state(self):
        # aki-roadmap#120, 9 Underground func_80091100: lui a2; addu a2, a2, s0; beq -> L; the fall-through writes a2
        # and ends in a b; at L, lh a2, %lo(a2) pairs with the lui before the branch
        h, l = hi_lo(0x800E62C4)
        lh = lambda rt, imm, rs: (0x21 << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
        A2 = 6
        words = [lui(A2, h), addu(A2, A2, S0), beq(A0, 0, 4), NOP,     # -> index 7
                 lw(A2, 0, S0), beq(0, 0, 9), NOP,                     # fall-through clobbers a2, then b away
                 lh(A2, l, A2)]
        self.assertEqual([(hi, lo, a) for hi, lo, a in mips.hilo_refs(words)], [(0, 7, 0x800E62C4)])
        # with a live fall-through that wrote a2, nothing pairs (unchanged behaviour)
        live = words[:5] + [NOP, NOP] + words[7:]
        self.assertEqual(mips.hilo_refs(live), [])

    def test_ori_is_unsigned(self):
        refs = mips.hilo_refs([lui(T0, 0x8040), ori(T0, T0, 0x9000)])
        self.assertEqual(refs[0][2], 0x80409000)


def shift_map(ranges):
    """candidates() for a piecewise map: [(lo, hi, delta)]."""
    def cands(a):
        for lo, hi, dl in ranges:
            if lo <= a < hi:
                return [(a + dl) & 0xFFFFFFFF]
        return []
    return cands


class RewriteTests(unittest.TestCase):
    def test_moved_function_becomes_stock(self):
        # stock references 0x80050000 and calls 0x80001000; the hack moved data +0xA0, code +0x90
        hh, hl = hi_lo(0x800500A0)
        sh, sl = hi_lo(0x80050000)
        hack = [lui(AT, hh), lw(V0, hl, AT), jal(0x80001090), NOP, jr(RA), NOP]
        stock = [lui(AT, sh), lw(V0, sl, AT), jal(0x80001000), NOP, jr(RA), NOP]
        cands = shift_map([(0x80040000, 0x80060000, -0xA0), (0x80000400, 0x80040000, -0x90)])
        probs = []
        out = d.rewrite(hack, 0x80002090, 0x80002000, cands, stock, probs)
        self.assertEqual(out, stock)
        self.assertEqual(probs, [])

    def test_real_change_is_kept(self):
        hh, hl = hi_lo(0x800500A0)
        hack = [lui(AT, hh), lw(V0, hl, AT), addiu(V0, V0, 0x60), jr(RA), NOP]   # 0x39 -> 0x60 style edit
        stock = [lui(AT, hh), lw(V0, hl - 0xA0, AT), addiu(V0, V0, 0x39), jr(RA), NOP]
        out = d.rewrite(hack, 0x80002000, 0x80002000, shift_map([(0x80040000, 0x80060000, -0xA0)]), stock, [])
        self.assertEqual(out[2], addiu(V0, V0, 0x60))
        self.assertEqual(out[1], stock[1])

    def test_rewrite_crosses_64k_boundary(self):
        # hack 0x80060010 -> stock 0x8005FF70: the %hi half changes too
        hh, hl = hi_lo(0x80060010)
        out = d.rewrite([lui(AT, hh), lw(V0, hl, AT)], 0x80001000, 0x80001000,
                        shift_map([(0x80040000, 0x80070000, -0xA0)]), None, [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x8005FF70)

    def test_jump_inside_function_follows_placement(self):
        hack = [beq(0, 0, 1), NOP, j(0x80003008), NOP]
        out = d.rewrite(hack, 0x80003000, 0x80007000, shift_map([]), None, [])
        self.assertEqual(mips.jump_target(out[2], 0x80007008), 0x80007008)

    def test_unresolved_is_reported(self):
        probs = []
        d.rewrite([jal(0x80300000), NOP], 0x80001000, 0x80001000, shift_map([]), None, probs)
        self.assertEqual(probs, [(0, 'jal', 0x80300000)])

    def test_candidate_matching_stock_wins(self):
        # two plausible overlays (-0x1500 / -0x1530): the one that reproduces the stock word wins
        def cands(a):
            return [(a - 0x1500) & 0xFFFFFFFF, (a - 0x1530) & 0xFFFFFFFF]
        hack = [jal(0x8012F308), NOP]
        stock = [jal(0x8012F308 - 0x1530), NOP]
        self.assertEqual(d.rewrite(hack, 0x80001000, 0x80001000, cands, stock, []), stock)

    def biased_cands(self):
        # BTBW's runs: data at -0x1530 up to 0x80152380, then -0x1520 (the table lies in the second run)
        m = d.AddressMap()
        m.ovl = {'ovl_e': [(0x80145BA0, 0x80152380, (-0x1530) & 0xFFFFFFFF, 'data'),
                           (0x80152380, 0x8016CF10, (-0x1520) & 0xFFFFFFFF, 'data')]}
        m.finish()
        cands = lambda a: m.candidates(a, 'ovl_e')
        cands.near_shift = lambda a, shift: m.near_data_shift(a, shift, 'ovl_e')
        return cands

    def test_biased_table_base_takes_the_stock_address(self):
        # aki-roadmap#177: `table - 0x4C5 * 4` lies below the run boundary, the table above it. The run at the base's
        # own address (-0x1530) leaves every lookup 4 entries off; the stock code has the right value (-0x1520).
        hh, hl = hi_lo(0x8015220C)
        sh, sl = hi_lo(0x80150CEC)
        hack = [lui(V0, hh), addiu(V0, V0, hl), addu(A1, A1, V0)]
        stock = [lui(V0, sh), addiu(V0, V0, sl), addu(A1, A1, V0)]
        self.assertEqual(d.rewrite(hack, 0x800DA630, 0x800DA630, self.biased_cands(), stock, []), stock)

    def test_repoint_to_another_variable_keeps_the_map(self):
        # The hack points the same instructions at another variable: the stock value's shift (-0x2200) is no data run.
        hh, hl = hi_lo(0x8015220C)
        sh, sl = hi_lo(0x8015220C - 0x2200)
        hack = [lui(V0, hh), addiu(V0, V0, hl), addu(A1, A1, V0)]
        stock = [lui(V0, sh), addiu(V0, V0, sl), addu(A1, A1, V0)]
        out = d.rewrite(hack, 0x800DA630, 0x800DA630, self.biased_cands(), stock, [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x8015220C - 0x1530)

    def test_biased_base_needs_the_same_instructions(self):
        # Same shift, but the stock code at this spot uses another register: not the same reference.
        hh, hl = hi_lo(0x8015220C)
        sh, sl = hi_lo(0x80150CEC)
        hack = [lui(V0, hh), addiu(V0, V0, hl)]
        stock = [lui(V1, sh), addiu(V1, V1, sl)]
        out = d.rewrite(hack, 0x800DA630, 0x800DA630, self.biased_cands(), stock, [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x8015220C - 0x1530)

    def test_near_data_shift_needs_a_nearby_data_run(self):
        m = d.AddressMap()
        m.main = [(0x80040000, 0x80050000, (-0xA0) & 0xFFFFFFFF, 'text'), (0x80060000, 0x80070000, (-0x28) & 0xFFFFFFFF, 'data')]
        m.ovl = {'ovl_e': [(0x80150000, 0x80160000, (-0x1520) & 0xFFFFFFFF, 'data')]}
        m.finish()
        self.assertTrue(m.near_data_shift(0x8015FFFF, (-0x1520) & 0xFFFFFFFF, 'ovl_e'))
        self.assertTrue(m.near_data_shift(0x8014F000, (-0x1520) & 0xFFFFFFFF, 'ovl_e'))   # below the run
        self.assertTrue(m.near_data_shift(0x8005F000, (-0x28) & 0xFFFFFFFF, 'ovl_e'))     # main counts for every section
        self.assertFalse(m.near_data_shift(0x8015FFFF, (-0x1520) & 0xFFFFFFFF, 'ovl_b'))  # another overlay's run
        self.assertFalse(m.near_data_shift(0x80048000, (-0xA0) & 0xFFFFFFFF, None))       # text
        self.assertFalse(m.near_data_shift(0x80162000, (-0x1520) & 0xFFFFFFFF, 'ovl_e'))  # exactly at the reach, above
        self.assertFalse(m.near_data_shift(0x8014E000, (-0x1520) & 0xFFFFFFFF, 'ovl_e'))  # exactly at the reach, below

    def test_near_data_shift_never_for_a_priority_range(self):
        m = d.AddressMap()
        m.main = [(0x80060000, 0x80070000, (-0x28) & 0xFFFFFFFF, 'data')]
        m.priority = [(0x80062000, 0x80063000, 0x00580000, 'save')]
        m.finish()
        self.assertTrue(m.near_data_shift(0x80061000, (-0x28) & 0xFFFFFFFF))
        self.assertFalse(m.near_data_shift(0x80062800, (-0x28) & 0xFFFFFFFF))

    def test_save_area_stays_off_the_hacks_memory(self):
        # The relinked hacks' payload image ends at 0x804A0000.
        pay = dict(ram=0x80400FE0, size=0x9F020)
        base = d.save_area_base(pay, 0x1AD0)
        self.assertEqual(base, 0x804C0000)
        self.assertGreaterEqual(base, 0x804A1000)                      # aki-roadmap#179: No Mercy Plus V3's table
        self.assertLessEqual(base + d.SAVE_AREA_ROOM, 0x804DC000)      # aki-roadmap#190: BTBW's table, up to 0x80500000

    def test_save_area_falls_back_when_the_payload_reaches_it(self):
        self.assertEqual(d.save_area_base(dict(ram=0x80400000, size=0xF8000), 0x1AD0), 0x804F8000 + 0x8000)
        self.assertEqual(d.save_area_base(dict(ram=0x80400FE0, size=0x9F020), 0x10000), 0x804A0000)
        self.assertEqual(d.save_area_base(None, 0x1AD0), 0x80600000)

    def test_moved_save_buffer_keeps_its_new_address(self):
        # Same instructions as stock, stock value on the run's shift, but the map moved this buffer to Expansion RAM.
        m = d.AddressMap()
        m.main = [(0x80060000, 0x80070000, (-0x28) & 0xFFFFFFFF, 'data')]
        m.priority = [(0x80062000, 0x80063000, 0x00580000, 'save')]
        m.finish()
        cands = lambda a: m.candidates(a)
        cands.near_shift = lambda a, shift: m.near_data_shift(a, shift)
        hh, hl = hi_lo(0x80062800)
        sh, sl = hi_lo(0x80062800 - 0x28)
        out = d.rewrite([lui(V0, hh), addiu(V0, V0, hl)], 0x80001000, 0x80001000, cands,
                        [lui(V0, sh), addiu(V0, V0, sl)], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x805E2800)

    def test_biased_base_only_for_a_built_address(self):
        # A load through the pair is not a base built for indexing: the map's reading stays.
        hh, hl = hi_lo(0x8015220C)
        sh, sl = hi_lo(0x80150CEC)
        out = d.rewrite([lui(V0, hh), lw(V1, hl, V0)], 0x800DA630, 0x800DA630, self.biased_cands(),
                        [lui(V0, sh), lw(V1, sl, V0)], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x8015220C - 0x1530)

    def indexed_cands(self):
        # No Mercy's label lookup: the base lies in main's bss (-0x1530), the table far above in ovl_a's data (-0x1500)
        m = d.AddressMap()
        m.main = [(0x800B0000, 0x800C0000, (-0x1530) & 0xFFFFFFFF, 'data')]
        m.ovl = {'ovl_a': [(0x800F0000, 0x80100000, (-0x1500) & 0xFFFFFFFF, 'data')]}
        m.finish()
        cands = lambda a: m.candidates(a, 'ovl_a')
        cands.near_shift = lambda a, shift, far=False: m.far_own_data_shift(a, shift, 'ovl_a') if far else m.near_data_shift(a, shift, 'ovl_a')
        return cands

    def test_indexed_load_through_a_far_biased_base_takes_the_stock_address(self):
        # aki-roadmap#191: `lui at; addu at,at,idx; lw v0,%lo(at)` with `table - 0xF0CE * 4`. The run at the base's own
        # address (-0x1530) reads 12 words before the table, so labels showed `0` and `150528`.
        hh, hl = hi_lo(0x800B8458)
        sh, sl = hi_lo(0x800B6F58)
        hack = [lui(AT, hh), addu(AT, AT, V0), lw(V0, hl, AT)]
        stock = [lui(AT, sh), addu(AT, AT, V0), lw(V0, sl, AT)]
        self.assertEqual(d.rewrite(hack, 0x800DEC94, 0x800DD764, self.indexed_cands(), stock, []), stock)

    def test_indexed_base_register_order_does_not_matter(self):
        hh, hl = hi_lo(0x800B8458)
        sh, sl = hi_lo(0x800B6F58)
        hack = [lui(AT, hh), addu(AT, V0, AT), lw(V0, hl, AT)]
        stock = [lui(AT, sh), addu(AT, V0, AT), lw(V0, sl, AT)]
        self.assertEqual(d.rewrite(hack, 0x800DEC94, 0x800DD764, self.indexed_cands(), stock, []), stock)

    def test_far_stock_value_needs_an_index(self):
        # The same two values, nothing added to the register: a plain variable, so the map's reading stays.
        hh, hl = hi_lo(0x800B8458)
        sh, sl = hi_lo(0x800B6F58)
        out = d.rewrite([lui(AT, hh), lw(V0, hl, AT)], 0x800DEC94, 0x800DD764, self.indexed_cands(),
                        [lui(AT, sh), lw(V0, sl, AT)], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x800B8458 - 0x1530)

    def test_index_on_another_register_is_no_index(self):
        hh, hl = hi_lo(0x800B8458)
        sh, sl = hi_lo(0x800B6F58)
        out = d.rewrite([lui(AT, hh), addu(V1, V1, V0), lw(V0, hl, AT)], 0x800DEC94, 0x800DD764, self.indexed_cands(),
                        [lui(AT, sh), addu(V1, V1, V0), lw(V0, sl, AT)], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x800B8458 - 0x1530)

    def test_indexed_base_in_main_read_by_main_keeps_the_map(self):
        # V3 and BTBW's main: the hack moved this table, so the map's -0xA0 finds it and the stock value holds other
        # data. A -0x28 run further up in main doesn't make the stock value right; taking it froze the game at wrestler select.
        m = d.AddressMap()
        m.main = [(0x8004D000, 0x8004E000, (-0xA0) & 0xFFFFFFFF, 'data'), (0x80060000, 0x80070000, (-0x28) & 0xFFFFFFFF, 'data')]
        m.finish()
        cands = lambda a: m.candidates(a, None)
        cands.near_shift = lambda a, shift, far=False: m.far_own_data_shift(a, shift, None) if far else m.near_data_shift(a, shift, None)
        hh, hl = hi_lo(0x8004D25C)
        sh, sl = hi_lo(0x8004D234)
        out = d.rewrite([lui(A2, hh), addu(A2, A2, V0), lw(A2, hl, A2)], 0x80019124, 0x80019124, cands,
                        [lui(A2, sh), addu(A2, A2, V0), lw(A2, sl, A2)], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x8004D25C - 0xA0)

    def test_indexed_repoint_to_no_data_run_keeps_the_map(self):
        # The hack repoints an indexed table: the stock value's shift (-0x2200) is no data run anywhere.
        hh, hl = hi_lo(0x800B8458)
        sh, sl = hi_lo(0x800B8458 - 0x2200)
        out = d.rewrite([lui(AT, hh), addu(AT, AT, V0), lw(V0, hl, AT)], 0x800DEC94, 0x800DD764, self.indexed_cands(),
                        [lui(AT, sh), addu(AT, AT, V0), lw(V0, sl, AT)], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x800B8458 - 0x1530)

    def test_biased_base_stock_differs_in_one_instruction(self):
        hh, hl = hi_lo(0x8015220C)
        sh, sl = hi_lo(0x80150CEC)
        hack = [lui(V0, hh), addiu(V0, V0, hl)]
        for stock in ([lui(V1, sh), addiu(V0, V0, sl)], [lui(V0, sh), addiu(V1, V0, sl)]):
            out = d.rewrite(hack, 0x800DA630, 0x800DA630, self.biased_cands(), stock, [])
            self.assertEqual(mips.hilo_refs(out)[0][2], 0x8015220C - 0x1530)

    def test_biased_base_with_ori_and_a_sign_extended_low_half(self):
        # ori: the low half is unsigned; addiu with %lo >= 0x8000: the %hi half is one more
        for build in (lambda r, h, l: [lui(r, h), ori(r, r, l)], lambda r, h, l: [lui(r, h), addiu(r, r, l)]):
            a, st = 0x8015A00C, 0x8015A00C - 0x1520           # low half >= 0x8000 on both sides
            if build(V0, 0, 0)[1] >> 26 == 0x0D:
                hh, hl, sh, sl = a >> 16, a & 0xFFFF, st >> 16, st & 0xFFFF
            else:
                hh, hl, sh, sl = *hi_lo(a), *hi_lo(st)
            m = d.AddressMap()
            m.ovl = {'ovl_e': [(0x80158000, 0x8015B000, (-0x1530) & 0xFFFFFFFF, 'data'),
                               (0x8015B000, 0x80160000, (-0x1520) & 0xFFFFFFFF, 'data')]}
            m.finish()
            cands = lambda x: m.candidates(x, 'ovl_e')
            cands.near_shift = lambda x, shift: m.near_data_shift(x, shift, 'ovl_e')
            stock = build(V0, sh, sl)
            self.assertEqual(d.rewrite(build(V0, hh, hl), 0x800DA630, 0x800DA630, cands, stock, []), stock)

    def test_biased_base_skips_an_address_inside_the_function(self):
        # The function's own address follows its placement, whatever the stock words say.
        a = 0x8015220C
        hh, hl = hi_lo(a + 8)
        sh, sl = hi_lo(a + 8 - 0x1520)
        out = d.rewrite([lui(V0, hh), addiu(V0, V0, hl), NOP, NOP], a, 0x80150000, self.biased_cands(),
                        [lui(V0, sh), addiu(V0, V0, sl), NOP, NOP], [])
        self.assertEqual(mips.hilo_refs(out)[0][2], 0x80150008)

    def test_lo_after_return_point_pairs_with_the_hooks_lui(self):
        # the hook left at = 0x8015xxxx (+index); back in the function: lw v0, 0x34FC(at); jr v0
        words = [lw(V0, 0x34FC, AT), jr(V0), NOP]
        init = {AT: 0x80150000}
        cand = lambda a: [a - 0x1520] if 0x80150000 <= a < 0x80160000 else []
        out = d.rewrite(words, 0x800DC478, 0x800DAF48, cand, [lw(V0, 0x1FDC, AT), jr(V0), NOP], [], init=init)
        self.assertEqual(out[0], lw(V0, 0x1FDC, AT))

    def test_split_hi_takes_common_shift(self):
        # one lui feeds two uses whose first choices would need different %hi halves; a shift
        # valid for both (-0x1500) is used instead
        def cands(a):
            if a == 0x80148000:
                return [(a - 0x12000) & 0xFFFFFFFF, (a - 0x1500) & 0xFFFFFFFF]
            return [(a - 0x1500) & 0xFFFFFFFF]
        hack = [lui(S0, 0x8015), lw(V0, -0x8000, S0), lw(V1, -0x7FF0, S0)]   # 0x80148000, 0x80148010
        probs = []
        out = d.rewrite(hack, 0x80001000, 0x80001000, cands, None, probs)
        self.assertEqual(probs, [])
        self.assertEqual([a for _, _, a in mips.hilo_refs(out)], [0x80146B00, 0x80146B10])


class MapTests(unittest.TestCase):
    def test_runs_from_pairs_and_stray(self):
        pairs = [(0x80040000 + 4 * i, 0x800400A0 + 4 * i) for i in range(10)]
        pairs.append((0x80040100, 0x800401A0 + 0x30))                  # one stray
        pairs += [(0x80040200 + 4 * i, 0x800402A0 + 4 * i) for i in range(10)]
        pairs += [(0x80050000 + 4 * i, 0x80051530 + 4 * i) for i in range(5)]
        runs = d.runs_from_pairs(pairs, 0x80040000, 0x80060000)
        self.assertEqual([r[2] for r in runs], [(-0xA0) & 0xFFFFFFFF, (-0x1530) & 0xFFFFFFFF])
        self.assertEqual(runs[0][0], 0x80040000)
        self.assertEqual(runs[-1][1], 0x80060000)

    def test_runs_from_pairs_lone_first_point_over_a_kept_layout(self):
        # aki-roadmap#173: one load repointed to the hack's own table is the lowest evidence in main's data; the code
        # above it says nothing moved, so the data below it must not move either
        pairs = [(0x8003DC80, 0x8003B60C)]
        pairs += [(0x8003C000 + 4 * i, 0x8003C000 + 4 * i) for i in range(5)]
        runs = d.runs_from_pairs(pairs, 0x80038840, 0x800E1B90)
        self.assertEqual(runs, [(0x80038840, 0x800E1B90, 0)])

    def test_runs_from_pairs_keeps_a_real_first_shift(self):
        # two or more points agree, or the next shift isn't "nothing moved": relinked hacks' data, kept as a shift
        pairs = [(0x80040000 + 0x100 * i, 0x800400A0 + 0x100 * i) for i in range(2)]
        pairs += [(0x80050000 + 4 * i, 0x80050000 + 4 * i) for i in range(5)]
        runs = d.runs_from_pairs(pairs, 0x80040000, 0x80060000)
        self.assertEqual([r[2] for r in runs], [(-0xA0) & 0xFFFFFFFF, 0])
        pairs = [(0x80040000, 0x800400A0)]
        pairs += [(0x80050000 + 4 * i, 0x80050090 + 4 * i) for i in range(5)]
        runs = d.runs_from_pairs(pairs, 0x80040000, 0x80060000)
        self.assertEqual([r[2] for r in runs], [(-0xA0) & 0xFFFFFFFF, (-0x90) & 0xFFFFFFFF])

    def test_runs_from_pairs_keeps_a_first_shift_many_references_agree_on(self):
        pairs = [(0x80040000, 0x800400A0)] * 40
        pairs += [(0x80050000 + 4 * i, 0x80050000 + 4 * i) for i in range(5)]
        runs = d.runs_from_pairs(pairs, 0x80040000, 0x80060000)
        self.assertEqual([r[2] for r in runs], [(-0xA0) & 0xFFFFFFFF, 0])

    def test_refine_boundaries_reads_a_pointer_table(self):
        # aki-roadmap#120: a table of pointers to strings, with entries inserted in the hack. No word equals stock (each
        # pointer carries the strings' shift), so the words must count once translated. The change of shift belongs where
        # the entries were inserted, not at the end of the gap or at a stray run of zeros before the table.
        TEXT, DATA, ROM = 0x80090000, 0x80090100, 0x1000
        STR_SHIFT, D1, D2 = 0x40, (-0x80) & 0xFFFFFFFF, (-0x88) & 0xFFFFFFFF
        strs = [0x80090100 + 8 * i for i in range(8)]     # stock string addresses
        table = 0x80090200                                # stock table
        size = 0x1000
        base, hack = bytearray(ROM + size), bytearray(ROM + size)
        stock_rows = [s for s in strs]
        hack_rows = [s + STR_SHIFT for s in strs[:4]] + [0x80090170, 0x80090178] + [s + STR_SHIFT for s in strs[4:]]
        put(base, ROM + (table - TEXT), stock_rows)
        put(hack, ROM + (table + 0x80 - TEXT), hack_rows)
        A = d.Analysis.__new__(d.Analysis)
        A.byname = {'ovl_a': d.Section('ovl_a', ROM, TEXT, DATA, TEXT + size, TEXT + size)}
        A.place = {'ovl_a': (ROM, TEXT, DATA, DATA, TEXT + size)}
        A.hsecs = {}
        A.base, A.hack = bytes(base), bytes(hack)
        # the strings' run, then the table's two shifts; the first reference with the second shift is past the table,
        # and no reference falls inside it
        runs = [(DATA, table + 0x60, (-STR_SHIFT) & 0xFFFFFFFF), (table + 0x60, table + 0xC0, D1), (table + 0xC0, TEXT + size, D2)]
        last_ev = [table - 0x100, table + 0x60]
        out = A.refine_boundaries('ovl_a', runs, last_ev)
        # the hack's code indexes the table with the hack's numbering: all 10 rows keep D1, the change comes after them
        self.assertEqual(out[1][2], D1)
        self.assertEqual(out[1][1], table + 0x80 + 4 * 10)
        self.assertEqual(out[2][0], table + 0x80 + 4 * 10)

    def _table_analysis(self, rows_at):
        # a section whose hack image holds pointer rows at the given addresses; every row points into the first run
        TEXT, DATA, ROM, size = 0x80090000, 0x80090100, 0x1000, 0x2000
        hack = bytearray(ROM + size)
        for addr, n in rows_at:
            put(hack, ROM + (addr - TEXT), [0x80090110] * n)
        A = d.Analysis.__new__(d.Analysis)
        A.byname = {'ovl_a': d.Section('ovl_a', ROM, TEXT, DATA, TEXT + size, TEXT + size)}
        A.place = {'ovl_a': (ROM, TEXT, DATA, DATA, TEXT + size)}
        A.hsecs = {}
        A.base, A.hack = bytes(bytearray(ROM + size)), bytes(hack)
        return A, DATA, TEXT + size

    def test_refine_boundaries_table_stops_at_evidence(self):
        # Two adjacent tables: code references B's head with the next shift, so B keeps it.
        A_, DATA, END = self._table_analysis([(0x80090300, 8), (0x80090320, 8)])
        runs = [(DATA, 0x80090320, 8), (0x80090320, END, 16)]
        out = A_.refine_boundaries('ovl_a', runs, [0x80090308])
        self.assertEqual(out[0][1], 0x80090320)
        self.assertEqual(out[1][0], 0x80090320)

    def test_refine_boundaries_never_inverts_a_run(self):
        # A table running past the next run's evidence must not leave a run with lo > hi: an address map with one loses
        # every address above it.
        A_, DATA, END = self._table_analysis([(0x80090300, 16)])
        runs = [(DATA, 0x80090320, 0), (0x80090320, 0x8009032C, 8), (0x8009032C, END, 16)]
        out = A_.refine_boundaries('ovl_a', runs, [0x80090308, 0x80090328])
        for lo, hi, _ in out:
            self.assertLessEqual(lo, hi)
        self.assertEqual(out[-1][1], END)
        self.assertEqual([r[0] for r in out[1:]], [r[1] for r in out[:-1]])

    def test_main_data_evidence_needs_a_stock_data_address(self):
        # VPW64 Deluxe (aki-roadmap#222): a hook repoints a stock text reference at a cave in main's data. That pair says
        # nothing about how the data moved; only the real data reference does.
        main = d.Section('main', 0x1000, 0x80000400, 0x80039620, 0x8004B2A0, 0x80060000)
        hook = (0x8001E630, 0x8003AF34)
        data, bss = (0x80040000, 0x80040000), (0x8004B2A0, 0x8004B2A0)
        ovl = (0x80100000, 0x80041000)
        got = d.main_data_evidence(main, {'main': [hook, data], 'ovl_a': [ovl, bss]})
        self.assertEqual(got, [data, bss])
        self.assertEqual(d.runs_from_pairs(got, 0x80039620, 0x800E6AF0)[0][2], 0)

    def runtime_analysis(self, chunk_words, loaded):
        # one overlay with a chunk reshaped at its own address (0x80100000-0x80100200), and code loaded at run time past the stock code
        ROM, TEXT, size, LOADED = 0x1000, 0x80100000, 0x1000, 0x3000
        hack = bytearray(LOADED + 0x400)
        for off, words in chunk_words.items():
            put(hack, ROM + off, words)
        put(hack, LOADED, loaded)
        A = d.Analysis.__new__(d.Analysis)
        A.byname = {'ovl_b': d.Section('ovl_b', ROM, TEXT, TEXT + 0xF00, TEXT + size, TEXT + size, funcs=[(0, 0x100)])}
        A.secs = list(A.byname.values())
        A.place = {'ovl_b': (ROM, TEXT, TEXT + 0xF00, TEXT + 0xF00, TEXT + size)}
        A.reshaped = [('ovl_b', TEXT, TEXT + 0x200, TEXT, TEXT + 0x200), ('ovl_b', TEXT + 0x300, TEXT + 0x320, TEXT + 0x340, TEXT + 0x360)]
        A.payload = None
        A.hack = bytes(hack)
        A.map = d.AddressMap()
        A.map.ovl['ovl_b'] = [(TEXT, TEXT + 0x10000, 0, 'text')]      # far branches stay at their addresses
        A.map.finish()
        return A

    def test_runtime_targets(self):
        # VPW64 Deluxe (aki-roadmap#222): only a real jump counts: into a chunk at its own address, from code outside the stock code
        TEXT = 0x80100000
        jump = lambda t, op=2: (op << 26) | ((t & 0x0FFFFFFF) >> 2)
        nop = [0] * 6
        loaded = nop + [jump(TEXT + 0x40)] + nop + nop + [jump(TEXT + 0x340)] + nop + nop + [jump(TEXT + 0x800)] + nop + nop + [jump(TEXT + 0x80, 3)] + nop
        A = self.runtime_analysis({}, loaded)
        self.assertEqual(d.runtime_targets(A), {('ovl_b', TEXT + 0x40), ('ovl_b', TEXT + 0x80)})
        # a jump in the stock code is rewritten anyway; one between undecodable words is a chance match in compressed data
        A = self.runtime_analysis({0xE00: nop + [jump(TEXT + 0x40)] + nop}, [0x48000000] * 6 + [jump(TEXT + 0x40)] + [0x48000000] * 6)
        self.assertEqual(d.runtime_targets(A), set())

    def test_runtime_functions_compile_the_hack_words_at_their_own_address(self):
        TEXT = 0x80100000
        JR = 0x03E00008
        beq_far = 0x11220000 | 0x1000                       # a far branch out of the chunk keeps its offset (a lookup)
        A = self.runtime_analysis({0x40: [0x11, beq_far, 0, JR, 0x13, 0x14]}, [0] * 6 + [(2 << 26) | ((TEXT + 0x40) & 0x0FFFFFFF) >> 2] + [0] * 6)
        area, report = bytearray(0x18), {}
        got = d.runtime_functions(A, area, 0x200000, report)
        self.assertEqual(got, [dict(vram=TEXT + 0x40, entry=TEXT + 0x40, rom=0x200020, size=0x14, kind='runtime', section_rom=0x1000,
                                    data=[TEXT, TEXT + 0x1000, 0x1000])])
        self.assertEqual([d.word(area, 0x20 + 4 * i) for i in range(5)], [0x11, beq_far, 0, JR, 0x13])
        self.assertEqual(report['runtime_functions'], [TEXT + 0x40])
        self.assertEqual(report['runtime_function_problems'], [])

    def test_runtime_functions_with_a_loop_above_the_entry(self):
        TEXT = 0x80100000
        JR = 0x03E00008
        back = 0x1000FFFD                                   # at +0x48, to +0x40, above the entry at +0x44
        A = self.runtime_analysis({0x40: [0x11, 0x12, back, 0, JR, 0]}, [0] * 6 + [(2 << 26) | ((TEXT + 0x44) & 0x0FFFFFFF) >> 2] + [0] * 6)
        area = bytearray()
        f = d.runtime_functions(A, area, 0x200000, {})[0]
        self.assertEqual((f['vram'], f['entry'], f['rom']), (TEXT + 0x38, TEXT + 0x44, 0x200000))
        self.assertEqual(d.mips.branch_target(d.word(area, 0), f['vram']), f['entry'])
        self.assertEqual(d.word(area, 8), 0x11)             # the words keep their addresses: +0x40 is at vram + 8

    def test_caller_stubs_set_the_return_address_a_hook_checks(self):
        # VPW64 Deluxe (aki-roadmap#222): code loaded at run time compares $ra with 0x80100108, the return address of a jal
        TEXT, RA = 0x80100000, 0x80100108
        check = [0x3C080000 | (RA >> 16), 0x35080000 | (RA & 0xFFFF), 0x13E80005]          # lui t0; ori t0,t0; beq ra,t0
        jal = 0x0C000000 | ((0x80020000 >> 2) & 0x3FFFFFF)
        A = self.runtime_analysis({0x100: [jal, 0x24A5FFFF, 0x11]}, [0] * 6 + check + [0] * 6)
        A.new_blocks = []
        A.extras = []
        self.assertEqual(d.caller_checks(A), {RA})
        D, area, report = bytearray(A.hack), bytearray(), {}
        d.add_caller_stubs(A, D, area, report)
        self.assertEqual(report['caller_stubs'], [('ovl_b', TEXT + 0x100, 0x80020000)])
        self.assertEqual(d.mips.jump_target(d.word(D, 0x1100), TEXT + 0x100), d.NEW_CODE_VRAM)
        self.assertEqual(d.word(D, 0x1100) >> 26, 3)                                       # still a jal, its delay slot untouched
        self.assertEqual(d.word(D, 0x1104), 0x24A5FFFF)
        self.assertEqual([d.word(area, 4 * i) for i in range(2)], [0x3C1F8010, 0x37FF0108])
        self.assertEqual(d.word(area, 8) >> 26, 3)                                         # a call, so $ra can be cleared after it
        self.assertEqual(d.mips.jump_target(d.word(area, 8), d.NEW_CODE_VRAM + 8), 0x80020000)
        self.assertEqual([d.word(area, 4 * i) for i in range(3, 7)], [0, 0x0000F821, 0x03E00008, 0])
        self.assertEqual([(x.kind, x.vram, x.size) for x in A.extras], [('refresh', d.NEW_CODE_VRAM, 28)])

    def test_caller_check_without_a_call_there_is_reported(self):
        RA = 0x80100108
        A = self.runtime_analysis({0x100: [0x11, 0x12, 0x13]}, [0] * 6 + [0x3C080000 | (RA >> 16), 0x35080000 | (RA & 0xFFFF), 0x13E80005] + [0] * 6)
        A.new_blocks, A.extras = [], []
        report = {}
        d.add_caller_stubs(A, bytearray(A.hack), bytearray(), report)
        self.assertEqual(report['caller_checks_unhandled'], [RA])
        self.assertNotIn('caller_stubs', report)

    def test_translate_data_reports_dropped_words(self):
        TEXT, DATA, ROM, size = 0x80090000, 0x80090100, 0x1000, 0x400
        base, hack = bytearray(ROM + size), bytearray(ROM + size)
        s0, y1, y2 = 0x80090200, 0x80090210, 0x80090218
        put(hack, ROM + (y1 - TEXT), [0, 0, 0x77, 0])       # the 3rd word lands on s0, already claimed by the 1st
        A = d.Analysis.__new__(d.Analysis)
        A.byname = {'ovl_a': d.Section('ovl_a', ROM, TEXT, DATA, TEXT + size, TEXT + size)}
        A.place = {'ovl_a': (ROM, TEXT, DATA, DATA, TEXT + size)}
        A.hsecs, A.new_blocks, A.payload = {}, [], None
        A.base, A.hack = bytes(base), bytes(hack)
        A.map = d.AddressMap()
        A.map.ovl = {'ovl_a': [(y1, y2, (s0 - y1) & 0xFFFFFFFF, 'data'), (y2, y2 + 8, (s0 - y2) & 0xFFFFFFFF, 'data')]}
        A.map.finish()
        report = {}
        d.translate_data(A, bytearray(base), report)
        self.assertEqual(report['data_words_overlapped'], 2)
        self.assertEqual(report['data_words_dropped'], [('ovl_a', y2, s0)])

    def test_translate_data_overlapping_ranges_keep_the_first(self):
        # aki-roadmap#120: the hack's data is longer than stock's, so two ranges of it land on one stock stretch. The later
        # range must not cut through what the earlier one wrote, nor through what it left equal to stock.
        TEXT, DATA, ROM, size = 0x80090000, 0x80090100, 0x1000, 0x400
        base, hack = bytearray(ROM + size), bytearray(ROM + size)
        s0, y1, y2 = 0x80090200, 0x80090210, 0x80090220
        put(base, ROM + (s0 - TEXT), [0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555, 0x66666666])
        put(hack, ROM + (y1 - TEXT), [0xAAAA0001, 0x22222222, 0xAAAA0003, 0x44444444,   # lands on s0 .. s0+0x10
                                      0xBBBB0001, 0xBBBB0002, 0xBBBB0003, 0xBBBB0004])  # lands on s0+8 .. s0+0x18
        A = d.Analysis.__new__(d.Analysis)
        A.byname = {'ovl_a': d.Section('ovl_a', ROM, TEXT, DATA, TEXT + size, TEXT + size)}
        A.place = {'ovl_a': (ROM, TEXT, DATA, DATA, TEXT + size)}
        A.hsecs, A.new_blocks, A.payload = {}, [], None
        A.base, A.hack = bytes(base), bytes(hack)
        A.map = d.AddressMap()
        A.map.ovl = {'ovl_a': [(y1, y2, (s0 - y1) & 0xFFFFFFFF, 'data'), (y2, y2 + 0x10, (s0 + 8 - y2) & 0xFFFFFFFF, 'data')]}
        A.map.finish()
        D, report = bytearray(base), {}
        d.translate_data(A, D, report)
        got = struct.unpack('>6I', bytes(D[ROM + (s0 - TEXT):ROM + (s0 - TEXT) + 24]))
        self.assertEqual(got, (0xAAAA0001, 0x22222222, 0xAAAA0003, 0x44444444, 0xBBBB0003, 0xBBBB0004))
        self.assertEqual(report['data_words_overlapped'], 2)

    def test_address_map_overlay_context(self):
        m = d.AddressMap()
        m.main = [(0x80000400, 0x80040000, (-0x90) & 0xFFFFFFFF, 'text')]
        m.ovl = {'ovl_b': [(0x80107C60, 0x80120000, (-0x1500) & 0xFFFFFFFF, 'text')],
                 'ovl_e': [(0x800DAE90, 0x8016CF10, (-0x1530) & 0xFFFFFFFF, 'text')]}
        m.finish()
        self.assertEqual(m.translate(0x80001090), 0x80001000)
        self.assertEqual(m.translate(0x80110000, 'ovl_b'), 0x8010EB00)
        self.assertEqual(m.translate(0x80110000, 'ovl_e'), 0x8010EAD0)
        self.assertEqual(m.translate(0x80420000), 0x80420000)        # Expansion RAM stays


def put(buf, off, words):
    for i, w in enumerate(words):
        struct.pack_into('>I', buf, off + 4 * i, w)


class MatchTests(unittest.TestCase):
    def test_shifted_functions_and_a_changed_one(self):
        # stock: three functions at 0x80001000; hack: the same shapes 0x40 later, the middle one reshaped
        f1 = [addiu(29, 29, -0x18), sw(RA, 0x10, 29), jal(0x80004000), NOP, lw(RA, 0x10, 29), jr(RA), addiu(29, 29, 0x18)]
        f2 = [lui(AT, 0x8005), lw(V0, 0x10, AT), addiu(V0, V0, 1), sw(V0, 0x10, AT), jr(RA), NOP, NOP]
        f3 = [lui(A0, 0x8006), addiu(A0, A0, 0x20), jal(0x80004100), NOP, jr(RA), NOP, NOP]
        f2h = [addiu(V0, 0, 5), addiu(V1, 0, 6), addu(V0, V0, V1), jr(RA), NOP, NOP, NOP]
        base, hack = bytearray(0x4000), bytearray(0x4000)
        put(base, 0x1000, f1 + f2 + f3)
        put(hack, 0x1040, f1 + f2h + f3)
        sec = d.Section('t', 0x1000, 0x80001000, 0x80001000 + 4 * 21, 0x80001000 + 4 * 21, 0x80001000 + 4 * 21,
                        [(0, 28), (28, 28), (56, 28)])
        m = d.match_section(bytes(base), bytes(hack), sec, 0x1000, 0x80001000, 0x80001000 + 0x40 + 4 * 21)
        self.assertEqual([(s, h) for s, h, z in m.matched], [(0x80001000, 0x80001040), (0x80001038, 0x80001078)])
        self.assertIn((0x8000101C, 0x80001038, 0x8000105C, 0x80001078), m.chunks)


class TraceTests(unittest.TestCase):
    def test_extent_follows_branches_and_stops_at_jr(self):
        words = {0x80400000: beq(V0, 0, 3), 0x80400004: NOP, 0x80400008: jr(RA), 0x8040000C: NOP,
                 0x80400010: j(0x80400100), 0x80400014: NOP, 0x80400018: 0xDEADBEEF}
        end, jt, jalt, back = d.trace_extent(lambda v: words.get(v, 0), 0x80400000, 0x80400000, 0x80400100)
        self.assertEqual(end, 0x80400018)
        self.assertEqual(jt, {0x80400100})

    def test_branch_into_a_jump_delay_slot_traces_on(self):
        # bne -> the j's delay slot, then fall through to a jump-table dispatch (hand-written hack code)
        words = {0x80400000: beq(AT, S0, 2) | (1 << 26), 0x80400004: NOP, 0x80400008: j(0x80100000),
                 0x8040000C: NOP, 0x80400010: lui(AT, 0x8015), 0x80400014: jr(V0), 0x80400018: NOP}
        end, jt, jalt, back = d.trace_extent(lambda v: words.get(v, 0), 0x80400000, 0x80400000, 0x80400100)
        self.assertEqual(end, 0x8040001C)


class CaveBlockTests(unittest.TestCase):
    def test_far_caves_get_their_own_blocks(self):
        # Revenge Starrcade 2.0: two cave clusters ~48 KB apart, main's tables between them (aki-roadmap#119)
        caves = [(0x8003A8E8, 0x8003A92C), (0x8002EB04, 0x8002EB8C), (0x8002EB8C, 0x8002EC20), (0x8003AF00, 0x8003AF0C)]
        self.assertEqual(d.cave_blocks(caves), [(0x8002EB04, 0x8002EC20), (0x8003A8E8, 0x8003A92C), (0x8003AF00, 0x8003AF0C)])

    def test_back_to_back_and_overlapping_caves_share_a_block(self):
        caves = [(0x80030000, 0x80030040), (0x80030048, 0x80030080), (0x80030010, 0x80030020)]
        self.assertEqual(d.cave_blocks(caves), [(0x80030000, 0x80030080)])

    def test_gap_boundary(self):
        self.assertEqual(d.cave_blocks([(0x80030000, 0x80030010), (0x80030020, 0x80030030)]), [(0x80030000, 0x80030030)])
        self.assertEqual(d.cave_blocks([(0x80030000, 0x80030010), (0x80030024, 0x80030030)]),
                         [(0x80030000, 0x80030010), (0x80030024, 0x80030030)])

    def test_no_caves(self):
        self.assertEqual(d.cave_blocks([]), [])


class PlausibleTests(unittest.TestCase):
    def test_code_is_plausible_and_text_is_not(self):
        self.assertTrue(mips.plausible_entry([addiu(AT, 0, 1), lui(V0, 0x8015), addu(V0, V0, A0), jr(RA), NOP]))
        # a byte table that happens to decode as a shift with rs set, and ASCII text
        self.assertFalse(mips.plausible_entry([0x01030202, 0x03020401]))
        self.assertFalse(mips.plausible(0x43414745))      # "CAGE": COP0 with an unused rs


class CodeWriteTests(unittest.TestCase):
    """aki-roadmap#191: a hack that patches its own instructions needs them watched, or the recompiled code never changes."""
    S1, T1 = 17, 9
    @staticmethod
    def sh(rt, imm, rs): return (41 << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
    @staticmethod
    def is_code(a): return 0x80100000 <= a < 0x80200000

    def test_the_art_hook_is_found(self):
        # No Mercy Plus's menu art: sh t1,-12554(s1) with s1 = 0x80130000 rewrites the immediate of the word at 8012CEF4
        words = [lui(self.S1, 0x8013), addiu(self.T1, T0, 0x397), self.sh(self.T1, -12554, self.S1)]
        self.assertEqual(d.code_write_targets(words, self.is_code), {0x8012CEF4})

    def test_a_base_built_with_addiu_is_followed(self):
        words = [lui(T0, 0x8012), addiu(T0, T0, 0x1000), sw(self.T1, 8, T0)]
        self.assertEqual(d.code_write_targets(words, self.is_code), {0x80121008})

    def test_stores_outside_code_are_ignored(self):
        words = [lui(T0, 0x8040), sw(self.T1, 0x20, T0)]
        self.assertEqual(d.code_write_targets(words, self.is_code), set())

    def test_a_register_that_was_overwritten_is_forgotten(self):
        for clobber in (lw(T0, 0, A0), addu(T0, A0, A1)):
            self.assertEqual(d.code_write_targets([lui(T0, 0x8012), clobber, sw(self.T1, 0, T0)], self.is_code), set())

    def test_the_table_only_watches(self):
        text = d.code_watch_text({0x8012CEF4, 0x80100000}, 0x3C08, 'nomercy-rev1')
        lines = [l for l in text.splitlines() if l and l[0] not in '#[']
        # sorted, and every write sits behind a condition the entry code can't meet
        self.assertEqual(lines, ['D1000400 3C09', '81100000 0000', 'D1000400 3C09', '8112CEF4 0000'])
        self.assertIn('# rom: nomercy-rev1', text)
        self.assertIn('# default: on', text)


class RefreshHookTests(unittest.TestCase):
    T1, T4 = 9, 12

    def test_pure_hook_body(self):
        # out = *(in) + 0x10000, then back into the hooked function
        words = [lui(T0, 0x8012), lw(T0, 0x3450, T0), lui(self.T1, 1), addu(T0, T0, self.T1),
                 lui(self.T4, 0x8041), sw(T0, 0x20, self.T4), j(0x80001234), NOP, 0xDEADBEEF]
        idx, ins, outs = d.pure_hook_body(words)
        self.assertEqual(idx, [0, 1, 2, 3, 4, 5])
        self.assertEqual(ins, {0x80123450})
        self.assertEqual(outs, {0x80410020})

    def test_hook_with_branch_or_saved_register_is_not_pure(self):
        self.assertIsNone(d.pure_hook_body([lui(T0, 0x8012), beq(T0, 0, 2), NOP, j(0x80001234), NOP]))
        self.assertIsNone(d.pure_hook_body([lui(S0, 0x8012), lw(T0, 0x3450, S0), lui(self.T4, 0x8041),
                                            sw(T0, 0x20, self.T4), j(0x80001234), NOP]))

    def test_delay_slot_of_the_jump_back_is_part_of_the_body(self):
        words = [lui(T0, 0x8012), lw(T0, 0x3450, T0), lui(self.T4, 0x8041), j(0x80001234), sw(T0, 0x20, self.T4)]
        idx, ins, outs = d.pure_hook_body(words)
        self.assertEqual(idx, [0, 1, 2, 4])
        self.assertEqual(outs, {0x80410020})


def fake_payload(words_by_vram, extents, shift=None):
    """A minimal analysis around synthetic payload code at 0x80400000 (ROM 0x1000), for the passes
    that rewrite payload code in place. extents: [(vram, size)] of payload functions."""
    import types
    rom, ram, size = 0x1000, 0x80400000, 0x100
    buf = bytearray(rom + size)
    for v, w in words_by_vram.items():
        put(buf, rom + (v - ram), [w])
    cand = shift or (lambda a, ctx=None: [])
    A = types.SimpleNamespace(payload=dict(rom=rom, ram=ram, size=size), region_ctx={},
                              map=types.SimpleNamespace(candidates=cand), hack=bytes(buf),
                              extras=[d.Extra('payload', v, v, z) for v, z in extents])
    return A, bytearray(buf)


def wd(buf, A, v):
    return d.word(buf, A.payload['rom'] + (v - A.payload['ram']))


class PayloadStubTests(unittest.TestCase):
    S0, A0, A1, T1, T2, T3, T5, T9 = 16, 4, 5, 9, 10, 11, 13, 25

    def test_lo_that_no_hi_reaches_goes_through_a_stub(self):
        # one lui feeds 0x800993F0 and 0x800A261C; after a -0x1530 shift they need different %hi
        hack = {0x80400000: lui(self.S0, 0x800A), 0x80400004: lw(self.A0, -0x6C10, self.S0),
                0x80400008: lw(self.A1, 0x261C, self.S0), 0x8040000C: addiu(self.T1, 0, 1),
                0x80400010: jr(RA), 0x80400014: NOP}
        shift = lambda a, ctx=None: [a - 0x1530] if 0x80090000 <= a < 0x800B0000 else []
        A, D = fake_payload(hack, [(0x80400000, 0x18)], shift)
        put(D, 0x1000 + 8, [lw(self.A1, 0x10EC, self.S0)])      # what rewrite() could do: the majority
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(len(report['unreachable_lo_fixed']), 1)
        stub = d.NEW_CODE_VRAM
        self.assertEqual(wd(D, A, 0x80400004), j(stub))
        self.assertEqual(wd(D, A, 0x80400008), NOP)
        self.assertEqual([d.word(area, 4 * i) for i in range(5)],
                         [lui(self.A0, 0x8009), lw(self.A0, 0x7EC0, self.A0), lw(self.A1, 0x10EC, self.S0),
                          j(0x8040000C), NOP])
        self.assertIn(0x8040000C, [x.vram for x in A.extras if x.kind == 'payload'])

    SH = staticmethod(lambda rt, off, base: (0x29 << 26) | (base << 21) | (rt << 16) | (off & 0xFFFF))

    def relinked_stores(self, after=None):
        # a relinked hack's payload (aki-roadmap#223, synthetic words): lui t1, 0x8013 feeds three sh into ovl_d
        # immediates; after the -0x1500 shift the first two need %hi 0x8012 and the third (0x8012E0BE -> 0x8012CBBE)
        # needs 0x8013. The map also offers ovl_e's -0x1530 (same addresses), as a second candidate.
        sh = self.SH
        after = after or [lui(self.T1, 0x8012), jr(RA), NOP]
        hack = {0x80400000: lui(self.T1, 0x8013), 0x80400004: sh(self.T3, -0x7A10, self.T1),
                0x80400008: sh(self.T5, -0x7300, self.T1), 0x8040000C: sh(self.T3, -0x1F42, self.T1)}
        for i, w in enumerate(after):
            hack[0x80400010 + 4 * i] = w
        shift = lambda a, ctx=None: [a - 0x1500, a - 0x1530] if 0x80106760 <= a < 0x80170000 else []
        A, D = fake_payload(hack, [(0x80400000, 0x10 + 4 * len(after))], shift)
        # what rewrite() leaves: the majority's %hi, and the odd store's old %lo under it (-> 0x8011E0BE)
        put(D, 0x1000, [lui(self.T1, 0x8012), sh(self.T3, 0x70F0, self.T1), sh(self.T5, 0x7800, self.T1)])
        return A, D

    def test_store_no_hi_reaches_goes_through_its_dead_base(self):
        A, D = self.relinked_stores()
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(report['unreachable_lo_fixed'], [(0x8040000C, 0x8012E0BE, 0x8012CBBE)])
        stub = d.NEW_CODE_VRAM
        self.assertEqual(wd(D, A, 0x8040000C), j(stub))
        self.assertEqual(wd(D, A, 0x80400010), NOP)
        self.assertEqual([d.word(area, 4 * i) for i in range(5)],
                         [lui(self.T1, 0x8013), self.SH(self.T3, -0x3442, self.T1), lui(self.T1, 0x8012), j(0x80400014), NOP])
        self.assertEqual(wd(D, A, 0x80400004), self.SH(self.T3, 0x70F0, self.T1))   # the majority is untouched
        # the code-write watch now sees the intended word, not the stray one
        self.assertEqual(d.code_write_targets([d.word(area, 4 * i) for i in range(2)], lambda a: True), {0x8012CBBC})

    def test_store_uses_at_when_its_base_is_still_needed(self):
        A, D = self.relinked_stores([addiu(self.T2, 0, 1), (self.T1 << 21) | (self.T2 << 11) | 0x21, lui(d.AT, 0x8000), jr(RA), NOP])
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(len(report['unreachable_lo_fixed']), 1)
        self.assertEqual([d.word(area, 4 * i) for i in range(3)],
                         [lui(d.AT, 0x8013), self.SH(self.T3, -0x3442, d.AT), addiu(self.T2, 0, 1)])

    def test_store_is_left_when_no_scratch_register_is_free(self):
        # t1 is read after the store and $at is never overwritten before the jr
        A, D = self.relinked_stores([addiu(self.T2, 0, 1), (self.T1 << 21) | (self.T2 << 11) | 0x21, jr(RA), NOP])   # addu t2, t1, zero
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(report['unreachable_lo_fixed'], [])
        self.assertEqual(report['unreachable_lo_left'], [0x8040000C])
        self.assertEqual(len(area), 0)
        self.assertEqual(wd(D, A, 0x8040000C), self.SH(self.T3, -0x1F42, self.T1))

    def test_load_with_two_candidates_is_still_left_alone(self):
        # loads keep the old rule (one candidate only), so packs the store fix doesn't concern don't change
        A, D = self.relinked_stores()
        put(D, 0x1000 + 0xC, [lw(self.T3, -0x1F42, self.T1)])
        A.hack = bytes(A.hack[:0x100C]) + lw(self.T3, -0x1F42, self.T1).to_bytes(4, 'big') + bytes(A.hack[0x1010:])
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(report['unreachable_lo_fixed'], [])

    def test_gpr_read_and_overwritten_before_read(self):
        sh = self.SH
        self.assertEqual(d.gpr_read(sh(self.T3, 0, self.T1)), {self.T3, self.T1})
        self.assertEqual(d.gpr_read(lw(self.T3, 0, self.T1)), {self.T1})
        self.assertEqual(d.gpr_read(lui(self.T1, 0x8012)), set())
        self.assertEqual(d.gpr_read((0x31 << 26) | (self.T1 << 21) | (1 << 16)), {self.T1})   # lwc1 f1, 0(t1)
        self.assertTrue(d.overwritten_before_read([NOP, lui(self.T1, 0x8011)], self.T1))
        self.assertFalse(d.overwritten_before_read([sh(self.T5, 0, self.T1), lui(self.T1, 0x8011)], self.T1))
        self.assertFalse(d.overwritten_before_read([jr(RA), lui(self.T1, 0x8011)], self.T1))
        self.assertFalse(d.overwritten_before_read([NOP], self.T1))
        self.assertTrue(d.overwritten_before_read([lw(self.T1, 0, self.T1)], self.T1) is False)   # reads before writing

    def test_store_rewrite_reached_through_a_later_candidate_is_left_alone(self):
        # rewrite()'s common shift can encode the second candidate for every use: that store is right as it is
        A, D = self.relinked_stores()
        put(D, 0x1000, [lui(self.T1, 0x8013), self.SH(self.T3, -0x7A10 - 0x1530, self.T1),
                        self.SH(self.T5, -0x7300 - 0x1530, self.T1), self.SH(self.T3, -0x1F42 - 0x1530, self.T1)])
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(report['unreachable_lo_fixed'], [])
        self.assertEqual(len(area), 0)

    def test_consecutive_unreachable_stores_never_go_unreported(self):
        # two odd stores in a row: the first has no free scratch (the second reads t1, $at reaches the jr), so it is
        # reported; the second goes through t1, which the lui after it overwrites
        A, D = self.relinked_stores([self.SH(self.T5, -0x1F40, self.T1), lui(self.T1, 0x8012), jr(RA), NOP])
        area, report = bytearray(), {}
        d.fix_unreachable_lo(A, D, area, report)
        self.assertEqual(report['unreachable_lo_left'], [0x8040000C])
        self.assertEqual([f[0] for f in report['unreachable_lo_fixed']], [0x80400010])

    def test_partial_loads_read_their_target(self):
        lwl = (0x22 << 26) | (self.T1 << 21) | (d.AT << 16)
        self.assertEqual(d.gpr_read(lwl), {self.T1, d.AT})
        self.assertFalse(d.overwritten_before_read([lwl], d.AT))

    def test_table_right_after_its_code_has_an_owner(self):
        hack = {0x80400000: lui(self.T1, 0x8040), 0x80400004: addiu(self.T1, self.T1, 0x10),
                0x80400008: jr(RA), 0x8040000C: NOP, 0x80400010: 0x8014A3C0}
        A, D = fake_payload(hack, [(0x80400000, 0x10)])
        A.region_ctx = {0x80400000: 'ovl_e'}
        get = lambda v: d.word(A.hack, A.payload['rom'] + (v - A.payload['ram']))
        own = d.payload_data_owners(A, get, d.code_spans(A.extras, 'payload'))
        self.assertEqual(own(0x80400010), 'ovl_e')

    def owned_table(self, ctx_by_entry):
        # payload code at 0x80400000 builds 0x80400040 (lui t1 / addiu t1); a two-word table of hack addresses sits there.
        # 0x8014A3C0 is ovl_e data (-0x1530) and ovl_b/c/d data (-0x1500) at once.
        hack = {0x80400000: lui(self.T1, 0x8040), 0x80400004: addiu(self.T1, self.T1, 0x40),
                0x80400008: jr(RA), 0x8040000C: NOP,
                0x80400040: 0x8014A3C0, 0x80400044: 0x8014A3C4}
        A, D = fake_payload(hack, [(0x80400000, 0x10)])
        shifts = {'ovl_e': -0x1530, None: -0x1500}
        A.map.translate = lambda a, ctx=None: (a + shifts.get(ctx, -0x1500)) & 0xFFFFFFFF if 0x80140000 <= a < 0x80150000 else None
        A.region_ctx = ctx_by_entry
        return A, D

    def test_payload_table_takes_the_overlay_of_the_code_that_builds_its_address(self):
        # a relinked hack (aki-roadmap#223): a hook in ovl_e walks such a table; -0x1500 puts every write 12 entries off
        A, D = self.owned_table({0x80400000: 'ovl_e'})
        report = {}
        d.payload_fixups(A, D, report)
        self.assertEqual([wd(D, A, 0x80400040), wd(D, A, 0x80400044)], [0x80148E90, 0x80148E94])
        self.assertEqual(report['payload_pointers_by_owner'], 2)

    def test_payload_table_with_no_single_owner_keeps_the_default(self):
        A, D = self.owned_table({})
        report = {}
        d.payload_fixups(A, D, report)
        self.assertEqual([wd(D, A, 0x80400040), wd(D, A, 0x80400044)], [0x80148EC0, 0x80148EC4])
        self.assertEqual(report['payload_pointers_by_owner'], 0)

    def test_payload_data_owner_reach(self):
        A, D = self.owned_table({0x80400000: 'ovl_e'})
        get = lambda v: d.word(A.hack, A.payload['rom'] + (v - A.payload['ram']))
        own = d.payload_data_owners(A, get, d.code_spans(A.extras, 'payload'))
        self.assertEqual(own(0x80400040), 'ovl_e')
        self.assertEqual(own(0x80400040 + d.PAYLOAD_DATA_REACH - 4), 'ovl_e')
        self.assertIsNone(own(0x80400040 + d.PAYLOAD_DATA_REACH))
        self.assertIsNone(own(0x8040003C))          # below the first address code builds

    def test_self_modified_constant_moves_to_a_ram_variable(self):
        # ori t3, zero, 3 holds a variable; jal getpc / addiu t3, ra, -0xE / sh t2, 0(t3) patches it
        sh = lambda rt, off, base: (0x29 << 26) | (base << 21) | (rt << 16) | (off & 0xFFFF)
        hack = {0x80400000: addiu(self.T2, 0, 0x40), 0x80400004: addiu(self.T9, 0, 0),
                0x80400008: ori(self.T3, 0, 3), 0x8040000C: addiu(self.T5, 0, 1),
                0x80400010: jal(0x80400040), 0x80400014: NOP,
                0x80400018: addiu(self.T3, RA, -0xE), 0x8040001C: sh(self.T2, 0, self.T3),
                0x80400020: jr(RA), 0x80400024: NOP,
                0x80400040: jr(RA), 0x80400044: NOP}
        A, D = fake_payload(hack, [(0x80400000, 0x28), (0x80400040, 8)])
        area, report = bytearray(), {}
        d.fix_get_pc(A, D, area, report, new_rom=0x400000)
        var = A.ram_vars[1][0]
        self.assertEqual(var['ram'], 0x80402000)
        self.assertEqual(d.word(area, var['rom'] - 0x400000), ori(self.T3, 0, 3))    # preloaded original word
        vt = var['ram'] + 2
        self.assertEqual(wd(D, A, 0x80400014), lui(self.T3, (vt + 0x8000) >> 16))      # absolute address,
        self.assertEqual(wd(D, A, 0x80400018), addiu(self.T3, self.T3, vt & 0xFFFF))   # no ra needed
        stub = wd(D, A, 0x80400008)
        self.assertEqual(stub >> 26, 2)
        s = mips.jump_target(stub, 0x80400008) - d.NEW_CODE_VRAM
        lhu = (0x25 << 26) | (self.T3 << 21) | (self.T3 << 16) | (vt & 0xFFFF)
        self.assertEqual([d.word(area, s + 4 * i) for i in range(5)],
                         [lui(self.T3, (vt + 0x8000) >> 16), lhu, addiu(self.T5, 0, 1), j(0x80400010), NOP])


def fake_main(funcs, stock_words, hack_words):
    """A minimal analysis with one 'main' section at 0x80000400 (ROM 0x1000) for the entry passes;
    stock_words / hack_words: {vram: word}."""
    import types
    size = 0x100
    base, D = bytearray(0x1000 + size), bytearray(0x1000 + size)
    for v, w in stock_words.items():
        put(base, 0x1000 + (v - 0x80000400), [w])
    for v, w in hack_words.items():
        put(D, 0x1000 + (v - 0x80000400), [w])
    sec = d.Section('main', 0x1000, 0x80000400, 0x80000400 + size, 0x80000400 + size, 0x80000400 + size,
                    [(v - 0x80000400, z) for v, z in funcs])
    A = types.SimpleNamespace(byname={'main': sec}, layout={'extra_code': []}, base=bytes(base), extras=[])
    return A, D


HULK = 0x48756C6B   # "Hulk": COP2, not an instruction a game has


class DataInCodeTests(unittest.TestCase):
    def test_dead_code_filled_with_names_is_data(self):
        whole, _ = d.data_in_code([HULK, 0x20486F67, 0x616E0000, NOP], 0x80001000)
        self.assertTrue(whole)

    def test_stray_word_after_the_return_is_unreached(self):
        whole, unreached = d.data_in_code([addiu(V0, 0, 1), jr(RA), NOP, HULK], 0x80001000)
        self.assertFalse(whole)
        self.assertEqual(unreached, {3})

    def test_branch_target_is_reached(self):
        # beq over a word to code after the return: both are code
        words = [beq(A0, 0, 3), NOP, jr(RA), NOP, addiu(V0, 0, 2), jr(RA), NOP]
        self.assertEqual(d.data_in_code(words, 0x80001000), (False, set()))

    def test_chunk_is_judged_per_function(self):
        # a changed chunk of two stock functions: the first now holds names, the second is hack code
        stock = {0x80000400: addiu(V0, 0, 1), 0x80000404: jr(RA), 0x80000408: NOP, 0x8000040C: NOP,
                 0x80000410: addiu(V0, 0, 2), 0x80000414: jr(RA), 0x80000418: NOP, 0x8000041C: NOP}
        A, _ = fake_main([(0x80000400, 0x10), (0x80000410, 0x10)], stock, {})
        sw = [stock[0x80000400 + 4 * i] for i in range(8)]
        hw = [HULK, 0x20486F67, 0x616E0000, 0, addiu(V0, 0, 3), jr(RA), NOP, NOP]
        ports = d.port_data_chunk(A, 'main', 0x80000400, 0x80000400, 0x20, hw, sw, shift_map([]))
        self.assertEqual([(p.stock_vram, p.status) for p in ports], [(0x80000400, 'data'), (0x80000410, 'changed')])
        self.assertEqual(ports[0].words, hw[:4])
        # inside such a chunk, a function whose entry decodes but which holds a non-instruction is data too
        hw[7] = HULK
        ports = d.port_data_chunk(A, 'main', 0x80000400, 0x80000400, 0x20, hw, sw, shift_map([]))
        self.assertEqual([(p.stock_vram, p.status) for p in ports], [(0x80000400, 'data'), (0x80000410, 'data')])

    def test_unreached_word_pointing_nowhere_is_kept_as_it_is(self):
        # a stray word after the return decodes as a jal to nowhere: data, not an unresolved reference
        stock = {0x80000400: addiu(V0, 0, 1), 0x80000404: jr(RA), 0x80000408: NOP, 0x8000040C: NOP}
        A, _ = fake_main([(0x80000400, 0x10)], stock, {})
        sw = [stock[0x80000400 + 4 * i] for i in range(4)]
        hw = [HULK, 0, 0, 0]                                   # the function's entry is data...
        p = d.port_data_chunk(A, 'main', 0x80000400, 0x80000400, 0x10, hw, sw, shift_map([]))
        self.assertEqual([(x.status, x.problems) for x in p], [('data', [])])
        hw = [addiu(V0, 0, 5), jr(RA), NOP, jal(0x80300000)]   # ...or only a stray word is
        p = d.port_data_chunk(A, 'main', 0x80000400, 0x80000400, 0x10, hw, sw, shift_map([]))
        self.assertEqual([(x.status, x.problems, x.words[3]) for x in p], [('changed', [], jal(0x80300000))])


class PaddingDataTests(unittest.TestCase):
    STOCK = {0x80000400: addiu(V0, 0, 1), 0x80000404: jr(RA), 0x80000408: NOP}   # then nop padding to 0x20

    def kept(self, hack):
        A, D = fake_main([(0x80000400, 0x20)], self.STOCK, {**self.STOCK, **hack})
        return d.unchanged_code_functions(A, D, [], {})

    def test_data_in_padding_keeps_the_static_function(self):
        # Revenge Starrcade: a table after the function's return, in its nop padding
        self.assertEqual(self.kept({0x80000410: 0x80000410, 0x80000414: 1, 0x80000418: HULK}), [0x1000])

    def test_a_lone_instruction_in_padding_is_a_code_change(self):
        self.assertEqual(self.kept({0x80000414: addu(V1, V0, 0)}), [])

    def test_a_changed_instruction_is_a_code_change(self):
        self.assertEqual(self.kept({0x80000400: addiu(V0, 0, 2), 0x80000418: HULK}), [])

    def test_a_one_word_padding_function_holding_data_is_kept(self):
        # Revenge Starrcade 0x8002E900: a 4-byte all-nop "function"; a table word there decodes as a jal
        A, D = fake_main([(0x80000400, 0x4)], {}, {0x80000400: jal(0x80300000)})
        self.assertEqual(d.unchanged_code_functions(A, D, [], {}), [0x1000])

    def test_a_padding_function_the_code_before_runs_into_is_code(self):
        # VPW Gamma 0x80158984: the hack's code in the function before continues into the one-word stub
        stock = {0x80000400: jr(RA), 0x80000404: NOP}
        A, D = fake_main([(0x80000400, 0x8), (0x80000408, 0x4)], stock,
                         {0x80000400: addiu(V0, 0, 1), 0x80000404: addiu(V1, 0, 2), 0x80000408: lw(A0, 0, V0)})
        self.assertEqual(d.unchanged_code_functions(A, D, [], {}), [])
        # the function before still returns: nothing reaches the stub, so its word is data
        A, D = fake_main([(0x80000400, 0x8), (0x80000408, 0x4)], stock, {**stock, 0x80000408: jal(0x80300000)})
        self.assertEqual(d.unchanged_code_functions(A, D, [], {}), [0x1008])


class DescriptorRomTests(unittest.TestCase):
    def test_overlay_rom_offsets_go_back_to_the_stock_overlays(self):
        # 9 Underground (aki-roadmap#120): rev 1 lists ovl_a at 0xA1CD06; the patched ROM has it at 0xA21750
        import types
        def table(rom_a, rom_b, rom_c, ram):
            return [rom_a, rom_b, 0x80090000, 0x80090000, ram, ram, 0, 0, 0] + [rom_b, rom_c, 0x80090000] + [0] * 6
        base, hack, D = bytearray(0x200), bytearray(0x200), bytearray(0x200)
        put(base, 0x100, table(0xA21750, 0xA69570, 0xAC2970, 0x800C3BC0))
        put(hack, 0x80, table(0xA1CD06, 0xA64C76, 0xABE0A6, 0x800C3CD0))
        put(D, 0x100, table(0xA1CD06, 0xA64C76, 0x00ABE0A6, 0x800C3BC0))   # RAM fields already translated
        A = types.SimpleNamespace(layout={'descriptor_count': 2, 'descriptor_table': 0x100}, base=bytes(base),
                                  hack=bytes(hack), table=0x80)
        self.assertEqual(d.stock_descriptor_rom_fields(A, D), 4)
        self.assertEqual(bytes(D[0x100:0x148]), bytes(base[0x100:0x148]))


class InstructionPatchTests(unittest.TestCase):
    """aki-roadmap#120: 9 Underground changes World Tour's idle thread, so the live-compiled copy needs the port's
    `b .` (0x1000FFFF at 0x8000058C) or its `jal; b` loop never yields."""

    def run_patch(self, status, hack_word):
        stock = {0x80000400: jr(RA), 0x80000410: beq(0, 0, -3)}
        A, D = fake_main([(0x80000400, 0x20)], stock, {0x80000400: addiu(V0, 0, 1), 0x80000410: hack_word})
        A.layout['instruction_patches'] = [[0x80000410, 0x1000FFFF]]
        n = d.port_instruction_patches(A, D, [d.Port('main', 0x80000400, 0x20, None, status, [])])
        return n, struct.unpack('>I', bytes(D[0x1010:0x1014]))[0]

    def test_a_changed_function_gets_the_patch(self):
        self.assertEqual(self.run_patch('changed', beq(0, 0, -3)), (1, 0x1000FFFF))

    def test_an_unchanged_function_or_another_word_is_left_alone(self):
        self.assertEqual(self.run_patch('kept', beq(0, 0, -3)), (0, beq(0, 0, -3)))
        self.assertEqual(self.run_patch('changed', NOP), (0, NOP))


class DisplayListWordTests(unittest.TestCase):
    """aki-roadmap#120: a static display list in overlay data holds physical addresses after its address-taking
    commands (9 Underground's ovl_a G_SETTIMG words kept rev 1's 0xC7B38 for stock's 0xC7A28)."""

    def test_address_commands(self):
        for w0 in (0xFD100000, 0xDE000000, 0x04000000, 0x01000000, 0xDA380003, 0xFE000000):
            self.assertTrue(d.dl_address_word(w0), hex(w0))

    def test_other_words(self):
        for w0 in (0xE7000000, 0xB8000000, 0xF5100000, 0x3C16000C, 0x00000000):
            self.assertFalse(d.dl_address_word(w0), hex(w0))


class PhysicalPairTests(unittest.TestCase):
    """aki-roadmap#120: 9 Underground's title code builds a G_DL target as a physical address,
    `lui s6,0x000C; addiu s6,s6,0x3E08` (rev 1's place of a triangle list in ovl_a data, 0x110 past stock's).
    Left as it was, the display list called texture bytes and the title drew nothing."""
    S6 = 22

    def amap(self):
        m = d.AddressMap()
        m.main = [(0x80000400, 0x80029C50, 0, 'text'),
                  (0x80029C50, 0x80062090, (-0x70) & 0xFFFFFFFF, 'data')]
        m.ovl = {'ovl_a': [(0x80090000, 0x800C3CD0, 0, 'text'),
                           (0x800C3CD0, 0x800D7F30, (-0x110) & 0xFFFFFFFF, 'data')]}
        m.finish()
        return m

    def run_rewrite(self, hi, lo, with_check=True, ctx='ovl_a'):
        m = self.amap()
        cand = lambda a: m.candidates(a, ctx)
        if with_check:
            cand.phys_ok = lambda a: d.phys_data_address(m, a, ctx)
        words = [lui(self.S6, hi), addiu(self.S6, self.S6, lo), jr(RA), NOP]
        return d.rewrite(words, 0x80090160, 0x80090160, cand)

    def test_main_code_keeps_its_low_values(self):
        # main builds ROM offsets into the hack's assets (0x00039500): they read as main-data addresses, never translated
        out = self.run_rewrite(0x0004, 0x9500, ctx=None)
        self.assertEqual((out[0] & 0xFFFF, out[1] & 0xFFFF), (0x0004, 0x9500))

    def test_a_physical_address_into_data_is_translated(self):
        out = self.run_rewrite(0x000C, 0x3E08)
        self.assertEqual(out[0] & 0xFFFF, 0x000C)
        self.assertEqual(out[1] & 0xFFFF, 0x3CF8)

    def test_only_when_the_caller_opts_in(self):
        self.assertEqual(self.run_rewrite(0x000C, 0x3E08, with_check=False)[1] & 0xFFFF, 0x3E08)

    def test_a_number_that_lands_in_text_is_left_alone(self):
        # 0x000A0164 as KSEG0 is ovl_a text: a constant, not a data address
        out = self.run_rewrite(0x000A, 0x0164)
        self.assertEqual((out[0] & 0xFFFF, out[1] & 0xFFFF), (0x000A, 0x0164))


class LikelyDelaySlotTests(unittest.TestCase):
    """aki-roadmap#120: a register write in a branch-likely's delay slot happens only on the taken path, so the
    fall-through %lo still pairs with the lui before it (9 Underground func_80099238 at 0x80099644..58)."""

    def test_bc1tl_slot_write_keeps_the_lui_for_the_fall_through(self):
        S1 = 17
        words = [lui(V0, 0x800E), 0x4502000A, lw(V0, 0, S1), lw(V0, 0x168, V0)]
        self.assertIn((0, 3, 0x800E0168), mips.hilo_refs(words))

    def test_beql_slot_write_keeps_the_lui_but_a_plain_branch_slot_does_not(self):
        S1 = 17
        likely = [lui(V0, 0x800E), (0x14 << 26) | (A0 << 21) | 3, lw(V0, 0, S1), lw(V0, 0x18, V0)]
        self.assertIn((0, 3, 0x800E0018), mips.hilo_refs(likely))
        plain = [lui(V0, 0x800E), beq(A0, 0, 3), lw(V0, 0, S1), lw(V0, 0x18, V0)]
        self.assertEqual(mips.hilo_refs(plain), [])


class PaddingCodeTests(unittest.TestCase):
    """Hack code in a text gap no stock function covers is kept and registered only where the hack's text is
    unshifted; in 9 Underground (rev 1 main shifted by -0x70) the gap is __osSiRawStartDma, which the runtime
    implements natively (aki-roadmap#120)."""

    def pad(self, delta):
        import types
        hack = {0x80000410: addiu(V0, 0, 1), 0x80000414: addiu(V1, 0, 2), 0x80000418: jr(RA), 0x8000041C: NOP}
        A, D = fake_main([(0x80000400, 0x10), (0x80000430, 0x10)], {0x80000410: lw(A0, 0, V0)}, hack)
        A.hack = bytes(D)
        A.place = {'main': (0x1000, 0x80000400)}
        A.map = types.SimpleNamespace(translate=lambda a, ctx=None: (a + delta) & 0xFFFFFFFF)
        return d.padding_code(A, D, 'main', 0x80000410, A.byname['main'].funcs)

    def test_unshifted_text_keeps_the_hack_code(self):
        self.assertEqual(self.pad(0), 0x10)

    def test_shifted_text_leaves_the_gap_to_the_runtime(self):
        self.assertIsNone(self.pad(0x70))


class EntryPassTests(unittest.TestCase):
    def test_tail_call_into_the_middle_of_a_function_gets_an_entry(self):
        hack = {0x80000400: j(0x80000418), 0x80000404: NOP, 0x80000410: addiu(V0, 0, 1),
                0x80000418: addiu(V0, 0, 2), 0x80000428: jr(RA), 0x8000042C: NOP}
        A, D = fake_main([(0x80000400, 0x10), (0x80000410, 0x20)], {0x80000400: jr(RA)}, hack)
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x10, None, 'changed', [])])
        self.assertEqual([(x.vram, x.size) for x in A.extras], [(0x80000418, 0x18)])

    def test_jal_into_the_middle_of_its_own_function_gets_an_entry(self):
        # Giant Series 64: a changed function calls a point inside itself (a call goes through the lookup)
        hack = {0x80000400: jal(0x80000410), 0x80000404: NOP, 0x80000408: jr(RA), 0x8000040C: NOP,
                0x80000410: addiu(V0, 0, 2), 0x80000414: jr(RA), 0x80000418: NOP}
        A, D = fake_main([(0x80000400, 0x20)], {0x80000400: jr(RA)}, hack)
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x20, None, 'changed', [])])
        self.assertEqual([(x.vram, x.size) for x in A.extras], [(0x80000410, 0x10)])

    def test_jump_to_code_in_padding_between_functions_gets_an_entry_in_place(self):
        # VPW64 Deluxe: func_80003E88 jumps to a trampoline in the padding after the entry code, which branches
        # and jumps back into the function: kept at its own address, registered, and its targets too
        hack = {0x80000400: addiu(V0, 0, 1), 0x80000404: j(0x80000428), 0x80000408: NOP,
                0x80000410: addiu(V0, 0, 2), 0x80000414: jr(RA), 0x80000418: NOP,
                0x80000428: beq(17, 0, (0x80000410 - 0x8000042C) >> 2), 0x8000042C: NOP,
                0x80000430: j(0x80000414), 0x80000434: NOP}
        A, D = fake_main([(0x80000400, 0x20), (0x80000440, 0x10)], {0x80000400: jr(RA)}, hack)
        A.place, A.hack = {'main': (0x1000, 0x80000400)}, bytes(D)
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x20, None, 'changed', [])])
        self.assertEqual(sorted((x.vram, x.size) for x in A.extras),
                         [(0x80000410, 0x10), (0x80000414, 0xC), (0x80000428, 0x10)])

    def test_padding_jump_target_the_hack_left_stock_gets_no_entry(self):
        hack = {0x80000400: j(0x80000428), 0x80000404: NOP}
        A, D = fake_main([(0x80000400, 0x20), (0x80000440, 0x10)], {0x80000400: jr(RA)}, hack)
        A.place, A.hack = {'main': (0x1000, 0x80000400)}, bytes(D)
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x20, None, 'changed', [])])
        self.assertEqual(A.extras, [])

    def test_body_with_data_after_its_jump_is_still_scanned(self):
        # VPW64 Deluxe's func_80003E88 keeps a table after its `j`: the code its entry reaches is scanned
        hack = {0x80000400: j(0x80000428), 0x80000404: NOP, 0x80000408: HULK, 0x8000040C: HULK,
                0x80000420: addiu(V0, 0, 2), 0x80000428: jr(RA), 0x8000042C: NOP}
        A, D = fake_main([(0x80000400, 0x10), (0x80000420, 0x10)], {0x80000400: jr(RA)}, hack)
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x10, None, 'changed', [])])
        self.assertEqual([(x.vram, x.size) for x in A.extras], [(0x80000428, 0x8)])

    def test_jump_between_two_functions_of_one_port_gets_an_entry(self):
        # the runtime regenerates stock functions one by one: a port spanning two still needs the entry
        hack = {0x80000400: j(0x80000418), 0x80000404: NOP,
                0x80000410: addiu(V0, 0, 2), 0x80000418: jr(RA), 0x8000041C: NOP}
        A, D = fake_main([(0x80000400, 0x10), (0x80000410, 0x10)], {0x80000400: jr(RA)}, hack)
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x20, None, 'changed', [])])
        self.assertEqual([(x.vram, x.size) for x in A.extras], [(0x80000418, 0x8)])

    def test_function_running_off_its_end_into_padding_gets_an_entry(self):
        # the hack replaced the exit of the first function; it now continues through padding
        stock = {0x80000400: addiu(V0, 0, 1), 0x80000408: jr(RA), 0x8000040C: NOP,
                 0x80000418: addiu(V0, 0, 2), 0x80000420: jr(RA), 0x80000424: NOP}
        hack = dict(stock)
        hack.update({0x80000408: addiu(A0, 0, 4), 0x8000040C: addiu(A1, 0, 2), 0x80000410: addiu(A0, 0, 1)})
        A, D = fake_main([(0x80000400, 0x10), (0x80000418, 0x10)], stock, hack)
        d.add_fall_through_entries(A, D, [])
        self.assertEqual([(x.vram, x.size) for x in A.extras], [(0x80000410, 0x18)])

    def test_blanked_function_gets_no_fall_through_entry(self):
        # the hack nopped a stub out: dead code, nothing runs into what follows
        stock = {0x80000400: addiu(V0, 0, 1), 0x80000408: jr(RA), 0x8000040C: NOP,
                 0x80000418: addiu(V0, 0, 2), 0x80000420: jr(RA), 0x80000424: NOP}
        hack = dict(stock)
        hack.update({0x80000400: NOP, 0x80000408: NOP})
        A, D = fake_main([(0x80000400, 0x10), (0x80000418, 0x10)], stock, hack)
        d.add_fall_through_entries(A, D, [])
        self.assertEqual(A.extras, [])

    def test_function_never_ends_on_a_jump_without_its_slot(self):
        self.assertEqual(d.with_delay_slot(0x4CC, jal(0x801409D4)), 0x4D0)
        self.assertEqual(d.with_delay_slot(0x20, beq(V0, 0, 3)), 0x24)
        self.assertEqual(d.with_delay_slot(0x20, addiu(V0, 0, 1)), 0x20)

    def test_data_kept_as_a_function_is_ignored(self):
        data = {0x80000400 + 4 * i: 0x43414745 for i in range(4)}       # "CAGE" words, not code
        A, D = fake_main([(0x80000400, 0x10), (0x80000418, 0x10)], {}, data)
        d.add_fall_through_entries(A, D, [])
        d.add_branch_out_entries(A, D, [d.Port('main', 0x80000400, 0x10, None, 'changed', [])])
        self.assertEqual(A.extras, [])


def fake_new_area(funcs, words):
    """New-code area holding reshaped functions [(vram, size)], each its own block; words {vram: word}."""
    area = bytearray(0x100)
    for v, w in words.items():
        put(area, v - d.NEW_CODE_VRAM, [w])
    extras = [d.Extra('reshaped', 0x80100000 + (v - d.NEW_CODE_VRAM), v, z, section='ovl_d') for v, z in funcs]
    blocks = [('reshaped', 'ovl_d', x.hack_vram, x.hack_vram + x.size, x.vram) for x in extras]
    return types.SimpleNamespace(extras=extras, new_blocks=blocks), area


class NewAreaEntryTests(unittest.TestCase):
    N = d.NEW_CODE_VRAM

    def test_branch_into_another_chunks_function_gets_an_entry(self):
        # VPW64 Deluxe v1.1: the hook at 0x8F0011B0 branches to the shared `j` back at 0x8F001194, inside 0x8F001150
        N = self.N
        A, area = fake_new_area([(N, 0x10), (N + 0x10, 0x10)],
                                {N: addiu(V0, 0, 1), N + 4: addiu(29, 29, -0x28), N + 8: j(0x80140BC8), N + 0xC: NOP,
                                 N + 0x10: beq(V0, 0, -4), N + 0x14: NOP, N + 0x18: jr(RA), N + 0x1C: NOP})
        report = {}
        d.add_new_area_entries(A, bytearray(), area, [], report)
        self.assertEqual([(x.kind, x.vram, x.size, x.hack_vram) for x in A.extras[2:]],
                         [('reshaped', N + 4, 0xC, 0x80100004)])
        self.assertEqual(report['new_area_entries'], [(N + 0x10, N + 4)])

    def test_jump_from_a_port_into_a_chunk_nothing_entered_gets_an_entry(self):
        # VPW64 Deluxe v1.1: main's hook at 0x80010ADC does `j 0x800F2ADC`, into ovl_a's chunk reshaped at 0x800F2A40;
        # the rewrite points it at the moved chunk, where no function was registered (crash at 0x8F0007CC)
        N = self.N
        A, D = fake_main([(0x80000400, 0x10)], {}, {0x80000400: j(N + 0x24), 0x80000404: NOP})
        A2, area = fake_new_area([(N, 0x10)], {N + 0x24: addiu(V0, 0, 1), N + 0x28: jr(RA), N + 0x2C: NOP})
        A.extras, A.new_blocks = A2.extras, [('reshaped', 'ovl_a', 0x800F2A40, 0x800F2A80, N)]
        report = {}
        d.add_new_area_entries(A, D, area, [d.Port('main', 0x80000400, 0x10, None, 'changed', [])], report)
        self.assertEqual([(x.kind, x.vram, x.size, x.hack_vram, x.section) for x in A.extras[1:]],
                         [('reshaped', N + 0x24, 0xC, 0x800F2A64, 'ovl_a')])
        self.assertEqual(report['new_area_entries'], [(0x80000400, N + 0x24)])

    def test_loop_in_its_own_block_and_known_starts_add_nothing(self):
        N = self.N
        A, area = fake_new_area([(N, 0x10), (N + 0x10, 0x10)],
                                {N: beq(V0, 0, 2), N + 8: jr(RA),
                                 N + 0x10: beq(V0, 0, -5), N + 0x18: jr(RA)})    # N + 0x10 -> N: a function start
        # two functions in one block: the second loops back above its entry (left to the back-branch copy)
        A.extras.append(d.Extra('reshaped', 0x80100028, N + 0x28, 0x10, section='ovl_d'))
        A.new_blocks[1] = ('reshaped', 'ovl_d', 0x80100010, 0x80100040, N + 0x10)
        put(area, 0x28, [beq(V0, 0, -6), NOP, jr(RA), NOP])     # N + 0x28 -> N + 0x14, inside the block, above the entry
        d.add_new_area_entries(A, bytearray(), area, [], {})
        self.assertEqual(len(A.extras), 3)


class _ChainMap:
    """The address map discover_extra_code reads: identity, no data ranges."""
    def __init__(self, names):
        self.main, self.ovl, self.priority = [], {n: [] for n in names}, []

    def translate(self, a):
        return a

    def finish(self):
        pass


def fake_hook_chain(extra_main=(), extra_ovl_b=()):
    """VPW64 Deluxe v1.1's match-load chain in miniature (jessetbh/aki-roadmap#222), hack layout = stock layout:
    ovl_b's changed function at 0x80100000 does `j 0x80000480` (a hook in main's text) at 0x80100010; that hook does
    `j 0x80100024`, into ovl_a's reshaped chunk 0x80100020-0x80100040; that does `j 0x80100054`, into ovl_a's reshaped
    chunk 0x80100050-0x80100070, which comes back with `j 0x80100018` into ovl_b's function."""
    hack = bytearray(0x5000)
    secs = [d.Section('main', 0x1000, 0x80000400, 0x80000800, 0x80000900, 0x80000900, [(0x80, 0x10)]),
            d.Section('ovl_a', 0x2000, 0x80100000, 0x80100100, 0x80100100, 0x80100100, [(0, 0x20), (0x20, 0x20), (0x50, 0x20)], slot=0x80100000),
            d.Section('ovl_b', 0x3000, 0x80100000, 0x80100100, 0x80100100, 0x80100100, [(0, 0x40)], slot=0x80100000)]
    by = {s.name: s for s in secs}
    words = {('main', 0x80000480): [j(0x80100024), NOP] + list(extra_main),
             ('ovl_a', 0x80100024): [addiu(V0, 0, 1), j(0x80100054), NOP],
             ('ovl_a', 0x80100054): [addiu(V1, 0, 2), j(0x80100018), NOP],
             ('ovl_b', 0x80100000): [addiu(29, 29, -0x18), NOP, NOP, NOP, j(0x80000480), NOP,
                                     addiu(V0, 0, 3), addiu(29, 29, 0x18), jr(RA), NOP] + list(extra_ovl_b)}
    for (name, v), ws in words.items():
        put(hack, by[name].rom_of(v), ws)
    A = types.SimpleNamespace(
        base=bytes(hack), hack=bytes(hack), secs=secs, byname=by, layout={'extra_code': []},
        place={s.name: (s.rom, s.text, s.data, s.data, s.bss) for s in secs},
        reshaped=[('ovl_a', 0x80100020, 0x80100040, 0x80100020, 0x80100040),
                  ('ovl_a', 0x80100050, 0x80100070, 0x80100050, 0x80100070)],
        units={'main': [(0x80000480, 0x80000480, 0x10)], 'ovl_b': [(0x80100000, 0x80100000, 0x40)]},
        map=_ChainMap(['ovl_a', 'ovl_b']))
    A.hack_rom_of = lambda name, v: by[name].rom_of(v)
    return A


class HookChainTests(unittest.TestCase):
    def test_main_jump_into_an_overlay_chunk_enters_it(self):
        # main's hook jumps into ovl_a's reshaped chunk: the target is an entry of the chunk, so its code is traced and
        # rewritten (before: copied raw, its `j 0x800FC29C` reached the stock function there)
        extras = d.discover_extra_code(fake_hook_chain())
        got = {(x.kind, x.hack_vram, x.section) for x in extras if x.kind == 'reshaped'}
        self.assertIn(('reshaped', 0x80100024, 'ovl_a'), got)
        self.assertIn(('reshaped', 0x80100054, 'ovl_a'), got)

    def test_return_point_behind_a_main_hook_gets_its_function(self):
        # the chain back runs through main's text, which reach doesn't follow: the `j` out at 0x80100010 owns
        # 0x80100018 (before: 'mid-unowned', no entry, and the hook's return ended the main thread)
        extras = d.discover_extra_code(fake_hook_chain())
        mids = [(x.hack_vram, x.vram, x.size, x.section, x.problems) for x in extras if x.kind == 'mid']
        self.assertEqual(mids, [(0x80100018, 0x80100018, 0x28, 'ovl_b', [])])

    def test_main_jump_back_to_another_overlays_hook_site_is_no_entry(self):
        # ovl_b's function hooks out right before 0x80100024 (`j` at 0x8010001C): main's `j 0x80100024` is that hook
        # coming back, not an entry of ovl_a's chunk (No Mercy Plus V3 / BTBW: caves return into ovl_b/c/e where ovl_d
        # has a reshaped chunk; those return points stay mid entries of their own functions)
        A = fake_hook_chain()
        hack = bytearray(A.hack)
        put(hack, A.byname['ovl_b'].rom_of(0x8010001C), [j(0x80000490)])
        A.hack = bytes(hack)
        extras = d.discover_extra_code(A)
        self.assertNotIn(0x80100024, {x.hack_vram for x in extras if x.kind == 'reshaped'})

    def test_a_local_jump_before_the_point_owns_nothing(self):
        # `j` at t - 8 that stays inside its own function (a branch around code, VPW64 Deluxe's 0x8014267C) is no hook
        A = fake_hook_chain()
        hack = bytearray(A.hack)
        put(hack, A.byname['ovl_b'].rom_of(0x80100010), [j(0x80100030)])
        A.hack = bytes(hack)
        mids = [(x.hack_vram, x.section, x.problems) for x in d.discover_extra_code(A) if x.kind == 'mid']
        self.assertEqual(mids, [(0x80100018, None, [('mid-unowned', 0x80100018)])])


class ViHookTests(unittest.TestCase):
    """The hack hooks the VI manager thread the port replaces: the hook has to run from the retrace thread."""
    THREAD, SITE, BACK, CAVE = 0x80000440, 0x80000410, 0x80000450, d.NEW_CODE_VRAM
    RECV = 0x80000480

    def build(self, site_word=None, back=True, hook=None, layout=None):
        stock = {self.THREAD + 0x10: j(self.BACK), self.SITE: jal(self.RECV)}
        hack = {self.THREAD + 0x10: j(hook or self.CAVE), self.SITE: site_word if site_word is not None else jal(self.RECV)}
        A, D = fake_main([(0x80000400, 0x40), (self.THREAD, 0x20)], stock, hack)
        A.layout.update(dict(vi_thread=[self.THREAD, 0x20], retrace_site=self.SITE) if layout is None else layout)
        A.new_blocks = []
        area = bytearray()
        for w in [addiu(T0, 0, 1), j(self.BACK) if back else jr(RA), NOP]:
            area.extend(w.to_bytes(4, 'big'))
        A.extras.append(d.Extra('cave', 0x80300000, self.CAVE, len(area)))
        return A, D, area

    def run_it(self, *a, **k):
        A, D, area = self.build(*a, **k)
        report = {}
        d.reroute_vi_hook(A, D, area, report)
        return A, D, area, report

    def test_the_retrace_wait_runs_the_hook_first_and_still_waits(self):
        A, D, area, report = self.run_it()
        stub = report['vi_hook']['stub']
        self.assertEqual(word_at(D, self.SITE), jal(stub))
        words = [int.from_bytes(area[stub - self.CAVE + 4 * i:stub - self.CAVE + 4 * i + 4], 'big')
                 for i in range((len(area) - (stub - self.CAVE)) // 4)]
        self.assertLess(words.index(jal(self.CAVE)), words.index(jal(self.RECV)))
        self.assertEqual(words[-2:], [jr(RA), addiu(29, 29, 0x48)])     # the frame comes back off in the delay slot
        self.assertEqual(words[0], addiu(29, 29, -0x48))
        self.assertEqual(A.extras[-1].vram, stub)
        self.assertEqual(A.extras[-1].size, 4 * len(words))

    def test_stub_keeps_the_arguments_and_saved_registers_across_the_hook(self):
        A, D, area, report = self.run_it()
        stub = report['vi_hook']['stub']
        words = [int.from_bytes(area[stub - self.CAVE + 4 * i:stub - self.CAVE + 4 * i + 4], 'big')
                 for i in range((len(area) - (stub - self.CAVE)) // 4)]
        at = words.index(jal(self.CAVE))
        keep = set(range(4, 8)) | set(range(16, 24))
        stored = {(w >> 16) & 31: w & 0xFFFF for w in words[:at] if w >> 26 == 0x2B and (w >> 21) & 31 == 29}
        loaded = {(w >> 16) & 31: w & 0xFFFF for w in words[at:words.index(jal(self.RECV))] if w >> 26 == 0x23}
        self.assertEqual(set(loaded), keep)
        self.assertEqual({r: stored[r] for r in keep}, loaded)

    def test_the_way_back_into_the_vi_loop_becomes_a_return(self):
        A, D, area, report = self.run_it()
        self.assertEqual(int.from_bytes(area[4:8], 'big'), jr(RA))
        self.assertEqual(int.from_bytes(area[0:4], 'big'), addiu(T0, 0, 1))
        self.assertEqual(report['vi_hook']['returns'], 1)

    def test_overlapping_cave_entries_rewrite_one_word_once(self):
        A, D, area = self.build()
        A.extras.append(d.Extra('cave', 0x80300004, self.CAVE + 4, 8))
        report = {}
        d.reroute_vi_hook(A, D, area, report)
        self.assertEqual(report['vi_hook']['returns'], 1)

    def test_overlapping_cave_entries_rewrite_one_word_once(self):
        A, D, area = self.build()
        A.extras.append(d.Extra('cave', 0x80300004, self.CAVE + 4, 8))
        report = {}
        d.reroute_vi_hook(A, D, area, report)
        self.assertEqual(report['vi_hook']['returns'], 1)

    def test_game_with_no_known_site_warns_and_changes_nothing(self):
        A, D, area, report = self.run_it(layout=dict(vi_thread=[self.THREAD, 0x20], retrace_site=None))
        self.assertIn('retrace_site', report['vi_hook_unhandled'])
        self.assertEqual(word_at(D, self.SITE), jal(self.RECV))
        self.assertEqual(int.from_bytes(area[4:8], 'big'), j(self.BACK))

    def test_a_hacked_site_is_left_alone(self):
        A, D, area, report = self.run_it(site_word=jal(0x80000490))
        self.assertIn('not the stock call', report['vi_hook_unhandled'])
        self.assertEqual(word_at(D, self.SITE), jal(0x80000490))

    def test_a_hook_that_never_comes_back_is_not_rerouted(self):
        A, D, area, report = self.run_it(back=False)
        self.assertIn('no jump back', report['vi_hook_unhandled'])
        self.assertEqual(word_at(D, self.SITE), jal(self.RECV))

    def test_a_jump_inside_the_vi_loop_is_not_a_hook(self):
        A, D, area, report = self.run_it(hook=self.THREAD + 8)
        self.assertEqual(report, {})
        self.assertEqual(word_at(D, self.SITE), jal(self.RECV))

    def test_layout_without_the_thread_does_nothing(self):
        A, D, area = self.build()
        del A.layout['vi_thread']
        report = {}
        d.reroute_vi_hook(A, D, area, report)
        self.assertEqual(report, {})

    def test_no_mercy_layout_points_at_its_threads(self):
        with open(os.path.join(d.LAYOUTS_DIR, 'nomercy.json')) as f:
            layout = json.load(f)
        self.assertEqual(layout['vi_thread'], [0x80038050, 0x178])
        self.assertEqual(layout['retrace_site'], 0x80000E7C)


def word_at(D, v):
    return int.from_bytes(D[0x1000 + (v - 0x80000400):0x1000 + (v - 0x80000400) + 4], 'big')


class JumpTableCheckTests(unittest.TestCase):
    def test_table_outside_function_is_flagged(self):
        rom = bytearray(0x200)
        h, l = hi_lo(0x80000100)
        code = [lui(AT, h), addu(AT, AT, V0), lw(V0, l, AT), jr(V0), NOP, jr(RA), NOP]
        put(rom, 0x0, code)
        put(rom, 0x100, [0x80000014, 0x80000014])                         # good: inside the function
        self.assertEqual(d.check_jump_tables(bytes(rom), [(0x80000000, 0x0, 4 * len(code))], [], []), [])
        put(rom, 0x100, [0x80400000, 0x80000014])                         # first case in hack code...
        self.assertEqual(len(d.check_jump_tables(bytes(rom), [(0x80000000, 0x0, 4 * len(code))], [], [])), 1)
        # ...which is fine once the hack's code range is declared
        self.assertEqual(d.check_jump_tables(bytes(rom), [(0x80000000, 0x0, 4 * len(code))],
                                             [(0x80400000, 0x80500000)], []), [])

    def test_jump_through_a_pointer_variable_is_not_a_table(self):
        # lui + lw %lo + jr with no index added: a function pointer, which the runtime calls through the lookup
        rom = bytearray(0x200)
        h, l = hi_lo(0x80000100)
        code = [lui(AT, h), lw(V0, l, AT), jr(V0), NOP, jr(RA), NOP]
        put(rom, 0x0, code)
        put(rom, 0x100, [0x80400000])
        self.assertEqual(d.check_jump_tables(bytes(rom), [(0x80000000, 0x0, 4 * len(code))], [], []), [])

    def test_load_overwritten_before_the_jr_is_not_its_table(self):
        # an indexed array load into v0, then v0 is recomputed from another base before the jr
        rom = bytearray(0x200)
        h, l = hi_lo(0x80000100)
        A2, T3 = 6, 11
        code = [lui(V0, h), addu(V0, V0, A2), lw(V0, l, V0), addu(V0, V1, T3), lw(V0, 0, V0), jr(V0), NOP]
        put(rom, 0x0, code)
        put(rom, 0x100, [0x80400000])
        self.assertEqual(d.check_jump_tables(bytes(rom), [(0x80000000, 0x0, 4 * len(code))], [], []), [])


class PayloadLoaderTests(unittest.TestCase):
    def test_copy_loop_hook_later_in_the_entry_code(self):
        # Revenge Starrcade 2.0: entry word 10 is `j cave`; the cave copies 1 MB from the cart to 0x80400000
        T0, T1, T2, T3 = 8, 9, 10, 11
        rom = bytearray(0x40000)
        put(rom, 0x1000, [NOP] * 10 + [j(0x80010000), NOP])
        h, l = hi_lo(0xB00DAC60)
        cave = [lui(T0, h), addiu(T0, T0, l), lui(T1, 0x10), lui(T2, 0x8040), lw(T3, 0, T0),
                (0x2B << 26) | (T2 << 21) | (T3 << 16), addiu(T0, T0, 4), addiu(T2, T2, 4), addiu(T1, T1, -4),
                (0x05 << 26) | (T1 << 21) | (-6 & 0xFFFF), NOP, j(0x80000430), NOP]
        put(rom, 0x1000 + (0x80010000 - 0x80000400), cave)
        p = d.find_payload(bytes(rom))
        self.assertEqual((p['rom'], p['ram'], p['size'], p['zero_lo'], p['cave']), (0xDAC60, 0x80400000, 0x100000, None, 0x80010000))


class StockCodeBlockTests(unittest.TestCase):
    """aki-roadmap#120: World Tour keeps its overlays near the ROM end; the hack's assets before them must stay."""

    def sec(self, name, rom, size):
        return d.Section(name, rom, 0x80000000, 0x80000000, 0x80000000 + size, 0x80000000 + size)

    def test_contiguous_sections_are_one_block_from_zero(self):
        secs = [self.sec('entry', 0x1000, 0x50), self.sec('main', 0x1050, 0x38000),
                self.sec('ovl_a', 0x39100, 0x1000)]
        self.assertEqual(d.stock_code_blocks(secs), [(0, 0x3A100)])

    def test_far_overlays_leave_the_assets_between(self):
        secs = [self.sec('entry', 0x1000, 0x50), self.sec('main', 0x1050, 0x383A0),
                self.sec('ovl_a', 0xA21750, 0x47E20), self.sec('ovl_b', 0xA69570, 0x59400)]
        self.assertEqual(d.stock_code_blocks(secs), [(0, 0x393F0), (0xA21750, 0xAC2970)])
        self.assertEqual(d.between_blocks(d.stock_code_blocks(secs), 0xC00000),
                         [(0x393F0, 0xA21750), (0xAC2970, 0xC00000)])

    def test_rom_offsets_into_the_hack_assets_are_not_read_as_physical_ram(self):
        m = d.AddressMap()
        m.main = [(0x80000450, 0x80040000, (-0x70) & 0xFFFFFFFF, 'data')]
        m.finish()
        self.assertEqual(m.candidates(0x00039500), [0x00039490])    # no asset area: physical RAM reading
        m.assets = [(0x393F0, 0xA21750)]
        self.assertEqual(m.candidates(0x00039500), [])              # 9 Underground's ROM offset stays
        self.assertEqual(m.candidates(0x80039500), [0x80039490])    # RAM addresses still translate
        m.rom = [(0x39400, 0x39460, (-0x70) & 0xFFFFFFFF)]
        self.assertIn(0x00039390, m.candidates(0x00039400))         # main's own ROM data still translates


class BpsTests(unittest.TestCase):
    def test_round_trip_with_moved_data(self):
        src = bytes(range(256)) * 64
        tgt = src[:1000] + b'new data' + src[3000:9000] + src[100:2000] + bytes(500)
        patch = bps.bps_encode(src, tgt)
        self.assertEqual(bps.bps_apply(src, patch), tgt)


if __name__ == '__main__':
    unittest.main()
