#!/usr/bin/env python3
"""Plan a shipment into containers, keeping each group (PO) whole, and check the result.

    python3 load_plan.py SHIPMENT.csv --out DIR [--length 1203.5 --width 235.2 --height 269.5
        --payload 28620] [--group-col po] [--split-oversize] [--time-limit 480] [--seed 1]
        [--support 0.8] [--clearance 0]

Method: per container, cartons are built into columns (one SKU stacked to the roof, flat
cartons riding in the headroom), columns are laid in lanes along the container length,
and leftovers go on column tops. Groups are assigned to containers by first-fit
decreasing from the lower bound, then repaired by annealed moves and swaps until every
container packs. With --split-oversize, an oversize group's lines are assigned one by one,
each only into a container that already holds another line of that group.
Writes allocation.csv, placements.csv, summary.json and report.md to DIR.
Standard library only.
"""
import argparse
import csv
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
        return out


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


POLICIES = [Policy(o, d, t, 0.0) for t in (3.0, math.inf, 1.5, 6.0) for d in (False, True)
            for o in ("match", "narrow", "height")]


def policy(attempt, rng):
    if attempt < len(POLICIES):
        return POLICIES[attempt]
    return Policy(rng.choice(("match", "narrow", "height")), rng.random() < 0.5, rng.choice((1.5, 3.0, 6.0, math.inf)), 0.3)


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


def pack_container(types, box, seed=1, tries=1):
    """Stage 2: pack the given carton types into one container. Best of `tries` attempts."""
    best = None
    for attempt in range(tries):
        rng = random.Random(seed * 7919 + attempt)
        pol = policy(attempt, rng)
        cols = build_columns(types, box.H, pol.orient)
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
        plan = ContainerPlan(lanes, unplaced)
        if best is None or plan.unplaced_vol < best.unplaced_vol:
            best = plan
        if not unplaced:
            break
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


# ---------------------------------------------------------------- main

def plan_shipment(args):
    t0 = time.time()
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
    tolerance = {}
    for mm in (1, 2, 3):
        res = {}
        for i, (members, _) in final.items():
            grown = [t.grown(mm / 10) for g in sorted(members) for t in groups[g]]
            p = pack_container(grown, box, args.seed, 20)
            res[str(i)] = {"fits": not p.unplaced, "unplaced": sum(p.unplaced.values())}
        tolerance[f"{mm}mm"] = res

    placements = {i: plan.placements() for i, (_, plan) in final.items()}
    checks = self_check(placements, lines, box, {t.po for ts in groups.values() for t in ts})
    return box, lines, by_group, groups, oversize, lb, final, placements, tolerance, checks, time.time() - t0


def write_outputs(out, args, box, by_group, groups, oversize, lb, final, placements, tolerance, checks, secs):
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
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "report.md").write_text(report(summary, box, args))
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
    args = ap.parse_args()
    box, lines, by_group, groups, oversize, lb, final, placements, tolerance, checks, secs = plan_shipment(args)
    ok = write_outputs(pathlib.Path(args.out), args, box, by_group, groups, oversize, lb, final, placements,
                       tolerance, checks, secs)
    print(f"containers {len(final)}, lower bound {lb}, oversize {len(oversize)}, checks {'passed' if ok else 'FAILED'},"
          f" {secs:.0f}s, outputs in {args.out}")
    if not ok:
        print(json.dumps({k: v for k, v in checks.items() if k != "min_support" and not v["ok"]}, indent=1),
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
