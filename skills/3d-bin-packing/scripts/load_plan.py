#!/usr/bin/env python3
"""Plan a shipment into containers, keeping each group (PO) whole, and check the result.

    python3 load_plan.py SHIPMENT.csv --out DIR [--length 1203.5 --width 235.2 --height 269.5
        --payload 28620] [--group-col po] [--split-oversize] [--time-limit 480] [--seed 1]
        [--support 0.8] [--clearance 0]
    python3 load_plan.py SHIPMENT.csv --out DIR --advise [--allocation ALLOC.csv]
        [--objective volume|cartons|value] [--limits LIMITS.csv] [... the options above ...]

Method: per container, cartons are built into columns (one SKU stacked to the roof, flat
cartons riding in the headroom), columns are laid in lanes along the container length,
and leftovers go on column tops. Groups are assigned to containers by first-fit
decreasing from the lower bound, then repaired by annealed moves and swaps until every
container packs. With --split-oversize, an oversize group's lines are assigned one by one,
each only into a container that already holds another line of that group.
Writes allocation.csv, placements.csv, summary.json and report.md to DIR.

Advise mode keeps the containers and the group assignment (given, or planned first) and
recommends extra cartons of lines already in each container. Per container it packs the base
load several ways (mixed-SKU stacks of one footprint, then single-SKU columns, with leftovers
placed in the free space), tops each up line by line into the space left above and beside it,
and keeps the best result for the objective. It also writes advice.csv.
Standard library only.
"""
import argparse
import csv
import heapq
import itertools
import json
import math
import pathlib
import random
import sys
import time
from dataclasses import dataclass, field

EPS = 1e-6


@dataclass(frozen=True)
class Box:
    L: float
    W: float
    H: float
    payload: float
    support: float

    @property
    def m3(self):
        return self.L * self.W * self.H / 1e6


@dataclass(eq=False)
class CartonType:
    po: str
    group: str
    sku: str
    dims: tuple
    kg: float  # per carton
    count: int
    upright: bool
    orients: list = field(default_factory=list)

    def __post_init__(self):
        l, w, h = self.dims
        cands = [(l, w, h), (w, l, h)] if self.upright else list(itertools.permutations(self.dims))
        self.orients = list(dict.fromkeys(cands))

    @property
    def vol(self):
        return self.dims[0] * self.dims[1] * self.dims[2]

    def grown(self, cm):
        return CartonType(self.po, self.group, self.sku, tuple(d + cm for d in self.dims),
                          self.kg, self.count, self.upright)


@dataclass(eq=False)
class Column:
    """A stack on one footprint; local x runs along the longer side l."""
    l: float
    w: float
    height: float
    room: float
    free_len: float
    items: list  # (ctype, dx, dy, z, l, w, h)
    carrying: bool = False

    def add_topper(self, t, n, a, b, h):
        dx = self.l - self.free_len
        self.items += [(t, dx, 0.0, self.height + k * h, a, b, h) for k in range(n)]
        self.free_len -= a
        self.carrying = True


def make_column(t, orient, n, H):
    a, b, h = orient
    if a < b:
        a, b = b, a
    return Column(a, b, n * h, H - n * h, a, [(t, 0.0, 0.0, k * h, a, b, h) for k in range(n)])


@dataclass(eq=False)
class Lane:
    x: float
    y: float
    length: float
    width: float
    cols: list  # (column, x offset, rotated)


@dataclass(eq=False)
class ContainerPlan:
    lanes: list
    unplaced: dict  # ctype -> count
    loose: list = field(default_factory=list)  # (ctype, x, y, z, l, w, h) placed outside the lanes

    @property
    def unplaced_vol(self):
        return sum(t.vol * n for t, n in self.unplaced.items())

    def placements(self):
        out = []
        for lane in self.lanes:
            for col, x, rot in lane.cols:
                for t, dx, dy, z, l, w, h in col.items:
                    if rot:
                        out.append((t, lane.x + x + dy, lane.y + dx, z, w, l, h))
                    else:
                        out.append((t, lane.x + x + dx, lane.y + dy, z, l, w, h))
        return out + self.loose


@dataclass
class Assignment:
    bins: list  # one set of group names per container

    def copy(self):
        return Assignment([set(b) for b in self.bins])


# ---------------------------------------------------------------- stage 2: one container

