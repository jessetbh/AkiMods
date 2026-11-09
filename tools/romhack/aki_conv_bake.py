"""Bake GameShark writes that land on game instructions into a ROM patch (aki-roadmap#185).

Recompiled code never reads its instruction bytes back, so a code that edits an instruction does nothing at run time.
The runtime does recompile every function whose ROM bytes a patch changes, so those edits can go into the pack's patch.

What real hardware does with a code list, per frame: run every unit in order against whatever is in RDRAM. That's what
this simulates, once per set of overlays that can be loaded together (a "world"), starting from the ROM's bytes:
  - A condition reading an overlay's or main's text is known: those bytes are the ROM's plus the codes' own writes.
    A code hack's "is this overlay loaded?" guard is that, and it's why most conditional code writes can be baked.
  - A condition reading anything else (data, BSS, the heap) depends on the running game: its unit can't be baked.
A byte is baked if it ends the same in every world holding its overlay. A byte that differs between worlds (main code
written only while some overlay is loaded) can't be, and neither can a list that never settles. Those are reported.

codes.txt is left alone: the baked writes keep running, as they do today, so conditions and any game read of those
bytes still see the same RDRAM.
"""
import collections
import re

RAM_SIZE = 0x800000                 # the runtime skips writes past 8 MB and fails conditions past it
MAX_PASSES = 64                     # long chains of guards settle slowly, a pass at a time
COND = {0xD0: (1, True), 0xD1: (2, True), 0xD2: (1, False), 0xD3: (2, False)}   # size, is an equality test
WRITE = {0x80: 1, 0xA0: 1, 0x81: 2, 0xA1: 2}
# The runtime's grammar: 8 hex digits, separators, 4 hex digits, or 12 hex digits; then the end, a space, # or //.
CODE_LINE = re.compile(r'^(?:([0-9A-Fa-f]{8})[\s:-]+([0-9A-Fa-f]{4})|([0-9A-Fa-f]{8})([0-9A-Fa-f]{4}))(?=$|\s|#|//)')

# conditions: (offset, size, equal, value); writes: (offset, size, value)
Unit = collections.namedtuple('Unit', 'group conditions writes')
Report = collections.namedtuple('Report', 'patched baked_bytes functions unsettled gaps inert guard_flips code_writes baked_groups ram_jumps')


def parse_units(text):
    """codes.txt -> [Unit] by the rules of librecomp conversion_codes.cpp. Prefixes carry across group boundaries, as
    they do in the runtime. Addresses are RDRAM offsets; a unit with a condition past RAM never runs, and a write past
    RAM is skipped."""
    raw, group = [], ''
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('['):
            group = line.strip('[]')
            continue
        m = CODE_LINE.match(line)
        if m:
            word = int(m[1] or m[3], 16)
            kind = word >> 24
            if kind in COND or kind in WRITE or kind == 0x50:
                raw.append((kind, word & 0xFFFFFF, int(m[2] or m[4], 16), group))

    def one(kind, addr, value):
        size = WRITE[kind]
        return (addr & ~1 if size == 2 else addr, size, value & (0xFFFF if size == 2 else 0xFF))

    units, pending, i = [], [], 0
    while i < len(raw):
        kind, addr, value, grp = raw[i]
        if kind in COND:
            size, equal = COND[kind]
            pending.append((addr & ~1 if size == 2 else addr, size, equal, value if size == 2 else value & 0xFF))
            i += 1
            continue
        if kind == 0x50:
            if i + 1 < len(raw) and raw[i + 1][0] in WRITE:
                nkind, naddr, nvalue, grp = raw[i + 1]
                count, step = (addr >> 8) & 0xFF, addr & 0xFF
                writes = [one(nkind, naddr + k * step, nvalue + k * value) for k in range(count)]
                i += 2
            else:  # a repeater before a conditional isn't a GameShark form: both go, as in the runtime
                i += 1
                while i < len(raw) and (raw[i][0] in COND or raw[i][0] == 0x50):
                    i += 1
                i += 1
                pending = []
                continue
        else:
            writes = [one(kind, addr, value)]
            i += 1
        writes = [w for w in writes if w[0] + w[1] <= RAM_SIZE]
        if writes and all(c[0] + c[1] <= RAM_SIZE for c in pending):
            units.append(Unit(grp, tuple(pending), writes))
        pending = []
    return units


def worlds_of(layout):
    """Every largest set of overlays that can be in RAM together: those whose address ranges don't overlap."""
    ovls = [s for s in layout['sections'] if s.get('slot') is not None]
    clash = lambda a, b: a['text'] < b['end'] and b['text'] < a['end']
    found = []

    def grow(chosen, rest):
        if not rest:
            found.append(frozenset(o['name'] for o in chosen))
            return
        first, others = rest[0], rest[1:]
        grow(chosen + [first], [o for o in others if not clash(first, o)])
        grow(chosen, others)

    grow([], ovls)
    return [sorted(n) for n in dict.fromkeys(found) if not any(n < m for m in found)]


