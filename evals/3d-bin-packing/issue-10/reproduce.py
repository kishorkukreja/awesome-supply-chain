"""Reproduce issue #10: 3d-bin-packing skill on a tight multi-PO 40' HC load.

Runs the skill's own heuristics (BLB, Layer Building, Extreme Point), loaded
verbatim from skills/3d-bin-packing/SKILL.md, on an anonymized real shipment:
22 POs, 55 lines, 3,973 cartons, 198.8 m3. A commercial planner loads it in
3 containers with every PO whole.

  T1  largest PO alone (62.8 m3, 82% of one container)
  T2  full shipment, every PO must stay whole in one container
  T3  full shipment, PO constraint dropped (easiest case for the skill)
  T4  lane-based certificate that the largest PO fits in one container, and its tolerance
  T5  placement auditor passes the T4 plan and catches four planted faults

Usage: python3 reproduce.py   (stdlib only, ~1 min)
"""
import collections
import csv
import itertools
import math
import pathlib
import re
import signal
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "skills" / "3d-bin-packing" / "SKILL.md"

# 40' HC internal dims (cm) and payload (kg), as used by the reference planner
C = (1203.5, 235.2, 269.5, 28620.0)
CAP_M3 = C[0] * C[1] * C[2] / 1e6
REFERENCE = "LoadViewer Solver: 3 containers (91.5% / 91.4% / 77.7%), all POs whole"

# --- load skill code verbatim: blocks [1]=BLB, [2]=layer, [3]=extreme point
ns = {}
for block in re.findall(r"```python\n(.*?)```", SKILL.read_text(), re.S)[1:4]:
    exec(block, ns)
Box = ns["Box"]
ALGS = [
    ("BLB", ns["bottom_left_back_packing"], False),
    ("Layer", ns["layer_building_algorithm"], None),
    ("ExtremePoint", ns["extreme_point_algorithm"], False),
]

ROWS = list(csv.DictReader(open(HERE / "shipment.csv")))
POS = list(dict.fromkeys(r["po"] for r in ROWS))


def boxes(pos=None):
    out = []
    for r in ROWS:
        if pos is not None and r["po"] not in pos:
            continue
        n = int(r["cartons"])
        for i in range(n):
            b = Box(float(r["length_cm"]), float(r["width_cm"]), float(r["height_cm"]),
                    float(r["line_weight_kg"]) / n, f"{r['po']}|{r['sku']}|{i}")
            b.po = r["po"]
            out.append(b)
    return out


PO_M3 = {p: sum(b.volume() for b in boxes({p})) / 1e6 for p in POS}


class Timeout(Exception):
    pass


def _alarm(*_):
    raise Timeout()


signal.signal(signal.SIGALRM, _alarm)


def run(alg, rot, items, limit=300):
    signal.alarm(limit)
    try:
        return alg(items, C) if rot is None else alg(items, C, rot)
    except Timeout:
        return None
    finally:
        signal.alarm(0)


def fills(res):
    out = []
    for c in res["containers"]:
        v = sum(i["dimensions"][0] * i["dimensions"][1] * i["dimensions"][2] for i in c["items"]) / 1e6
        out.append(f"{100 * v / CAP_M3:.0f}%")
    return ", ".join(out)


def violations(res):
    """Physical checks the skill never runs on its own output."""
    bad = collections.Counter()
    for c in res["containers"]:
        its = c["items"]
        for a in its:
            x, y, z = a["position"]
            l, w, h = a["dimensions"]
            if abs(h - a["box"].height) > 1e-9:
                bad["tipped"] += 1
            if z > 1e-6:
                area = 0.0
                for b in its:
                    bx, by, bz = b["position"]
                    bl, bw, bh = b["dimensions"]
                    if abs(bz + bh - z) < 1e-6:
                        area += max(0, min(x + l, bx + bl) - max(x, bx)) * max(0, min(y + w, by + bw) - max(y, by))
                if area < 1e-6:
                    bad["floating"] += 1
                elif area < 0.7 * l * w:
                    bad["support<70%"] += 1
    return dict(bad) or "none"


def t1():
    big = max(POS, key=PO_M3.get)
    print(f"T1  Largest PO {big} alone: {len(boxes({big}))} cartons, {PO_M3[big]:.1f} m3 "
          f"({100 * PO_M3[big] / CAP_M3:.0f}% of one container). Lower bound: 1 container.")
    for name, alg, rot in ALGS + [("BLB", ns["bottom_left_back_packing"], True),
                                  ("ExtremePoint", ns["extreme_point_algorithm"], True)]:
        res = run(alg, rot, boxes({big}))
        print(f"    {name:12s} allow_rotation={rot!s:5s}: {res['num_containers']} containers [{fills(res)}]"
              f"  violations: {violations(res)}")
    return big


