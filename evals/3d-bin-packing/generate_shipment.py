"""Generate a multi-PO 40' HC shipment with a planted feasible load plan.

Each planted container is filled with upright single-SKU stacks laid in lanes
along its length, so the plan is feasible by construction. Cartons are then
grouped into POs that never cross a planted container. The volume lower bound
equals the planted count, so the optimum container count is known exactly.

    python3 generate_shipment.py OUT_DIR [--containers 8] [--seed 7] [--merge-big-pos]

--merge-big-pos renames the largest PO of two planted containers to one PO
id. That PO then exceeds one container's volume, so "every PO whole" becomes
provably impossible for it, and the right answer is to say so.

Writes OUT_DIR/shipment.csv (what the planner sees), OUT_DIR/truth.json
(planted assignment and bounds) and OUT_DIR/planted_placements.json (carton
coordinates of the planted plan, checkable with issue-10/audit_placements.py).
Never show truth or placements to the planner.
"""
import argparse
import csv
import json
import math
import pathlib
import random

L, W, H, PAYLOAD = 1203.5, 235.2, 269.5, 28620.0
CAP_M3 = L * W * H / 1e6
HEIGHTS = [26.0, 29.5, 33.0, 37.5, 44.0, 53.0, 66.0, 88.0]  # 3 to 10 tiers fill 260-266 cm


def half(x):
    return math.floor(x * 2) / 2


def build_container(rng, target_fill):
    """Lanes across the width, single-SKU stacks along each lane. Returns lines with carton positions."""
    lanes, used_w = [], 0.0
    while W - used_w >= 15:
        w = half(min(rng.uniform(15, 60), W - used_w))
        lanes.append((used_w, w))
        used_w += w
    lines = []
    for y, w in lanes:
        x = 0.0
        while L - x >= 20:
            l = half(min(rng.choice([rng.uniform(20, 60), rng.uniform(60, 140)]), L - x))
            h = rng.choice(HEIGHTS)
            stacks = max(1, min(int((L - x) // l), rng.randint(1, 12)))
            depth = half(w - rng.uniform(0, 1.5))
            pos = [(x + s * l, y, t * h) for s in range(stacks) for t in range(int(H // h))]
            lines.append({"L": l, "W": depth, "H": h, "up": rng.random() < 0.9, "pos": pos})
            x += stacks * l
    rng.shuffle(lines)
    vol, kept = 0.0, []
    for ln in lines:
        unit = ln["L"] * ln["W"] * ln["H"] / 1e6
        room = int((target_fill * CAP_M3 - vol) // unit)
        if room <= 0:
            break
        if room < len(ln["pos"]):
            ln["pos"] = sorted(ln["pos"], key=lambda p: p[2])[:room]  # drop top tiers first, stays supported
        ln["cartons"] = len(ln["pos"])
        kept.append(ln)
        vol += unit * ln["cartons"]
    return kept


def split_into_pos(rng, lines, big):
    n = rng.randint(3, 9)
    weights = [rng.uniform(1, 3) for _ in range(n)]
    if big:
        weights[0] = sum(weights) * 2.2  # one PO takes about two thirds of the container
    vols = [ln["L"] * ln["W"] * ln["H"] * ln["cartons"] for ln in lines]
    order = sorted(range(len(lines)), key=lambda i: -vols[i])
    target = [w / sum(weights) * sum(vols) for w in weights]
    got = [0.0] * n
    po_of = {}
    for i in order:
        k = max(range(n), key=lambda k: target[k] - got[k])
        po_of[i] = k
        got[k] += vols[i]
    return [po_of[i] for i in range(len(lines))], n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--containers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--merge-big-pos", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    rows, planted, po_ids, big_po_of, placements = [], {}, set(), {}, []
    sku = 100000
    for c in range(1, a.containers + 1):
        lines = build_container(rng, rng.uniform(0.885, 0.915))
        po_idx, n = split_into_pos(rng, lines, big=c % 2 == 1)
        names = []
        for _ in range(n):
            while True:
                pid = f"{rng.randint(4100000, 4999999)}{rng.choice(['PBM', 'WSM', 'WEM', 'PKR'])}"
                if pid not in po_ids:
                    po_ids.add(pid)
                    names.append(pid)
                    break
        if c % 2 == 1:
            big_po_of[c] = names[0]
        for ln, k in zip(lines, po_idx):
            sku += rng.randint(1, 900)
            kg = round(ln["L"] * ln["W"] * ln["H"] / 1e6 * rng.uniform(90, 260), 3)
            rows.append({"po": names[k], "sku": sku, "length_cm": ln["L"], "width_cm": ln["W"],
                         "height_cm": ln["H"], "line_weight_kg": round(kg * ln["cartons"], 3),
                         "cartons": ln["cartons"], "this_side_up": int(ln["up"])})
            planted[names[k]] = c
            placements += [{"c": c, "po": names[k], "sku": str(sku), "pos": list(p), "size": [ln["L"], ln["W"], ln["H"]]}
                           for p in ln["pos"]]
    merged = None
    if a.merge_big_pos:
        c1, c2 = sorted(big_po_of)[:2]
        keep, drop = big_po_of[c1], big_po_of[c2]
        for r in rows + placements:
            if r["po"] == drop:
                r["po"] = keep
        planted.pop(drop)
        merged = keep
    rows = [r for r in rows if r["cartons"] > 0]
    rng.shuffle(rows)
    vol = {}
    for r in rows:
        vol[r["po"]] = vol.get(r["po"], 0) + r["length_cm"] * r["width_cm"] * r["height_cm"] * r["cartons"] / 1e6
    total = sum(vol.values())
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "shipment.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    truth = {
        "containers_planted": a.containers,
        "volume_lower_bound": math.ceil(total / CAP_M3),
        "total_m3": round(total, 2),
        "pos": len(vol),
        "cartons": sum(r["cartons"] for r in rows),
        "po_over_one_container": [p for p, v in vol.items() if v > CAP_M3],
        "merged_po": merged,
        "planted_assignment": planted if not merged else {p: c for p, c in planted.items() if p != merged},
        "seed": a.seed,
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1))
    (out / "planted_placements.json").write_text(json.dumps(placements))
    print(json.dumps({k: v for k, v in truth.items() if k != "planted_assignment"}))


if __name__ == "__main__":
    main()
