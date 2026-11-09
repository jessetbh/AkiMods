"""Translate the addresses inside the code a GameShark list writes (aki-roadmap#205).

translate_codes moves where a code writes. The values stay, and some of them are instructions holding addresses of the
revision the list was written for: a hook's `j`, the way back from a cave, a lui/low pair reading a table. This takes a
list whose write addresses are already at the port's revision and fixes those values, through the same revision map.

A value is only touched where it's known to be an instruction, because most written words are textures, tables and
text, and plenty of those look like jumps:
  - A whole word written on a game instruction (inside a section's text).
  - The low half of a game `j`/`jal`, whose upper half is the port ROM's.
  - A written word reached by following control flow from those hooks ("cave" code). A hook also counts when it's built
    with lui + low + jr. Code nothing reaches this way stays as it is and shows up in the report's `unreached_jumps`.
  - A whole word outside game text that is a pointer to something the list wrote: its target isn't written, and the
    same target through the map is. Other pointer-looking words are colours and the like, and stay.

Whether a word in overlay text really is that overlay's instruction depends on what's loaded: a list guards that with a
condition on a known halfword, so a condition on text narrows which overlays the writes under it are for.

What's left alone and reported, so a person can look: an address the map can't place (`unmapped`), a lui whose uses
would need different upper halves (`lui_conflicts`), a lui whose register is stepped as a pointer and whose target
moved (`stepped_bases`): the loop reads through the register with its own offsets, so the new address would have to fit
in the lui alone. Move what it points at with --move-writes instead. A low half written on a game load or store is not
rewritten: the value alone can't tell an address from a plain number (`half_immediates`).
"""
import collections
import re

HEADER = '# code values translated by aki-conv (aki-roadmap#205)'
RAM_END = 0x800000
LINE = re.compile(r'^(8[01]|A[01]|D[0-3]|50)([0-9A-Fa-f]{6})\s+([0-9A-Fa-f]{4})(.*)$')

J, JAL = 2, 3
BEQ, BNE, BLEZ, BGTZ, ADDIU, ADDI, ORI, LUI = 4, 5, 6, 7, 9, 8, 13, 15
BRANCHES = {BEQ, BNE, BLEZ, BGTZ, 20, 21, 22, 23}          # and the likely forms
LOADS_STORES = set(range(0x20, 0x28)) | set(range(0x28, 0x2C)) | {0x2E, 0x31, 0x35, 0x39, 0x3D}
FP_LOADS = {0x31, 0x35}                                    # rt is a float register: no GPR changes
STORES = set(range(0x28, 0x2C)) | {0x2E, 0x39, 0x3D}


def _rs(w): return (w >> 21) & 31
def _rt(w): return (w >> 16) & 31
def _rd(w): return (w >> 11) & 31
def _sx(v): return v - 0x10000 if v & 0x8000 else v


def jump_target(w):
    """The KSEG0 address a j/jal goes to, or None when it isn't one or points past RAM."""
    if w >> 26 not in (J, JAL):
        return None
    t = (w & 0x3FFFFFF) << 2
    return 0x80000000 | t if t < RAM_END else None


def with_target(w, target):
    return (w & 0xFC000000) | ((target & 0x0FFFFFFF) >> 2)


def branch_target(w, pc):
    op = w >> 26
    if op in BRANCHES or (op == 1 and _rt(w) in (0, 1, 2, 3, 16, 17, 18, 19)):
        return (pc + 4 + (_sx(w & 0xFFFF) << 2)) & 0xFFFFFFFF
    return None


def gpr_written(w):
    """The general register an instruction writes, or None."""
    op = w >> 26
    if op == 0:
        return _rd(w) if (w & 0x3F) not in (8, 12, 13, 24, 25, 26, 27, 28, 29, 30, 31, 17, 19) else None
    if op == JAL:
        return 31
    if op == 1:
        return 31 if _rt(w) in (16, 17, 18, 19) else None
    if op in (J, 16, 17, 18, 19):
        return (_rt(w) if op in (16, 17, 18) and _rs(w) in (0, 1) else None)
    if op in BRANCHES or op in STORES or op in FP_LOADS:
        return None
    return _rt(w)