def best_orient(t, H, how="height", depths=None):
    use = {o: int(H // o[2]) * o[2] for o in t.orients}
    top = max(use.values())
    good = [o for o in t.orients if use[o] >= 0.95 * top]
    if how == "narrow":
        # A narrow footprint fits a lane far more often than a wide one that fills the height perfectly.
        return min(good, key=lambda o: (min(o[0], o[1]), -use[o]))
    if how == "match" and depths:
        # Free-rotation cartons: stand them so a footprint side matches the lanes the fixed cartons need.
        def supply(o):
            return sum(n for d, n in depths.items() if 0 <= d - min(o[:2]) <= 2)
        return max(good, key=lambda o: (supply(o), use[o]))
    return max(t.orients, key=lambda o: (use[o], o[0] * o[1]))


def place_on_tops(t, n, cols, skip=None, whole=False):
    """Put up to n cartons of t as small stacks on column tops, fully supported. Returns count placed.
    With whole=True it places all n or nothing."""
    placed, touched = 0, []
    hmin = min(o[2] for o in t.orients) - EPS
    smin = min(t.dims) - EPS
    for c in sorted((c for c in cols if c.room >= hmin and c.free_len >= smin and c.w >= smin and c is not skip),
                    key=lambda c: (c.w, c.free_len)):
        while placed < n:
            best = None
            for a, b, h in t.orients:
                if h > c.room + EPS:
                    continue
                for p, q in ((a, b), (b, a)):
                    if p <= c.free_len + EPS and q <= c.w + EPS:
                        k = min(n - placed, int((c.room + EPS) // h))
                        key = (k * p * q * h, -p)
                        if best is None or key > best[0]:
                            best = (key, k, p, q, h)
            if best is None:
                break
            _, k, p, q, h = best
            touched.append((c, c.free_len, len(c.items), c.carrying))
            c.add_topper(t, k, p, q, h)
            placed += k
        if placed == n:
            break
    if whole and placed < n:
        for c, free_len, count, carrying in reversed(touched):
            c.free_len, c.carrying = free_len, carrying
            del c.items[count:]
        return 0
    return placed


def build_columns(types, H, how="narrow"):
    cols, pool = [], {}
    by_type = {}
    depths = {}  # narrow side of upright cartons -> lane length their columns need
    for t in types:
        if t.upright:
            side = min(t.dims[:2])
            depths[side] = depths.get(side, 0) + max(t.dims[:2]) * t.count / max(1, H // t.dims[2])
    for t in types:
        o = best_orient(t, H, how, depths)
        tiers = int(H // o[2])
        full = t.count // tiers
        by_type[t] = [make_column(t, o, tiers, H) for _ in range(full)]
        pool[t] = t.count - full * tiers
    # Flat cartons ride in the headroom of other SKUs' columns instead of taking floor.
    for t in types:
        lowest = min(o[2] for o in t.orients)
        if any(c.room >= lowest - EPS for u, cs in by_type.items() if u is not t for c in cs[:1]):
            pool[t] = t.count
            by_type[t] = []
    cols = [c for cs in by_type.values() for c in cs]
    for t in sorted(pool, key=lambda t: -t.dims[0] * t.dims[1]):
        if pool[t] and min(o[2] for o in t.orients) < H / 2:
            pool[t] -= place_on_tops(t, pool[t], cols)
    for t, n in pool.items():
        o = best_orient(t, H, how, depths)
        tiers = int(H // o[2])
        while n > 0:
            k = min(n, tiers)
            cols.append(make_column(t, o, k, H))
            n -= k
    # A column whose cartons all fit on other tops frees its floor space.
    for c in sorted(cols, key=lambda c: c.l * c.w):
        if not c.carrying and place_on_tops(c.items[0][0], len(c.items), cols, skip=c, whole=True):
            c.items = []
            cols.remove(c)
    return cols


def best_stack(members, H):
    """Counts per member that fill the height best (bounded subset sum in mm)."""
    cap = int(H * 10 + EPS)
    reach = [None] * (cap + 1)  # height -> (member, height below)
    reach[0] = (-1, 0)
    for i, (t, a, b, h, n) in enumerate(members):
        u = math.ceil(h * 10 - EPS)
        n = min(n, cap // u)
        if not n:
            continue
        used = [0] * (cap + 1)
        for s in range(u, cap + 1):
            if reach[s] is None and reach[s - u] is not None and used[s - u] < n:
                reach[s] = (i, s - u)
                used[s] = used[s - u] + 1
    s = max(s for s in range(cap + 1) if reach[s] is not None)
    counts = [0] * len(members)
    while s:
        i, s = reach[s]
        counts[i] += 1
    return counts


def stack_columns(types, H, how="narrow"):
    """Columns that mix SKUs (and groups) of one footprint class, stacked to fill the height.
    A footprint joins a class when it fits inside the class's smallest member and covers most of its area,
    so stacking members largest first keeps every carton fully supported."""
    depths = {}
    for t in types:
        if t.upright:
            side = min(t.dims[:2])
            depths[side] = depths.get(side, 0) + max(t.dims[:2]) * t.count / max(1, H // t.dims[2])
    classes = []  # [A, B, members]; member = [ctype, a, b, h, count left], a >= b
    entries = []
    for t in types:
        a, b, h = best_orient(t, H, how, depths)
        entries.append([t, max(a, b), min(a, b), h, t.count])
    for e in sorted(entries, key=lambda e: (-e[1] * e[2], -e[1], -e[3])):
        for c in classes:
            last = c[2][-1]
            if e[1] <= last[1] + EPS and e[2] <= last[2] + EPS and e[1] * e[2] >= 0.85 * c[0] * c[1]:
                c[2].append(e)
                break
        else:
            classes.append([e[1], e[2], [e]])
    cols = []
    for A, B, members in classes:
        while any(m[4] for m in members):
            counts = best_stack(members, H)
            repeat = min(m[4] // k for m, k in zip(members, counts) if k)
            for _ in range(repeat):
                items, z = [], 0.0
                for m, k in zip(members, counts):
                    for _ in range(k):
                        items.append((m[0], 0.0, 0.0, z, m[1], m[2], m[3]))
                        z += m[3]
                cols.append(Column(A, B, z, H - z, A, items))
            for m, k in zip(members, counts):
                m[4] -= k * repeat
    for c in sorted(cols, key=lambda c: c.l * c.w):
        t = c.items[0][0]
        if not c.carrying and all(it[0] is t for it in c.items) and place_on_tops(t, len(c.items), cols, skip=c, whole=True):
            c.items = []
            cols.remove(c)
    return cols


def fill_lane(items, d, length, tol):
    low = d - tol - EPS
    cand = []
    for c in items:
        if c.l <= d + EPS:
            if c.l >= low:
                cand.append((c.l, c.w, True, c))
        elif low <= c.w <= d + EPS:
            cand.append((c.w, c.l, False, c))
    cand.sort(key=lambda e: (-e[0], -e[1]))
    used, area, picked = 0.0, 0.0, []
    for dep, ln, rot, c in cand:
        if used + ln <= length + EPS:
            picked.append((c, used, rot, dep, ln))
            used += ln
            area += dep * ln
    return area, used, picked


def fill_rect(rect, remaining, rng, policy, tol, free):
    """Lay lanes along x inside rect. Leftover lane ends and the strips beside shallower columns go to free."""
    x0, y0, lx, ly = rect
    y, lanes = y0, []
    while remaining:
        avail = y0 + ly - y
        ds = {v for c in remaining for v in (c.l, c.w) if v <= avail + EPS}
        opts = []
        for d in ds:
            area, used, picked = fill_lane(remaining, d, lx, tol)
            if picked:
                opts.append((area / (d * lx), area, d, used, picked))
        if not opts:
            break
        opts.sort(key=lambda o: (-o[0], -o[1]))
        if policy.deep:
            # Deep lanes first, so wide columns are not stranded once the width is used up.
            opts = sorted((o for o in opts if o[0] >= 0.9 * opts[0][0]), key=lambda o: -o[2])
        pick = opts[rng.randrange(min(3, len(opts)))] if rng.random() < policy.noise else opts[0]
        _, _, depth, used, picked = pick
        lanes.append(Lane(x0, y, lx, depth, [(c, x, rot) for c, x, rot, _, _ in picked]))
        taken = {id(p[0]) for p in picked}
        remaining[:] = [c for c in remaining if id(c) not in taken]
        for dep, run in itertools.groupby(picked, key=lambda p: p[3]):
            run = list(run)
            if depth - dep > EPS:
                free.append((x0 + run[0][1], y + dep, sum(p[4] for p in run), depth - dep))
        if lx - used > EPS:
            free.append((x0 + used, y, lx - used, depth))
        y += depth
    return lanes


def pack_floor(cols, box, rng, policy):
    remaining = sorted(cols, key=lambda c: -c.l * c.w)
    free = []
    lanes = fill_rect((0.0, 0.0, box.L, box.W), remaining, rng, policy, policy.tol, free)
    while free and remaining:
        free.sort(key=lambda r: r[2] * r[3])
        lanes += fill_rect(free.pop(), remaining, rng, policy, math.inf, free)
    return lanes, remaining


@dataclass(frozen=True)
class Policy:
    orient: str  # free-rotation cartons: "match" lane depths, "narrow" footprint, or best "height" use
    deep: bool  # lane order: deepest good lane rather than most efficient
    tol: float  # how much shallower than its lane a column may be in the first pass
    noise: float  # chance of taking a runner-up lane
    mixed: bool = False  # stack different SKUs of one footprint in a column (advise mode)


POLICIES = [Policy(o, d, t, 0.0) for t in (3.0, math.inf, 1.5, 6.0) for d in (False, True)
            for o in ("match", "narrow", "height")]
MIXED_POLICIES = [Policy(o, d, t, 0.0, True) for t in (3.0, math.inf, 1.5, 6.0) for d in (False, True)
                  for o in ("match", "narrow", "height")]


def policy(attempt, rng, mixed=False):
    fixed = MIXED_POLICIES if mixed else POLICIES
    if attempt < len(fixed):
        return fixed[attempt]
    return Policy(rng.choice(("match", "narrow", "height")), rng.random() < 0.5, rng.choice((1.5, 3.0, 6.0, math.inf)), 0.3,
                  mixed)


def merged_surfaces(lanes, H):
    """Runs of adjacent, untouched, equal-height columns in a lane form one long top surface.
    Long flat cartons can then span several columns, fully supported."""
    out = []
    for lane in lanes:
        run = []
        for c, x, rot in sorted(lane.cols, key=lambda e: e[1]) + [(None, None, None)]:
            if run and (c is None or c.free_len < c.l - EPS or abs(c.height - run[0][0].height) > EPS
                        or abs(x - (run[-1][1] + (run[-1][0].w if run[-1][2] else run[-1][0].l))) > EPS):
                if len(run) > 1:
                    length = sum(e[0].w if e[2] else e[0].l for e in run)
                    depth = min(e[0].l if e[2] else e[0].w for e in run)
                    top = Column(length, depth, run[0][0].height, H - run[0][0].height, length, [])
                    for e in run:
                        e[0].free_len = 0.0
                    lane.cols.append((top, run[0][1], False))
                    out.append(top)
                run = []
            if c is not None and c.items and c.free_len >= c.l - EPS and H - c.height > EPS:
                run.append((c, x, rot))
    return out


def pack_one(types, box, pol, rng):
    cols = (stack_columns if pol.mixed else build_columns)(types, box.H, pol.orient)
    lanes, left = pack_floor(cols, box, rng, pol)
    placed = [c for lane in lanes for c, _, _ in lane.cols]
    if left:
        placed += merged_surfaces(lanes, box.H)
    loose = {}
    for c in left:
        for it in c.items:
            loose[it[0]] = loose.get(it[0], 0) + 1
    unplaced = {}
    for t, n in loose.items():
        n -= place_on_tops(t, n, placed)
        if n:
            unplaced[t] = n
    return ContainerPlan(lanes, unplaced)


def pack_container(types, box, seed=1, tries=1, mixed=False):
    """Stage 2: pack the given carton types into one container. Best of `tries` attempts."""
    best = None
    for attempt in range(tries):
        rng = random.Random(seed * 7919 + attempt)
        plan = pack_one(types, box, policy(attempt, rng, mixed), rng)
        if best is None or plan.unplaced_vol < best.unplaced_vol:
            best = plan
        if not plan.unplaced:
            break
    return best


class Space:
    """The load as a height map, for placing cartons one at a time into whatever space is left.
    A carton rests on the floor or on tops at one height with at least the support ratio, and is never
    tucked under another carton, so only cartons with some top still exposed matter for a new one."""
    CELL = 25.0

    def __init__(self, box, placements):
        self.box = box
        self.items = []  # (ctype, x, y, z, l, w, h)
        self.cover = []  # area of each item's top covered by cartons resting on it
        self.under = []  # per item: [(supporting item, overlap area)]
        self.grid = {}  # cell -> set of exposed item indices
        for p in placements:
            self.add(p)

    def cells(self, x, y, l, w):
        c = self.CELL
        return [(i, j) for i in range(int(x // c), int((x + l - EPS) // c) + 1)
                for j in range(int(y // c), int((y + w - EPS) // c) + 1)]

    def near(self, x, y, l, w):
        found = set()
        for cell in self.cells(x, y, l, w):
            found |= self.grid.get(cell, set())
        return found

    def overlaps(self, x, y, l, w):
        """(item, overlap area) for exposed items under the rectangle."""
        out = []
        for k in self.near(x, y, l, w):
            _, bx, by, _, bl, bw, _ = self.items[k]
            ox = min(x + l, bx + bl) - max(x, bx)
            oy = min(y + w, by + bw) - max(y, by)
            if ox > EPS and oy > EPS:
                out.append((k, ox * oy))
        return out

    def fit(self, x, y, l, w, h):
        """Height a carton of these extents would rest at, or None if it cannot go there."""
        box = self.box
        if x < -EPS or y < -EPS or x + l > box.L + EPS or y + w > box.W + EPS:
            return None
        z, area = 0.0, l * w
        for k, a in self.overlaps(x, y, l, w):
            top = self.items[k][3] + self.items[k][6]
            if top > z + EPS:
                z, area = top, a
            elif top > z - EPS:
                area += a
        if z + h > box.H + EPS or area < box.support * l * w - EPS:
            return None
        return z

    def add(self, p):
        t, x, y, z, l, w, h = p
        k = len(self.items)
        under = [(j, a) for j, a in self.overlaps(x, y, l, w)
                 if abs(self.items[j][3] + self.items[j][6] - z) < EPS] if z > EPS else []
        self.items.append(p)
        self.cover.append(0.0)
        self.under.append(under)
        for cell in self.cells(x, y, l, w):
            self.grid.setdefault(cell, set()).add(k)
        for j, a in under:
            self.cover[j] += a
            if self.cover[j] >= self.items[j][4] * self.items[j][5] * (1 - 1e-9):
                self.expose(j, False)

    def pop(self):
        k = len(self.items) - 1
        _, x, y, _, l, w, _ = self.items[k]
        for cell in self.cells(x, y, l, w):
            self.grid[cell].discard(k)
        for j, a in self.under[k]:
            if self.cover[j] >= self.items[j][4] * self.items[j][5] * (1 - 1e-9):
                self.expose(j, True)
            self.cover[j] -= a
        self.items.pop()
        self.cover.pop()
        self.under.pop()

    def expose(self, j, on):
        _, x, y, _, l, w, _ = self.items[j]
        for cell in self.cells(x, y, l, w):
            if on:
                self.grid.setdefault(cell, set()).add(j)
            else:
                self.grid[cell].discard(j)

    def exposed(self):
        return {k for s in self.grid.values() for k in s}

    def anchors(self, k, l, w):
        """Corner positions for an l x w footprint on top of, or beside, item k."""
        _, x, y, _, bl, bw, _ = self.items[k]
        xe, ye = x + bl, y + bw
        return [(x, y), (xe - l, y), (x, ye - w), (xe - l, ye - w), (xe, y), (xe, ye - w), (x, ye), (xe - l, ye),
                (x - l, y), (x, y - w), (x, self.box.W - w), (self.box.L - l, y)]

    def rank(self, x, y, z, l, w, h):
        """Order of open spots: lowest first, then nearest the front wall and the side wall."""
        return (round(z, 6), round(x, 4), round(y, 4))

    def place(self, t, n, whole=False):
        """Put up to n cartons of t, each at the best-ranked open spot. With whole=True, all n or none."""
        if n <= 0:
            return 0
        heap, seen = [], set()

        def push(k):
            for l, w, h in t.orients:
                for x, y in (self.anchors(k, l, w) if k is not None else [(0.0, 0.0)]):
                    key = (round(x, 4), round(y, 4), l, w, h)
                    if key in seen:
                        continue
                    seen.add(key)
                    z = self.fit(x, y, l, w, h)
                    if z is not None:
                        heapq.heappush(heap, (self.rank(x, y, z, l, w, h), x, y, l, w, h))

        push(None)
        for k in sorted(self.exposed()):
            push(k)
        placed = 0
        while placed < n and heap:
            r, x, y, l, w, h = heapq.heappop(heap)
            z = self.fit(x, y, l, w, h)
            if z is None:
                continue
            if self.rank(x, y, z, l, w, h) != r:
                heapq.heappush(heap, (self.rank(x, y, z, l, w, h), x, y, l, w, h))
                continue
            self.add((t, x, y, z, l, w, h))
            placed += 1
            seen.clear()
            push(len(self.items) - 1)
        if whole and placed < n:
            for _ in range(placed):
                self.pop()
            return 0
        return placed


def lift_flat(placements, box):
    """Take every flat carton (and whatever rests on one) off the load. Returns (kept, lifted counts)."""
    flat = {p[0] for p in placements if min(o[2] for o in p[0].orients) <= box.H / 8}
    lifted = [p for p in placements if p[0] in flat]
    tops = {}
    for p in lifted:
        tops.setdefault(round(p[3] + p[6], 4), []).append(p)
    kept = []
    for p in sorted((p for p in placements if p[0] not in flat), key=lambda p: p[3]):
        t, x, y, z, l, w, h = p
        if any(x < a + al and a < x + l and y < b + bw and b < y + w for _, a, b, _, al, bw, _ in tops.get(round(z, 4), [])):
            lifted.append(p)
            tops.setdefault(round(z + h, 4), []).append(p)
        else:
            kept.append(p)
    counts = {}
    for p in lifted:
        counts[p[0]] = counts.get(p[0], 0) + 1
    return kept, counts


def place_leftovers(placements, unplaced, box):
    """Place unplaced cartons in the free space, largest first. If some still do not fit, lift the flat
    cartons off the column tops and place everything loose again, largest first."""
    kept, lifted = lift_flat(placements, box)
    for t, n in unplaced.items():
        lifted[t] = lifted.get(t, 0) + n
    best = None
    for base, todo in ((placements, unplaced), (kept, lifted)):
        space = Space(box, base)
        left = {}
        # Narrowest footprint last: wide cartons need the few wide surfaces, rods fit almost anywhere.
        for t, n in sorted(todo.items(), key=lambda e: (-min(min(o[:2]) for o in e[0].orients), -e[0].vol)):
            k = space.place(t, n)
            if k < n:
                left[t] = n - k
        plan = ContainerPlan([], left, space.items)
        if not left:
            return plan
        if best is None or plan.unplaced_vol < best.unplaced_vol:
            best = plan
    return best


def pack_complete(types, box, seed=1, tries=16):
    """Advise-mode stage 2: the column and lane packer (mixed stacks first, then single-SKU columns),
    with any leftovers placed in the remaining space. Returns the first plan that places everything,
    else the one leaving the least volume."""
    best = None
    for mixed in (True, False):
        for attempt in range(tries):
            rng = random.Random(seed * 7919 + attempt)
            plan = pack_one(types, box, policy(attempt, rng, mixed), rng)
            if plan.unplaced:
                plan = place_leftovers(plan.placements(), plan.unplaced, box)
            if not plan.unplaced:
                return plan
            if best is None or plan.unplaced_vol < best.unplaced_vol:
                best = plan
    return best


# ---------------------------------------------------------------- stage 1: groups to containers

class Planner:
    def __init__(self, groups, box, seed, families=None):
        self.groups, self.box, self.seed = groups, box, seed
        self.families = families or {}  # split oversize PO -> fewest containers it needs
        self.family = {g: ts[0].po for g, ts in groups.items() if ts[0].po in self.families}
        self.vol = {g: sum(t.vol * t.count for t in ts) / 1e6 for g, ts in groups.items()}
        self.kg = {g: sum(t.kg * t.count for t in ts) for g, ts in groups.items()}
        self.cache = {}
        self.rng = random.Random(seed)

    def cost(self, members, tries=2):
        """Unplaced m3 after stage 2, plus any payload excess in tonnes. Cached by frozenset of groups."""
        key = frozenset(members)
        hit = self.cache.get(key)
        if hit is None or (hit[1] < tries and hit[0] > 0):
            types = [t for g in sorted(key) for t in self.groups[g]]
            plan = pack_container(types, self.box, self.seed, tries) if types else ContainerPlan([], {})
            hit = (plan.unplaced_vol / 1e6, tries)
            self.cache[key] = hit
        over = max(0.0, sum(self.kg[g] for g in key) - self.box.payload) / 1000
        return hit[0] + over

    def initial(self, n):
        total = sum(self.vol.values())
        cap = min(self.box.m3, total / n * 1.02)
        bins, bv, bk = [set() for _ in range(n)], [0.0] * n, [0.0] * n
        for po, k in self.families.items():
            home = sorted(range(n), key=lambda i: bv[i])[:k]
            for g in sorted((g for g in self.groups if self.family.get(g) == po), key=lambda g: -self.vol[g]):
                i = min(home, key=lambda i: bv[i])
                bins[i].add(g)
                bv[i] += self.vol[g]
                bk[i] += self.kg[g]
        for g in sorted((g for g in self.groups if g not in self.family), key=lambda g: -self.vol[g]):
            fits = [i for i in range(n) if bv[i] + self.vol[g] <= cap and bk[i] + self.kg[g] <= self.box.payload]
            i = fits[0] if fits else min(range(n), key=lambda i: bv[i])
            bins[i].add(g)
            bv[i] += self.vol[g]
            bk[i] += self.kg[g]
        return Assignment(bins)

    def joins(self, g, members):
        """A line of a split PO may only go where another line of that PO already is."""
        po = self.family.get(g)
        return po is None or any(self.family.get(x) == po for x in members if x != g)

    def fits(self, members):
        return (sum(self.vol[g] for g in members) <= self.box.m3 + EPS
                and sum(self.kg[g] for g in members) <= self.box.payload + EPS)

    def search(self, n, budget_end):
        """Simulated annealing over moves and swaps; returns the best assignment and its per-container costs."""
        a = self.initial(n)
        costs = [self.cost(b) for b in a.bins]
        best, best_costs = a.copy(), list(costs)
        temp, stale, tries = 0.3, 0, 2
        while sum(best_costs) > 0 and time.time() < budget_end and n > 1:
            stale += 1
            temp = max(0.005, temp * 0.999)
            if stale > 400:
                # Stuck: look harder at the containers that still miss, then reheat from the best state.
                tries = min(tries * 2, 64)
                a, costs = best.copy(), [self.cost(b, tries) if c > 0 else c for b, c in zip(best.bins, best_costs)]
                best_costs = list(costs)
                temp, stale = 0.3, 0
                continue
            bad = [i for i in range(n) if costs[i] > 0]
            i = self.rng.choice(bad) if bad and self.rng.random() < 0.8 else self.rng.randrange(n)
            j = self.rng.choice([j for j in range(n) if j != i])
            if not a.bins[i]:
                continue
            g = self.rng.choice(sorted(a.bins[i]))
            h = self.rng.choice(sorted(a.bins[j])) if a.bins[j] and self.rng.random() < 0.5 else None
            newA, newB = a.bins[i] - {g}, (a.bins[j] - {h}) | {g}
            if h is not None:
                newA.add(h)
            if not (self.fits(newA) and self.fits(newB) and self.joins(g, newB) and self.joins(h, newA)):
                continue
            ca, cb = self.cost(newA), self.cost(newB)
            # A container that just misses gets a harder stage 2 before the move is judged.
            if 0 < ca < 1.5:
                ca = self.cost(newA, 8)
            if 0 < cb < 1.5:
                cb = self.cost(newB, 8)
            delta = ca + cb - costs[i] - costs[j]
            if delta <= 0 or self.rng.random() < math.exp(-delta / temp):
                a.bins[i], a.bins[j], costs[i], costs[j] = newA, newB, ca, cb
                if sum(costs) < sum(best_costs) - 1e-9:
                    best, best_costs, stale = a.copy(), list(costs), 0
        return best, best_costs


# ---------------------------------------------------------------- input, bounds, oversize

def read_shipment(path, group_col):
    lines = []
    for r in csv.DictReader(open(path, newline="")):
        n = int(r["cartons"])
        if n <= 0:
            continue
        dims = tuple(float(r[k]) for k in ("length_cm", "width_cm", "height_cm"))
        g = r[group_col]
        lines.append(CartonType(g, g, r["sku"], dims, float(r["line_weight_kg"]) / n, n,
                                r["this_side_up"].strip() == "1"))
    return lines


def oversize_reason(ts, box):
    m3 = sum(t.vol * t.count for t in ts) / 1e6
    kg = sum(t.kg * t.count for t in ts)
    for t in ts:
        if not any(l <= box.L + EPS and w <= box.W + EPS and h <= box.H + EPS for l, w, h in t.orients):
            return f"carton {t.sku} {t.dims[0]:g}x{t.dims[1]:g}x{t.dims[2]:g} cm fits in no allowed orientation"
    if m3 > box.m3 + EPS:
        return f"volume {m3:.1f} m3 exceeds one container ({box.m3:.1f} m3)"
    if kg > box.payload + EPS:
        return f"weight {kg:,.0f} kg exceeds payload ({box.payload:,.0f} kg)"
    return None


def split_group(g, ts, box):
    """Split an oversize group into its lines, so the search can place each line where it fits.
    Returns the parts and the fewest containers the group needs."""
    m3 = sum(t.vol * t.count for t in ts) / 1e6
    kg = sum(t.kg * t.count for t in ts)
    need = max(math.ceil(m3 / box.m3 - 1e-9), math.ceil(kg / box.payload - 1e-9))
    parts = {}
    for t in ts:
        # A line bigger than most of a container is cut by carton count.
        k = min(t.count, max(1, math.ceil(t.vol * t.count / 1e6 / (0.8 * box.m3)),
                             math.ceil(t.kg * t.count / (0.8 * box.payload))))
        for c in range(k):
            name = f"{g}#{len(parts) + 1}"
            parts[name] = [CartonType(t.po, name, t.sku, t.dims, t.kg, t.count // k + (c < t.count % k), t.upright)]
    return parts, need


def lower_bound(groups, box):
    m3 = sum(t.vol * t.count for ts in groups.values() for t in ts) / 1e6
    kg = sum(t.kg * t.count for ts in groups.values() for t in ts)
    return max(math.ceil(m3 / box.m3 - 1e-9), math.ceil(kg / box.payload - 1e-9), 1 if groups else 0)


# ---------------------------------------------------------------- self-check

def self_check(plans, lines, box, placed_pos):
    """plans: container -> list of (ctype, x, y, z, l, w, h). Mirrors the independent auditor."""
    errors = {"counts": [], "containment": [], "overlap": [], "upright": [], "support": [], "payload": []}
    got = {}
    for c, pl in plans.items():
        for t, *_ in pl:
            got[(t.po, t.sku)] = got.get((t.po, t.sku), 0) + 1
    for t in lines:
        if t.po in placed_pos and got.get((t.po, t.sku), 0) != t.count:
            errors["counts"].append(f"{t.po}/{t.sku}: {got.get((t.po, t.sku), 0)} of {t.count}")
    min_support = 1.0
    for c, pl in plans.items():
        lowest = 1.0
        kg = sum(t.kg for t, *_ in pl)
        if kg > box.payload + EPS:
            errors["payload"].append(f"container {c}: {kg:.0f} kg")
        tops = {}
        for t, x, y, z, l, w, h in pl:
            if x < -EPS or y < -EPS or z < -EPS or x + l > box.L + EPS or y + w > box.W + EPS or z + h > box.H + EPS:
                errors["containment"].append(f"container {c}: {t.sku} at {x:.1f},{y:.1f},{z:.1f}")
            if sorted((l, w, h)) != sorted(t.dims) or (t.upright and abs(h - t.dims[2]) > EPS):
                errors["upright"].append(f"container {c}: {t.sku} placed {l}x{w}x{h}")
            tops.setdefault(round(z + h, 4), []).append((x, y, l, w))
        srt = sorted(pl, key=lambda p: p[1])
        for i, (t, x, y, z, l, w, h) in enumerate(srt):
            for u, x2, y2, z2, l2, w2, h2 in srt[i + 1:]:
                if x2 >= x + l - EPS:
                    break
                if y < y2 + w2 - EPS and y2 < y + w - EPS and z < z2 + h2 - EPS and z2 < z + h - EPS:
                    errors["overlap"].append(f"container {c}: {t.sku} and {u.sku}")
            if z > EPS:
                area = sum(max(0.0, min(x + l, a + al) - max(x, a)) * max(0.0, min(y + w, b + bw) - max(y, b))
                           for a, b, al, bw in tops.get(round(z, 4), []))
                lowest = min(lowest, area / (l * w))
        if lowest < box.support - EPS:
            errors["support"].append(f"container {c}: a carton is {lowest:.0%} supported")
        min_support = min(min_support, lowest)
    checks = {k: {"ok": not v, "issues": v[:5]} for k, v in errors.items()}
    checks["min_support"] = round(min_support, 3)
    return checks


# ---------------------------------------------------------------- advisor: top up booked containers

@dataclass(frozen=True)
class LineRule:
    max_extra: float = math.inf
    multiple: int = 1
    value: float = 0.0


def read_limits(path):
    rules = {}
    for r in csv.DictReader(open(path, newline="")):
        cap = (r.get("max_extra") or "").strip()
        mult = (r.get("multiple") or "").strip()
        value = (r.get("value_per_carton") or "").strip()
        rules[(r["po"].strip(), r["sku"].strip())] = LineRule(int(float(cap)) if cap else math.inf,
                                                              max(1, int(float(mult))) if mult else 1,
                                                              float(value) if value else 0.0)
    return rules


def read_allocation(path, by_group):
    """po -> container number. Every group must be listed once; UNASSIGNED leaves it out."""
    where = {}
    for r in csv.DictReader(open(path, newline="")):
        where.setdefault(r["po"].strip(), set()).add(r["container"].strip())
    problems = [f"{g} is not in the allocation" for g in by_group if g not in where]
    problems += [f"{g} is in the allocation but not the shipment" for g in where if g not in by_group]
    problems += [f"{g} is in several containers {sorted(cs)}; advise needs one per group"
                 for g, cs in where.items() if len(cs) > 1]
    if problems:
        sys.exit("allocation: " + "; ".join(problems[:5]))
    containers = {}
    for g, (c,) in where.items():
        if c.upper() != "UNASSIGNED":
            containers.setdefault(int(c), set()).add(g)
    return dict(sorted(containers.items()))


def with_count(t, n):
    return CartonType(t.po, t.group, t.sku, t.dims, t.kg, n, t.upright)


def layout_key(placements):
    return hash(tuple(sorted((t.sku, t.po, round(x, 2), round(y, 2), round(z, 2)) for t, x, y, z, *_ in placements)))


def base_layouts(types, box, seed, want, deadline):
    """Distinct layouts that place every base carton: the fixed stage-2 policies first (mixed stacks, then
    single-SKU columns), then randomised ones. A column style that keeps failing is dropped."""
    out, seen, misses = [], set(), {True: 0, False: 0}
    attempt = 0
    while len(out) < want and attempt < 6 * want and time.time() < deadline:
        for mixed in (True, False):
            if misses[mixed] >= 6 and misses[mixed] > 2 * len(out) or len(out) >= want:
                continue
            rng = random.Random(seed * 7919 + attempt)
            plan = pack_one(types, box, policy(attempt, rng, mixed), rng)
            if plan.unplaced:
                plan = place_leftovers(plan.placements(), plan.unplaced, box)
            if plan.unplaced:
                misses[mixed] += 1
                continue
            pl = plan.placements()
            key = layout_key(pl)
            if key not in seen:
                seen.add(key)
                out.append(pl)
        attempt += 1
    return out


def fill_orders(types, rules):
    """Line orders for topping up. The same orders serve every objective, so each objective chooses
    from the same candidates and is never beaten on its own measure by another objective."""
    def side(t):
        return min(min(o[:2]) for o in t.orients)
    orders = [sorted(types, key=lambda t: (-side(t), -t.vol)),  # wide cartons need the few wide spots
              sorted(types, key=lambda t: -t.vol),
              sorted(types, key=lambda t: t.vol)]
    if any(rules.get((t.po, t.sku), LineRule()).value for t in types):
        orders.append(sorted(types, key=lambda t: (-rules.get((t.po, t.sku), LineRule()).value / t.vol, -t.vol)))
    return orders


def top_up(base, order, box, rules, objective):
    """Add extra cartons line by line into the space left above and beside the base load."""
    space = Space(box, base)
    kg_room = box.payload - sum(p[0].kg for p in base)
    extra = {}
    for t in order:
        rule = rules.get((t.po, t.sku), LineRule())
        if objective == "value" and rule.value <= 0:
            continue
        cap = rule.max_extra
        if t.kg > 0:
            cap = min(cap, int((kg_room + EPS) // t.kg))
        cap = int(min(cap, 10 ** 6))
        cap -= cap % rule.multiple
        k = space.place(t, cap)
        for _ in range(k % rule.multiple):
            # The newest cartons carry nothing yet, so they can come off again.
            space.pop()
        k -= k % rule.multiple
        if k:
            extra[t] = k
            kg_room -= k * t.kg
    return space.items, extra


def score(extra, rules, objective):
    m3 = sum(t.vol * k for t, k in extra.items()) / 1e6
    if objective == "cartons":
        return (sum(extra.values()), m3)
    if objective == "value":
        return (sum(rules.get((t.po, t.sku), LineRule()).value * k for t, k in extra.items()), m3)
    return (m3, sum(extra.values()))


def advise_container(types, box, rules, objective, seed, deadline, fallback=None, layouts=10, keep=3):
    """Best (placements, extras) for one container: several base layouts are screened with one top-up
    order, then the most promising get every order."""
    bases = base_layouts(types, box, seed, layouts, deadline)
    if not bases and fallback is not None:
        bases = [fallback]
    if not bases:
        return None
    orders = fill_orders(types, rules)
    tried = []
    for base in bases:
        if time.time() > deadline and tried:
            break
        pl, extra = top_up(base, orders[0], box, rules, objective)
        tried.append((sum(t.vol * k for t, k in extra.items()), len(tried), base, (pl, extra)))
    tried.sort(key=lambda e: (-e[0], e[1]))
    cands = [e[3] for e in tried]
    for _, _, base, _ in tried[:keep]:
        for order in orders[1:]:
            if time.time() > deadline:
                break
            cands.append(top_up(base, order, box, rules, objective))
    return max(cands, key=lambda c: score(c[1], rules, objective))


def load_totals(placements, box):
    rows = []
    for i, pl in placements.items():
        m3 = sum(t.vol for t, *_ in pl) / 1e6
        rows.append({"container": i, "cartons": len(pl), "m3": round(m3, 3), "fill": round(m3 / box.m3, 4),
                     "kg": round(sum(t.kg for t, *_ in pl), 1)})
    m3 = sum(r["m3"] for r in rows)
    return {"containers": rows, "cartons": sum(r["cartons"] for r in rows), "m3": round(m3, 3),
            "fill": round(m3 / (box.m3 * len(rows)), 4) if rows else 0.0, "kg": round(sum(r["kg"] for r in rows), 1)}


def advise_shipment(args):
    t0 = time.time()
    deadline = t0 + args.time_limit
    box, lines, by_group, groups, oversize, families, lb = load_shipment(args)
    rules = read_limits(args.limits) if args.limits else {}
    if args.objective == "value" and not args.limits:
        sys.exit("--objective value needs --limits with value_per_carton")
    if args.allocation:
        loads = {i: [t for g in sorted(gs) for t in by_group[g]] for i, gs in read_allocation(args.allocation, by_group).items()}
        groups = {g: by_group[g] for ts in loads.values() for g in {t.group for t in ts}}
        fallbacks, source = {}, "given"
    else:
        final = assign_groups(args, box, groups, families, lb, t0)
        loads = {i: [t for g in sorted(members) for t in groups[g]] for i, (members, _) in final.items()}
        fallbacks = {i: plan.placements() for i, (_, plan) in final.items() if not plan.unplaced}
        source = "planned"
    lb = lower_bound(groups, box) if args.allocation else lb

    # Leave a tenth of the time for the tolerance re-packs and the outputs.
    budget_end = deadline - 0.1 * args.time_limit
    base_pl, placements, extras, unplaced = {}, {}, {}, {}
    todo = list(loads.items())
    for n, (i, types) in enumerate(todo):
        share = (budget_end - time.time()) / (len(todo) - n)
        res = advise_container(types, box, rules, args.objective, args.seed, time.time() + share, fallbacks.get(i))
        if res is None:
            plan = pack_complete(types, box, args.seed, 4)
            placements[i], extras[i], unplaced[i] = plan.placements(), {}, plan.unplaced
            base_pl[i] = placements[i]
            continue
        placements[i], extras[i] = res
        base_pl[i] = placements[i][:len(placements[i]) - sum(extras[i].values())]
        unplaced[i] = {}

    added = {}
    for ex in extras.values():
        for t, k in ex.items():
            added[(t.po, t.sku)] = added.get((t.po, t.sku), 0) + k
    advised_lines = [with_count(t, t.count + added.get((t.po, t.sku), 0)) for t in lines]
    advised_loads = {i: [with_count(t, t.count + extras[i].get(t, 0)) for t in types] for i, types in loads.items()}
    tolerance = tolerance_check(advised_loads, box, args.seed)
    checks = self_check(placements, advised_lines, box, {t.po for ts in loads.values() for t in ts})
    final = {i: ({t.group for t in types}, ContainerPlan([], unplaced[i], placements[i])) for i, types in loads.items()}

    rows = []
    for t in lines:
        homes = [i for i, types in loads.items() if any(u.po == t.po and u.sku == t.sku for u in types)]
        for i in homes or ["UNASSIGNED"]:
            part = [u for u in loads.get(i, []) if u.po == t.po and u.sku == t.sku]
            base_n = sum(u.count for u in part) if part else t.count
            k = sum(extras[i].get(u, 0) for u in part)
            rows.append({"po": t.po, "sku": t.sku, "container": i, "base_cartons": base_n, "extra_cartons": k,
                         "new_cartons": base_n + k, "extra_m3": round(k * t.vol / 1e6, 4), "extra_kg": round(k * t.kg, 2)})
    base, advised = load_totals(base_pl, box), load_totals(placements, box)
    advice = {"objective": args.objective, "allocation_source": source, "base": base, "advised": advised,
              "extra_cartons": sum(r["extra_cartons"] for r in rows),
              "extra_m3": round(sum(r["extra_m3"] for r in rows), 3),
              "extra_kg": round(sum(r["extra_kg"] for r in rows), 1)}
    if args.objective == "value" or rules:
        advice["extra_value"] = round(sum(r["extra_cartons"] * rules.get((r["po"], r["sku"]), LineRule()).value
                                          for r in rows), 2)
    advice["lines"] = rows
    return box, lines, by_group, groups, oversize, lb, final, placements, tolerance, checks, time.time() - t0, advice


# ---------------------------------------------------------------- main

def load_shipment(args):
    box = Box(args.length - args.clearance, args.width - args.clearance, args.height - args.clearance,
              args.payload, args.support)
    lines = read_shipment(args.shipment, args.group_col)
    by_group = {}
    for t in lines:
        by_group.setdefault(t.group, []).append(t)
    oversize, groups, families = [], {}, {}
    for g, ts in by_group.items():
        reason = oversize_reason(ts, box)
        if reason is None:
            groups[g] = ts
            continue
        oversize.append({"group": g, "m3": round(sum(t.vol * t.count for t in ts) / 1e6, 2),
                         "kg": round(sum(t.kg * t.count for t in ts), 1), "reason": reason})
        if args.split_oversize and "orientation" not in reason:
            parts, need = split_group(g, ts, box)
            groups.update(parts)
            families[g] = need
    # Bound over all cargo being placed, split parts included. Summing per-family ceilings would overstate it.
    lb = max(lower_bound(groups, box), max(families.values(), default=0))
    return box, lines, by_group, groups, oversize, families, lb


def assign_groups(args, box, groups, families, lb, t0):
    """Stage 1 plus a final, harder stage 2 per container. Returns {container: (groups, plan)}."""
    deadline = t0 + args.time_limit
    planner = Planner(groups, box, args.seed, families)
    n = max(lb, 1) if groups else 0
    assignment, costs = Assignment([]), []
    while groups:
        remaining = deadline - time.time() - 0.1 * args.time_limit
        assignment, costs = planner.search(n, time.time() + max(5.0, 0.8 * remaining))
        if sum(costs) <= 0 or n >= len(groups):
            break
        n += 1

    final = {}
    for i, members in enumerate(assignment.bins, 1):
        types = [t for g in sorted(members) for t in groups[g]]
        tries = max(16, planner.cache.get(frozenset(members), (0, 0))[1])
        final[i] = (members, pack_container(types, box, args.seed, tries))
    return final


def tolerance_check(loads, box, seed):
    """loads: container -> carton types. Re-packs each with every carton grown 1, 2 and 3 mm."""
    tolerance = {}
    for mm in (1, 2, 3):
        res = {}
        for i, types in loads.items():
            p = pack_container([t.grown(mm / 10) for t in types], box, seed, 20)
            res[str(i)] = {"fits": not p.unplaced, "unplaced": sum(p.unplaced.values())}
        tolerance[f"{mm}mm"] = res
    return tolerance


def plan_shipment(args):
    t0 = time.time()
    box, lines, by_group, groups, oversize, families, lb = load_shipment(args)
    final = assign_groups(args, box, groups, families, lb, t0)
    tolerance = tolerance_check({i: [t for g in sorted(members) for t in groups[g]] for i, (members, _) in final.items()},
                                box, args.seed)
    placements = {i: plan.placements() for i, (_, plan) in final.items()}
    checks = self_check(placements, lines, box, {t.po for ts in groups.values() for t in ts})
    return box, lines, by_group, groups, oversize, lb, final, placements, tolerance, checks, time.time() - t0


def write_outputs(out, args, box, by_group, groups, oversize, lb, final, placements, tolerance, checks, secs, advice=None):
    out.mkdir(parents=True, exist_ok=True)
    where = {}
    for i, (members, _) in final.items():
        for g in members:
            where.setdefault(groups[g][0].po, set()).add(i)
    with open(out / "allocation.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["po", "container"])
        for g in by_group:
            for c in sorted(where.get(g, [])) or ["UNASSIGNED"]:
                w.writerow([g, c])
    with open(out / "placements.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["container", "po", "sku", "x", "y", "z", "l", "w", "h"])
        for i, pl in placements.items():
            for t, *xyz in pl:
                w.writerow([i, t.po, t.sku, *(round(v, 4) for v in xyz)])
    detail = []
    for i, (members, plan) in final.items():
        pl = placements[i]
        m3 = sum(t.vol for t, *_ in pl) / 1e6
        detail.append({
            "container": i, "groups": sorted({t.po for t, *_ in pl}), "cartons": len(pl),
            "m3": round(m3, 2), "fill": round(m3 / box.m3, 4), "kg": round(sum(t.kg for t, *_ in pl), 1),
            "spare_length_cm": round(box.L - max((p[1] + p[4] for p in pl), default=0), 1),
            "spare_width_cm": round(box.W - max((p[2] + p[5] for p in pl), default=0), 1),
            "spare_height_cm": round(box.H - max((p[3] + p[6] for p in pl), default=0), 1),
            "unplaced_cartons": sum(plan.unplaced.values())})
    ok = all(v["ok"] for k, v in checks.items() if k != "min_support")
    summary = {"lower_bound": lb, "containers": len(final), "proven_optimal": len(final) == lb,
               "oversize_groups": oversize, "split_oversize": args.split_oversize,
               "containers_detail": detail, "tolerance": tolerance, "checks": checks,
               "checks_passed": ok, "seconds": round(secs, 1)}
    text = report(summary, box, args)
    if advice is not None:
        advice = dict(advice)
        rows = advice.pop("lines")
        with open(out / "advice.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["po", "sku", "container", "base_cartons",
                                                                         "extra_cartons", "new_cartons", "extra_m3", "extra_kg"])
            w.writeheader()
            w.writerows(rows)
        summary["advice"] = advice
        text += advisor_report(advice, rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "report.md").write_text(text)
    return ok


def report(s, box, args):
    n, lb = s["containers"], s["lower_bound"]
    cartons = sum(d["cartons"] for d in s["containers_detail"])
    m3 = sum(d["m3"] for d in s["containers_detail"])
    kg = sum(d["kg"] for d in s["containers_detail"])
    out = ["# Load Plan", "", "**Executive Summary:**",
           f"- Cartons packed: {cartons:,}",
           f"- Containers used: {n} ({box.L:g} x {box.W:g} x {box.H:g} cm, payload {box.payload:,.0f} kg)",
           f"- Average utilization: {100 * m3 / (box.m3 * n) if n else 0:.1f}%",
           f"- Total weight: {kg:,.0f} kg",
           "- Algorithm: SKU columns with headroom fill, lanes along the length, search over group assignments",
           "", "**Container Count and Bounds:**", "",
           f"- Lower bound: {lb} (max of volume bound and payload bound"
           + (", plus the minimum containers for each split oversize group)" if s["split_oversize"] and s["oversize_groups"] else ")"),
           f"- Containers in this plan: {n}, gap to lower bound: {n - lb}"]
    if n == lb:
        out.append("- The plan meets the lower bound, so it is optimal.")
    else:
        out.append(f"- Best found, not proven optimal. The packer did not find a {lb}-container plan; "
                   "that is a search result, not a proof that it is impossible.")
    for o in s["oversize_groups"]:
        fate = "split across containers (--split-oversize)" if s["split_oversize"] and "orientation" not in o["reason"] \
            else "left UNASSIGNED; offer a split (--split-oversize) or bigger equipment"
        out.append(f"- Group {o['group']} cannot ship whole: {o['reason']}. It is {fate}.")
    out += ["", "**Group Assignment:**", "",
            "| Container | Groups (POs) | Cartons | Volume | Weight | Fill | Spare L / W / H |",
            "|-----------|--------------|---------|--------|--------|------|-----------------|"]
    for d in s["containers_detail"]:
        out.append(f"| {d['container']} | {', '.join(d['groups'])} | {d['cartons']:,} | {d['m3']:.1f} m³ | "
                   f"{d['kg']:,.0f} kg | {100 * d['fill']:.1f}% | {d['spare_length_cm']:g} cm / "
                   f"{d['spare_width_cm']:g} cm / {d['spare_height_cm']:g} cm |")
    out += ["", "**Tolerance:** each container re-packed with every carton grown in each dimension.", "",
            "| Container | +1 mm | +2 mm | +3 mm |", "|-----------|-------|-------|-------|"]
    for d in s["containers_detail"]:
        c = str(d["container"])
        cells = [("fits" if s["tolerance"][k][c]["fits"] else f"{s['tolerance'][k][c]['unplaced']} cartons do not fit")
                 for k in ("1mm", "2mm", "3mm")]
        out.append(f"| {c} | " + " | ".join(cells) + " |")
    if any(not r["fits"] for t in s["tolerance"].values() for r in t.values()):
        out.append("\nWhere a grown load does not fit, the plan is tight: confirm carton sizes before loading, "
                   "or keep a fallback with one more container ready.")
    ck = s["checks"]
    out += ["", "**Checks Run:** " + ", ".join(f"{k} {'ok' if v['ok'] else 'FAILED'}"
                                               for k, v in ck.items() if k != "min_support")
            + f", minimum support {100 * ck['min_support']:.0f}% (threshold {100 * args.support:.0f}%).",
            "", "Carton positions for every container are in placements.csv (cm, z up)."]
    return "\n".join(out) + "\n"


def advisor_report(a, rows):
    b, v = a["base"], a["advised"]
    out = ["", "**Advisor:** the same containers and group assignment, topped up with extra cartons of lines already "
           f"in each container (objective: {a['objective']}, allocation {a['allocation_source']}). "
           "The tables above describe the advised load.", "",
           "| Container | Base fill | Advised fill | Base m³ | Advised m³ | Base kg | Advised kg |",
           "|-----------|-----------|--------------|---------|------------|---------|------------|"]
    for cb, cv in zip(b["containers"], v["containers"]):
        out.append(f"| {cb['container']} | {100 * cb['fill']:.1f}% | {100 * cv['fill']:.1f}% | {cb['m3']:.1f} | "
                   f"{cv['m3']:.1f} | {cb['kg']:,.0f} | {cv['kg']:,.0f} |")
    out.append(f"| Total | {100 * b['fill']:.1f}% | {100 * v['fill']:.1f}% | {b['m3']:.1f} | {v['m3']:.1f} | "
               f"{b['kg']:,.0f} | {v['kg']:,.0f} |")
    out += ["", f"Extra: {a['extra_cartons']:,} cartons, {a['extra_m3']:.2f} m³, {a['extra_kg']:,.0f} kg"
            + (f", value {a['extra_value']:,.2f}" if "extra_value" in a else "") + ".", ""]
    grown = sorted((r for r in rows if r["extra_cartons"]), key=lambda r: -r["extra_m3"])
    if grown:
        out += ["| PO | SKU | Container | Base | Extra | New | Extra m³ | Extra kg |",
                "|----|-----|-----------|------|-------|-----|----------|----------|"]
        out += [f"| {r['po']} | {r['sku']} | {r['container']} | {r['base_cartons']} | {r['extra_cartons']} | "
                f"{r['new_cartons']} | {r['extra_m3']:.3f} | {r['extra_kg']:,.1f} |" for r in grown]
    else:
        out.append("No line can grow without breaking a rule.")
    out += ["", "Extras are checked like the base load: every carton placed, upright kept, support and payload held. "
            "Order quantities are a recommendation; confirm them with the supplier before booking changes."]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shipment")
    ap.add_argument("--out", required=True)
    ap.add_argument("--length", type=float, default=1203.5)
    ap.add_argument("--width", type=float, default=235.2)
    ap.add_argument("--height", type=float, default=269.5)
    ap.add_argument("--payload", type=float, default=28620)
    ap.add_argument("--group-col", default="po")
    ap.add_argument("--split-oversize", action="store_true")
    ap.add_argument("--time-limit", type=float, default=480)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--support", type=float, default=0.8)
    ap.add_argument("--clearance", type=float, default=0.0)
    ap.add_argument("--advise", action="store_true", help="top up the booked containers with extra cartons")
    ap.add_argument("--allocation", help="advise: po,container CSV to keep fixed (default: plan first)")
    ap.add_argument("--objective", choices=("volume", "cartons", "value"), default="volume")
    ap.add_argument("--limits", help="advise: po,sku,max_extra,multiple,value_per_carton CSV")
    args = ap.parse_args()
    if not args.advise and (args.allocation or args.limits or args.objective != "volume"):
        ap.error("--allocation, --limits and --objective apply only with --advise")
    if args.advise:
        *res, advice = advise_shipment(args)
    else:
        res, advice = plan_shipment(args), None
    box, lines, by_group, groups, oversize, lb, final, placements, tolerance, checks, secs = res
    ok = write_outputs(pathlib.Path(args.out), args, box, by_group, groups, oversize, lb, final, placements,
                       tolerance, checks, secs, advice)
    print(f"containers {len(final)}, lower bound {lb}, oversize {len(oversize)}, checks {'passed' if ok else 'FAILED'},"
          f" {secs:.0f}s, outputs in {args.out}")
    if advice:
        print(f"advised fill {100 * advice['base']['fill']:.1f}% -> {100 * advice['advised']['fill']:.1f}%, "
              f"+{advice['extra_cartons']} cartons, +{advice['extra_m3']:.2f} m3")
    if not ok:
        print(json.dumps({k: v for k, v in checks.items() if k != "min_support" and not v["ok"]}, indent=1),
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