class Sections:
    """A layout's text sections on a ROM image: what's code, and each section's bytes."""

    def __init__(self, layout, rom):
        self.rom = rom
        self.secs = {s['name']: s for s in layout['sections']}
        self.fixed = [s['name'] for s in layout['sections'] if s.get('slot') is None]
        self.functions = {}
        for name, s in self.secs.items():
            base = s['text'] & 0xFFFFFF
            self.functions[name] = [(base + off, base + off + size) for off, size in s['funcs'] if size]

    def rom_at(self, name, ram_offset):
        s = self.secs[name]
        return s['rom'] + ram_offset - (s['text'] & 0xFFFFFF)

    def text_range(self, name):
        s = self.secs[name]
        return s['text'] & 0xFFFFFF, s['data'] & 0xFFFFFF

    def world_image(self, overlays):
        """(RDRAM with this world's text, mask of the bytes that are text)."""
        image, known = bytearray(RAM_SIZE), bytearray(RAM_SIZE)
        for name in self.fixed + list(overlays):
            lo, hi = self.text_range(name)
            start = self.rom_at(name, lo)
            image[lo:hi] = self.rom[start:start + hi - lo]
            known[lo:hi] = b'\1' * (hi - lo)
        return image, known


def _holds(image, c):
    offset, size, equal, value = c
    have = image[offset] if size == 1 else image[offset] << 8 | image[offset + 1]
    return (have == value) == equal


def _all_known(known, u):
    return all(known[c[0]] and known[c[0] + c[1] - 1] for c in u.conditions)


def _put(image, known, w):
    offset, size, value = w
    for k in range(size):
        if known[offset + k]:
            image[offset + k] = (value >> (8 * (size - 1 - k))) & 0xFF


def simulate(units, image, known):
    """Runs the list on the image, in file order, until a pass ends with the bytes it started with. The game runs between
    passes, so only those end values count: a unit flipping a byte that a later one flips back in the same pass settles.
    Returns the offsets still changing when the list doesn't settle, empty when it does. A unit with an unknown condition
    doesn't run; only known bytes are written, the rest of RAM is the runtime's business."""
    runnable = [u for u in units if _all_known(known, u)]

    def run_pass():
        for u in runnable:
            if all(_holds(image, c) for c in u.conditions):
                for w in u.writes:
                    _put(image, known, w)
        return bytes(image)

    seen = bytes(image)
    for _ in range(MAX_PASSES):
        now = run_pass()
        if now == seen:
            return set()
        seen = now
    # Whatever differs between passes from here on is what never settles.
    unstable = set()
    written = {w[0] + k for u in runnable for w in u.writes for k in range(w[1])}
    for _ in range(MAX_PASSES):
        now = run_pass()
        if now != seen:
            unstable.update(k for k in written if now[k] != seen[k])
            seen = now
    return unstable


NO_TARGET = 'jumps to code that is not in the ROM'
NO_COMPILE = 'the recompiler would refuse it'
LW, SW, COP1, SP = 35, 43, 17, 29


def recompiler_refuses(word):
    """Instructions N64Recomp stops on (its analysis.cpp, recompilation.cpp). One refused function fails the whole patch
    and the conversion with it, and it needn't be code that runs: an unconditional write lands in every overlay at that
    address, where on hardware it's harmless junk in code nothing calls (Lucha Underground's odd stack offset)."""
    op, rs, rt = word >> 26, (word >> 21) & 31, (word >> 16) & 31
    if op in (LW, SW) and rs == SP:
        offset = word & 0xFFFF
        return bool(offset & 3 or offset & 0x8000)
    if op == COP1 and rs in (2, 6):   # cfc1, ctc1: only control registers 0 and 31 exist
        return (word >> 11) & 31 not in (0, 31)
    return False


def bake(units, layout, base_rom):
    """Bakes the list, leaving out any group that would jump outside the game's text. Such a hook was harmless while it
    did nothing, and baked it can crash: it's live from boot, and its code isn't there yet. Code the list writes appears
    only once the list has run (Legends' Extra Attires ran off into empty RAM 5 s in), and code from a hack's own loader
    may never appear in a de-relinked patch."""
    sections = Sections(layout, base_rom)
    provided = bytearray(RAM_SIZE)
    for name in sections.secs:
        lo, hi = sections.text_range(name)
        provided[lo:hi] = b'\1' * (hi - lo)
    left_out = collections.Counter()
    while True:
        report = _bake_once(units, layout, base_rom)
        missing = {target for _, _, target in report.ram_jumps if not provided[target]}
        if not missing:
            break
        # The groups whose writes make those jumps.
        jumps = {(a, t) for _, a, t in report.ram_jumps if t in missing}
        at = {a for a, _ in jumps}
        groups = {u.group for u in units for w in u.writes if w[0] & ~3 in at}
        if not groups:
            break
        for u in units:
            if u.group in groups:
                left_out[u.group] += sum(1 for w in u.writes if any(
                    sections.rom_at(n, w[0]) >= 0 and lo <= w[0] < hi
                    for n, fs in sections.functions.items() for lo, hi in fs))
        units = [u for u in units if u.group not in groups]
    if left_out:
        report.gaps[NO_TARGET] = dict(left_out)
    return report