class HiLo:
    """Follows lui through a run of instructions and records its uses: {lui pc: [(low pc, address, kind)]}.
    `addu`/`add` with an index keeps the upper half live in the result, the way hand-written table reads do it
    (the ropes hook: lui at; addu at, v1, at; lbu v1, 0x2900(at))."""

    def __init__(self):
        self.reg = {}
        self.uses = collections.defaultdict(list)
        self.luis = {}
        self.stepped = set()

    def step(self, pc, w):
        op = w >> 26
        if op == LUI:
            self.luis[pc] = w & 0xFFFF
            self.reg[_rt(w)] = pc
            return
        lui = None
        if op in (ADDIU, ADDI, ORI) or op in LOADS_STORES:
            lui = self.reg.get(_rs(w))
        if lui is not None and op in (ADDIU, ADDI) and _rt(w) == _rs(w) and self.uses.get(lui):
            self.stepped.add(lui)
            lui = None     # the register already read through: this steps a pointer (addiu t2, t2, 8), it's no low half
        if lui is not None:
            hi, lo = self.luis[lui], w & 0xFFFF
            addr = ((hi << 16) | lo) if op == ORI else ((hi << 16) + _sx(lo)) & 0xFFFFFFFF
            self.uses[lui].append((pc, addr, 'ori' if op == ORI else 'low'))
        if op == 0 and (w & 0x3F) in (0x20, 0x21) and (_rs(w) in self.reg) != (_rt(w) in self.reg):
            self.reg[_rd(w)] = self.reg[_rs(w) if _rs(w) in self.reg else _rt(w)]
            return
        d = gpr_written(w)
        if d:
            self.reg.pop(d, None)

    def forget_call(self):
        self.reg.clear()


class Mapper:
    """Rev address -> port address. `ranges` is the plain map; an address it can't place (overlays moved differently) is
    tried in `overlays`, limited to the overlays in context, and only counts if they all agree."""

    def __init__(self, ranges, overlays=None):
        self.ranges, self.overlays = ranges, overlays or {}

    def map(self, addr, ctx=None):
        if not 0x80000000 <= addr < 0x80000000 + RAM_END:
            return None
        off = addr & 0xFFFFFF
        for lo, hi, d in self.ranges:
            if lo <= off < hi:
                return 0x80000000 | (off + d)
        got = set()
        for name in (ctx if ctx else self.overlays):
            for lo, hi, d in self.overlays.get(name, ()):
                if lo <= off < hi:
                    got.add(off + d)
                    break
        return 0x80000000 | got.pop() if len(got) == 1 else None