def t2():
    print("\nT2  Full shipment, every PO whole (first-fit decreasing over POs; "
          "the skill's heuristic decides whether a container still fits)")
    order = sorted(POS, key=lambda p: -PO_M3[p])
    for name, alg, rot in ALGS:
        conts, not_whole = [], []
        for p in order:
            for c in conts:
                res = run(alg, rot, boxes(set(c) | {p}))
                if res and res["num_containers"] == 1:
                    c.append(p)
                    break
            else:
                res = run(alg, rot, boxes({p}))
                (conts.append([p]) if res and res["num_containers"] == 1 else not_whole.append(p))
        pct = ", ".join(f"{100 * sum(PO_M3[p] for p in c) / CAP_M3:.0f}%" for c in conts)
        print(f"    {name:12s}: {len(conts)} containers [{pct}] + 'not packable whole': {not_whole}")


def t3():
    print("\nT3  Full shipment, PO constraint dropped")
    for name, alg, rot in ALGS:
        res = run(alg, rot, boxes(), limit=900)
        split = sum(1 for p in POS
                    if len({k for k, c in enumerate(res["containers"]) for i in c["items"] if i["box"].po == p}) > 1)
        print(f"    {name:12s}: {res['num_containers']} containers [{fills(res)}], POs split: {split}/22")


def lane_plan(big, grow=0.0):
    """Upright stacks in lanes along the container length; lanes sized to carton depth."""
    L, W, H, _ = C
    stacks = []
    for r in ROWS:
        if r["po"] != big:
            continue
        l, w, h = (float(r[k]) + grow for k in ("length_cm", "width_cm", "height_cm"))
        n, tiers = int(r["cartons"]), int(H // (float(r["height_cm"]) + grow))
        stacks += [(l, w, h, min(tiers, n - k * tiers), r["sku"]) for k in range(math.ceil(n / tiers))]
    kinds = sorted({s[1] for s in stacks}, reverse=True)
    for counts in itertools.product(range(16), repeat=len(kinds)):
        widths = [wd for wd, c in zip(kinds, counts) for _ in range(c)]
        if not widths or sum(widths) > W:
            continue
        lanes = [{"w": wd, "free": L, "stacks": []} for wd in widths]
        for st in sorted(stacks, key=lambda s: (-s[1], -s[0])):
            fit = [ln for ln in lanes if ln["w"] >= st[1] and ln["free"] >= st[0]]
            if not fit:
                break
            ln = min(fit, key=lambda ln: (ln["w"], ln["free"]))
            ln["stacks"].append(st)
            ln["free"] -= st[0]
        else:
            return lanes
    return None


def placements(big, lanes):
    out, y = [], 0.0
    for ln in lanes:
        x = 0.0
        for l, w, h, n, sku in ln["stacks"]:
            out += [{"c": 1, "po": big, "sku": sku, "pos": [x, y, k * h], "size": [l, w, h]} for k in range(n)]
            x += l
        y += ln["w"]
    return out


def t4(big):
    L, W = C[0], C[1]
    print()
    for grow_mm in (0, 1, 2, 3):
        lanes = lane_plan(big, grow_mm / 10)
        if lanes:
            used = max(L - ln["free"] for ln in lanes)
            print(f"T4  {big} with cartons +{grow_mm} mm per dimension: fits whole in ONE container, "
                  f"{len(lanes)} lanes ({sum(ln['w'] for ln in lanes):.1f} of {W} cm wide), longest lane {used:.0f} of {L} cm")
        else:
            print(f"T4  {big} with cartons +{grow_mm} mm per dimension: lane method finds no fit")
    return placements(big, lane_plan(big))


def t5(plan):
    """The placement auditor must pass a known-good plan and catch each planted fault."""
    import copy, json, subprocess, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROWS[0].keys())
        w.writeheader()
        w.writerows(r for r in ROWS if r["po"] == plan[0]["po"])
    ship = f.name

    def audit(pl):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(pl, f)
        return subprocess.run([sys.executable, str(HERE / "audit_placements.py"), ship, f.name],
                              capture_output=True, text=True).stdout.strip()

    faults = {
        "overlapping pairs": lambda p: p[1].update(pos=list(p[0]["pos"])),
        "this_side_up tipped": lambda p: p[2].update(size=sorted(p[2]["size"])),
        "count mismatch": lambda p: p.pop(3),
        "outside container": lambda p: p[4]["pos"].__setitem__(0, C[0] - p[4]["size"][0] + 1),
    }
    print(f"\nT5  Placement auditor on the T4 plan: {audit(plan).splitlines()[-1].strip()}")
    for name, inject in faults.items():
        bad = copy.deepcopy(plan)
        inject(bad)
        print(f"    planted {name}: {'caught' if name in audit(bad) else 'MISSED'}")


if __name__ == "__main__":
    t0 = time.time()
    total = sum(PO_M3.values())
    print(f"Shipment: {len(POS)} POs, {len(ROWS)} lines, {len(boxes())} cartons, {total:.1f} m3. "
          f"Volume lower bound: {math.ceil(total / CAP_M3)} containers.")
    print(f"Reference: {REFERENCE}\n")
    big = t1()
    t2()
    t3()
    t5(t4(big))
    print(f"\n({time.time() - t0:.0f}s)")