def _bake_once(units, layout, base_rom):
    """-> Report. patched: the ROM with every bakeable byte written (base_rom is left as it is). gaps: {reason: {group:
    writes}}; inert: {group: writes} that land on code only where their conditions don't hold (in the other overlays
    they're data writes, or they never fire); functions: {section: [(start, end)]} changed; unsettled: offsets that
    never settle."""
    sections = Sections(layout, base_rom)
    results = []
    stock_images = {}
    unstable = set()
    for world in worlds_of(layout):
        image, known = sections.world_image(world)
        stock_images[tuple(world)] = bytes(image)
        unstable |= simulate(units, image, known)
        results.append((world, image, known))

    in_function = {}
    for name in sections.secs:
        mask = bytearray(RAM_SIZE)
        for lo, hi in sections.functions[name]:
            mask[lo:hi] = b'\1' * (hi - lo)
        in_function[name] = mask

    # Each function byte: its value in every world holding its section.
    holds_section = lambda name, world: name in sections.fixed or name in world
    patched = bytearray(base_rom)
    disagreed = bytearray(RAM_SIZE)
    refused = bytearray(RAM_SIZE)
    baked = 0
    changed_functions = {}
    for name in sections.secs:
        holding = [r for r in results if holds_section(name, r[0])]
        for lo, hi in sections.functions[name]:
            start = sections.rom_at(name, lo)
            stock = base_rom[start:start + hi - lo]
            if all(bytes(r[1][lo:hi]) == stock for r in holding):
                continue
            hit = 0
            for k in range(hi - lo):
                if lo + k in unstable:
                    continue
                values = {r[1][lo + k] for r in holding}
                if len(values) > 1:
                    disagreed[lo + k] = 1
                    continue
                v = values.pop()
                if v != stock[k]:
                    patched[start + k] = v
                    hit += 1
            if not hit:
                continue
            new = patched[start:start + hi - lo]
            if any(new[k:k + 4] != stock[k:k + 4] and recompiler_refuses(int.from_bytes(new[k:k + 4], 'big'))
                   for k in range(0, hi - lo, 4)):
                patched[start:start + hi - lo] = stock
                refused[lo:hi] = b'\1' * (hi - lo)
                continue
            baked += hit
            changed_functions.setdefault(name, []).append((lo, hi))

    # Baked jumps to anywhere that isn't a known function, such as the middle of one. The runtime compiles those from
    # RDRAM when they're first reached, once a relink.json has switched that on.
    starts = {lo for fs in sections.functions.values() for lo, _ in fs}
    ram_jumps = []
    for name, fs in changed_functions.items():
        for lo, hi in fs:
            start = sections.rom_at(name, lo)
            for k in range(0, hi - lo, 4):
                word = int.from_bytes(patched[start + k:start + k + 4], 'big')
                if word != int.from_bytes(base_rom[start + k:start + k + 4], 'big') and word >> 26 in (2, 3):
                    target = (word & 0x3FFFFFF) << 2 & 0xFFFFFF
                    if target not in starts:
                        ram_jumps.append((name, lo + k, target))

    # What became of every write that lands on a function in some world.
    gaps = collections.defaultdict(collections.Counter)
    inert = collections.Counter()
    flips = collections.Counter()
    baked_groups = collections.Counter()
    code_writes = 0
    for u in units:
        for w in u.writes:
            homes = [n for n in sections.secs if any(in_function[n][w[0] + k] for k in range(w[1]))]
            if not homes:
                # A data write behind a guard the baked bytes turn false: on hardware it fires for a frame or more.
                for world, image, known in results:
                    if u.conditions and _all_known(known, u) and all(_holds(stock_images[tuple(world)], c) for c in u.conditions) \
                            and not all(_holds(image, c) for c in u.conditions):
                        flips[u.group] += 1
                        break
                continue
            code_writes += 1
            where = [r for r in results if any(holds_section(n, r[0]) for n in homes)]
            decided = [r for r in where if _all_known(r[2], u)]
            fires = [r for r in decided if all(_holds(stock_images[tuple(r[0])], c) or _holds(r[1], c) for c in u.conditions)]
            if any(refused[w[0] + k] for k in range(w[1])) and (fires or not u.conditions):
                gaps[NO_COMPILE][u.group] += 1
            elif any(disagreed[w[0] + k] for k in range(w[1])):
                gaps['differs between overlays'][u.group] += 1
            elif any(w[0] + k in unstable for k in range(w[1])):
                gaps['never settles'][u.group] += 1
            elif fires:
                baked_groups[u.group] += 1
            elif len(decided) < len(where):
                gaps['condition on game state'][u.group] += 1
            else:
                inert[u.group] += 1
    return Report(bytes(patched), baked, changed_functions, sorted(unstable), {k: dict(v) for k, v in gaps.items()},
                  dict(inert), dict(flips), code_writes, dict(baked_groups), ram_jumps)