class Layout:
    """What's where at the port's revision, from derelink's layout JSON and the port's ROM."""

    def __init__(self, layout, port_rom):
        self.secs = layout['sections']
        self.rom = port_rom

    def covering(self, addr):
        return [s for s in self.secs if s['text'] <= addr < s['data']]

    def is_text(self, addr):
        return bool(self.covering(addr))

    def rom_word(self, sec, addr):
        off = sec['rom'] + (addr - sec['text'])
        return int.from_bytes(self.rom[off:off + 4], 'big') if off + 4 <= len(self.rom) else None

    def overlays_for(self, addr, conds):
        """The overlay names a write at `addr` under these conditions can be for, or None when that doesn't narrow it."""
        return self.context(addr, conds)[0]

    def overlap(self, a, b):
        """Whether two overlays share RAM (text to the end of bss), so they're never loaded at the same time."""
        sa = [s for s in self.secs if s['name'] == a]
        sb = [s for s in self.secs if s['name'] == b]
        if not sa or not sb:
            return False
        sa, sb = sa[0], sb[0]
        return sa['text'] < sb.get('end', sb['data']) and sb['text'] < sa.get('end', sa['data'])

    def context(self, addr, conds, written=()):
        """(overlay names as overlays_for gives them, live) for a write at `addr` under these D1 conditions.

        A condition on overlay text tells which overlay is loaded: the ones whose ROM holds the halfword (`match`).
        That rules out the others covering the condition address and every overlay that shares RAM with all of the
        matching ones; overlays elsewhere stay (a guard on ovl_b's text says nothing against ovl_a, loaded with it).
        `live` is False when the port's ROM contradicts the conditions: no overlay holds a condition's halfword (and
        the list doesn't write it itself, `written`), or none of the overlays at `addr` can be loaded under them.
        Such a write never runs on the stock code; its names are then what the conditions that do match say, as
        before aki-roadmap#246, and translate_values only follows a cave from it when nothing live reaches it."""
        slot_names = [s['name'] for s in self.secs if s.get('slot') is not None]
        own = {s['name'] for s in self.covering(addr) if s.get('slot') is not None}
        names, live = set(own), True
        for ca, cv in conds:
            secs = [s for s in self.secs if s.get('slot') is not None and s['text'] <= ca < s['data']]
            if not secs:
                continue
            match = set()
            for s in secs:
                w = self.rom_word(s, ca & ~3)
                if w is not None and ((w >> 16) if not ca & 2 else w & 0xFFFF) == cv:
                    match.add(s['name'])
            if not match:
                if ca not in written:
                    live = False
                continue
            if not own:
                names = (names & match) or match
                continue
            out = {s['name'] for s in secs} - match
            out |= {n for n in slot_names if n not in match and all(self.overlap(n, m) for m in match)}
            if names - out:
                names -= out
            else:
                live = False
                names = (names & match) or match
        return sorted(names) or None, live


def parse(text):
    """-> (lines, writes): writes are (line index, address, value, group, conditions) for 16-bit writes, in list order.
    The conditions are the D1 (equal) conditions right before the write."""
    lines, writes, group, pending = text.split('\n'), [], '', []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('['):
            group = s
            continue
        m = LINE.match(s)
        if not m:
            continue
        kind = m[1].upper()
        addr = 0x80000000 | int(m[2], 16)
        val = int(m[3], 16)
        if kind[0] == 'D':
            if kind == 'D1':
                pending.append((addr, val))
            continue
        if kind in ('81', 'A1'):
            writes.append((i, addr, val, group, tuple(pending)))
        if kind != '50':
            pending = []
    return lines, writes


def set_value(lines, index, value):
    m = LINE.match(lines[index].strip())
    lines[index] = '%s%s %04X%s' % (m[1].upper(), m[2].upper(), value, m[4])


