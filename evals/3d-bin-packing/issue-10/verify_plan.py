"""Score a PO-to-container allocation against the shipment.

Usage: python3 verify_plan.py shipment.csv allocation.csv [more allocation.csv ...]

allocation.csv needs columns `po` and `container`. A PO listed more than once
in different containers counts as split. A container value that is empty or
not a number (e.g. "none", "unpacked") counts as left out.
"""
import collections
import csv
import math
import sys

CAP_M3 = 1203.5 * 235.2 * 269.5 / 1e6
CAP_KG = 28620.0
TIGHTEST_KNOWN = 0.9151  # highest single-container fill in the reference 3D plan


def load_shipment(path):
    vol, kg = collections.defaultdict(float), collections.defaultdict(float)
    for r in csv.DictReader(open(path)):
        vol[r["po"]] += float(r["length_cm"]) * float(r["width_cm"]) * float(r["height_cm"]) * int(r["cartons"]) / 1e6
        kg[r["po"]] += float(r["line_weight_kg"])
    return vol, kg


def score(vol, kg, path):
    where = collections.defaultdict(set)
    for r in csv.DictReader(open(path)):
        po, c = r["po"].strip(), str(r["container"]).strip()
        where[po].add(int(float(c)) if c.replace(".", "", 1).isdigit() else None)
    left_out = sorted(p for p in vol if not where.get(p) or where[p] == {None})
    split = sorted(p for p in vol if len(where.get(p, set()) - {None}) > 1)
    unknown = sorted(set(where) - set(vol))
    conts = collections.defaultdict(list)
    for p, cs in where.items():
        for c in cs - {None}:
            if p in vol:
                conts[c].append(p)
    rows, over, unproven = [], [], []
    for c in sorted(conts):
        v, w = sum(vol[p] for p in conts[c]), sum(kg[p] for p in conts[c])
        rows.append(f"C{c} {v:.1f}m3 {100 * v / CAP_M3:.0f}% {w:.0f}kg {len(conts[c])}POs")
        if v > CAP_M3 or w > CAP_KG:
            over.append(c)
        elif v / CAP_M3 > TIGHTEST_KNOWN:
            unproven.append(c)
    lb = math.ceil(sum(vol.values()) / CAP_M3)
    ok = len(conts) <= lb and not (left_out or split or unknown or over)
    print(f"{path}\n  containers: {len(conts)} (lower bound {lb})  {'PASS' if ok else 'FAIL'}")
    print("  " + " | ".join(rows))
    for label, xs in [("left out / 'not packable'", left_out), ("split across containers", split),
                      ("unknown POs", unknown), ("over volume or payload", over),
                      (f"fill above {TIGHTEST_KNOWN:.1%}, 3D fit unproven", unproven)]:
        if xs:
            print(f"  {label}: {xs}")
    return ok


if __name__ == "__main__":
    vol, kg = load_shipment(sys.argv[1])
    results = [score(vol, kg, p) for p in sys.argv[2:]]
    sys.exit(0 if all(results) else 1)