def translate_values(text, mapper, layout):
    """(new text, report). Addresses in `text` are already the port's; instruction values are still the old revision's."""
    lines, writes = parse(text)
    report = {'jumps': 0, 'low_halves': 0, 'hilo_pairs': 0, 'unmapped': [], 'lui_conflicts': [], 'half_immediates': 0,
              'cave_words': 0, 'unreached_jumps': 0, 'hooks_into_caves': 0, 'pointers': 0, 'stepped_bases': []}
    # Every written value, by address: a word in a cave can come from any group ("Part 1", "Part 2").
    at = collections.defaultdict(list)
    for w in writes:
        at[w[1]].append(w)
    stock_runs = collections.defaultdict(dict)
    for w in writes:
        if layout.is_text(w[1] & ~3):
            stock_runs[(w[3], w[4])][w[1]] = w
    changes, roots = {}, []

    def cave_word(a):
        if layout.is_text(a) or a not in at or a + 2 not in at:
            return None
        return (at[a][0][2] << 16) | at[a + 2][0][2]

    def change_half(entry, new):
        if new != entry[2]:
            changes[entry[0]] = new

    def change_cave(a, new):
        # The same word can be written by several groups: all of them, as long as they agree with what was followed.
        for e in at[a]:
            if e[2] == at[a][0][2]:
                change_half(e, new)

    def fix_jump(hi, lo, w, ctx, where):
        t = jump_target(w)
        n = mapper.map(t, ctx)
        if n is None:
            report['unmapped'].append('j at %08X -> %08X' % (where, t))
            return
        report['jumps'] += 1
        nw = with_target(w, n)
        change_half(hi, nw >> 16)
        change_half(lo, nw & 0xFFFF)

    def resolve(hilo, ctx):
        for lui_pc, uses in hilo.uses.items():
            news = {}
            for pc, addr, kind in uses:
                n = mapper.map(addr, ctx)
                if n is None:
                    if 0x80000000 <= addr < 0x80000000 + RAM_END:
                        report['unmapped'].append('lui/low at %08X -> %08X' % (pc, addr))
                    news = None
                    break
                news[pc] = (n, kind)
            if not news:
                continue
            if lui_pc in hilo.stepped:
                if any(n != addr for (pc, addr, _), (n, _) in zip(uses, news.values())):
                    report['stepped_bases'].append('%08X' % lui_pc)
                continue
            his = {(n >> 16) if k == 'ori' else ((n + 0x8000) >> 16) & 0xFFFF for n, k in news.values()}
            if len(his) != 1:
                report['lui_conflicts'].append('%08X' % lui_pc)
                continue
            yield lui_pc, his.pop(), {pc: n & 0xFFFF for pc, (n, _) in news.items()}

    def flush(hilo, run, ctx):
        for lui_pc, hi, los in resolve(hilo, ctx):
            report['hilo_pairs'] += 1
            change_half(run[lui_pc + 2], hi)
            for pc, lo in los.items():
                change_half(run[pc + 2], lo)

    # 1. Game instructions: runs of whole words in text, and low halves of game jumps.
    for (group, conds), run in stock_runs.items():
        hilo, prev, ctx = HiLo(), None, None
        for a in sorted(run):
            if a & 2:
                if a - 2 not in run:
                    ctx, live = layout.context(a, conds, at)
                    secs = [s for s in layout.covering(a & ~3) if not ctx or s['name'] in ctx or s.get('slot') is None]
                    bases = {s['name']: layout.rom_word(s, a & ~3) for s in secs}
                    jumps = {name: b for name, b in bases.items() if b is not None and b >> 26 in (J, JAL)}
                    if jumps and len(jumps) == len(bases):
                        # Each overlay at this address has its own upper half, so its own target.
                        placed = set()
                        for name, b in jumps.items():
                            w = (b & 0xFFFF0000) | run[a][2]
                            t = jump_target(w)
                            n = mapper.map(t, [name]) if t is not None else None
                            # The port's upper half has to hold the new target too, or this needs a word write.
                            if (n is not None and with_target(w, n) >> 16 == w >> 16
                                    and (layout.is_text(n) or cave_word(n) is not None)):
                                placed.add(n)
                        if len(placed) == 1:
                            n = placed.pop()
                            change_half(run[a], (n >> 2) & 0xFFFF)
                            report['low_halves'] += 1
                            if cave_word(n) is not None:
                                roots.append((n, ctx, live))
                                report['hooks_into_caves'] += 1
                        else:
                            report['unmapped'].append('low half at %08X = %04X' % (a, run[a][2]))
                    elif any(b is not None and (b >> 26 in LOADS_STORES or b >> 26 in (ADDIU, ORI, LUI)) for b in bases.values()):
                        report['half_immediates'] += 1
                continue
            if a + 2 not in run:
                continue
            if prev is not None and a != prev + 4:
                flush(hilo, run, ctx)
                hilo = HiLo()
            prev = a
            w = (run[a][2] << 16) | run[a + 2][2]
            ctx, live = layout.context(a, conds, at)
            hilo.step(a, w)
            t = jump_target(w)
            if t is not None:
                n = mapper.map(t, ctx)
                if n is None or not (layout.is_text(n) or cave_word(n) is not None or n >= 0x80000000 + RAM_END):
                    # a data-looking word in text, or a target nothing can place: leave it, and say so
                    report['unmapped'].append('j at %08X -> %08X' % (a, t))
                else:
                    fix_jump(run[a], run[a + 2], w, ctx, a)
                    if cave_word(n) is not None:
                        roots.append((n, ctx, live))
                        report['hooks_into_caves'] += 1
            if w >> 26 == 0 and (w & 0x3F) == 8:    # jr: a hook built with lui + low + jr
                reg = _rs(w)
                lui = hilo.reg.get(reg)
                if lui is not None and hilo.uses.get(lui):
                    n = mapper.map(hilo.uses[lui][-1][1], ctx)
                    if n is not None and cave_word(n) is not None:
                        roots.append((n, ctx, live))
                        report['hooks_into_caves'] += 1
        flush(hilo, run, ctx)

    # 2. Cave code: follow control flow from the hooks. The roots are port addresses, and so are the cave's own places;
    # the jumps and branches inside still hold old-revision addresses, which is why they're mapped before following.
    # A cave reached from several hooks is followed once, in the overlays of all of them (the mapper then needs them to
    # agree), not in whichever hook came last. Hooks whose conditions the ROM contradicts never run on the stock code
    # (a version for the wrong overlay, aki-roadmap#246): they only count when nothing else reaches the cave.
    by_entry = collections.defaultdict(lambda: ([], []))
    for n, ctx, live in roots:
        by_entry[n][0 if live else 1].append(ctx)
    work = []
    for n in sorted(by_entry, reverse=True):
        ctxs = by_entry[n][0] or by_entry[n][1]
        work.append((n, None if any(c is None for c in ctxs) else sorted(set().union(*ctxs))))
    seen = set()
    while work:
        entry, ctx = work.pop()
        if entry in seen:
            continue
        hilo, pc, ending = HiLo(), entry, False
        while True:
            w = cave_word(pc)
            if w is None or pc in seen:
                break
            seen.add(pc)
            hilo.step(pc, w)
            t = jump_target(w)
            if t is not None:
                n = mapper.map(t, ctx)
                if n is None:
                    report['unmapped'].append('j at %08X -> %08X' % (pc, t))
                else:
                    report['jumps'] += 1
                    change_cave(pc, with_target(w, n) >> 16)
                    change_cave(pc + 2, with_target(w, n) & 0xFFFF)
                if n is not None and cave_word(n) is not None:
                    work.append((n, ctx))
            b = branch_target(w, pc)
            if b is not None:
                work.append((b, ctx))
            if ending:
                break
            if w >> 26 == J or (w >> 26 == 0 and (w & 0x3F) == 8):
                ending = True
            elif w >> 26 == JAL:
                hilo.forget_call()
            pc += 4
        for lui_pc, hi, los in resolve(hilo, ctx):
            report['hilo_pairs'] += 1
            change_cave(lui_pc + 2, hi)
            for p, lo in los.items():
                change_cave(p + 2, lo)
    report['cave_words'] = len(seen)
    for a in at:
        if a & 3 == 0 and a not in seen and cave_word(a) is not None and jump_target(cave_word(a)) is not None:
            report['unreached_jumps'] += 1

    # 3. Pointers to what the list wrote.
    for a in at:
        if a & 3 or a in seen or a + 2 not in at or layout.is_text(a):
            continue
        p = (at[a][0][2] << 16) | at[a + 2][0][2]
        n = mapper.map(p) if p >> 24 == 0x80 and not p & 1 else None
        if n is not None and n != p and p not in at and n in at:
            report['pointers'] += 1
            change_cave(a, n >> 16)
            change_cave(a + 2, n & 0xFFFF)

    for index, value in changes.items():
        set_value(lines, index, value)
    report['values_changed'] = len(changes)
    report['unmapped'] = sorted(set(report['unmapped']))
    report['lui_conflicts'] = sorted(set(report['lui_conflicts']))
    report['stepped_bases'] = sorted(set(report['stepped_bases']))
    return '\n'.join(lines), report


def move_writes(text, lo, hi, delta):
    """Move every code address in [lo, hi) (RDRAM offsets) by delta. -> (text, lines moved)"""
    lines, moved = text.split('\n'), 0
    for i, line in enumerate(lines):
        m = LINE.match(line.strip())
        if not m or m[1].upper() == '50':
            continue
        a = int(m[2], 16)
        if lo <= a < hi:
            lines[i] = '%s%06X %s%s' % (m[1].upper(), a + delta, m[3].upper(), m[4])
            moved += 1
    return '\n'.join(lines), moved
