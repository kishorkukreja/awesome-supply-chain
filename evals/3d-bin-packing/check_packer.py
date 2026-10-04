"""Acceptance test for skills/3d-bin-packing/scripts/load_plan.py.

    python3 check_packer.py [--quick]

Runs the bundled packer on three shipments with known answers and checks its
output independently with issue-10/audit_placements.py:

  original      22 POs, 3,973 cartons. Best possible 3 (a reference planner found 3).
  large-8box    49 POs, 6,839 cartons. 8 planted, volume bound 8.
  oversize-po   48 POs. One PO is 96.2 m3, more than a container. Must be flagged
                and left unassigned by default; --split-oversize must place it.

--quick skips the extra generated seeds.
"""
import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PACKER = ROOT / "skills" / "3d-bin-packing" / "scripts" / "load_plan.py"
AUDIT = HERE / "issue-10" / "audit_placements.py"
CAP_M3 = 1203.5 * 235.2 * 269.5 / 1e6
LIMIT_S = 600

CASES = [
    ("original", HERE / "issue-10" / "shipment.csv", {"containers": 3}, []),
    ("large-8box", HERE / "shipments" / "large-8box" / "shipment.csv", {"containers": 8}, []),
    ("oversize-po", HERE / "shipments" / "large-oversize-po" / "shipment.csv", {"oversize": 1}, []),
    ("oversize-po split", HERE / "shipments" / "large-oversize-po" / "shipment.csv", {"containers": 8}, ["--split-oversize"]),
]


def run(name, shipment, expect, extra):
    out = pathlib.Path(tempfile.mkdtemp(prefix="plan-"))
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(PACKER), str(shipment), "--out", str(out), *extra],
                          capture_output=True, text=True, timeout=LIMIT_S + 60)
    secs = time.time() - t0
    fails = []
    if proc.returncode != 0:
        return [f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"], secs, {}
    summary = json.loads((out / "summary.json").read_text())
    alloc = list(csv.DictReader(open(out / "allocation.csv")))
    rows = list(csv.DictReader(open(shipment)))
    pos = {r["po"] for r in rows}
    where = {}
    for a in alloc:
        where.setdefault(a["po"], set()).add(a["container"])
    oversize = {g["group"] for g in summary.get("oversize_groups", [])}

    if set(where) != pos:
        fails.append(f"allocation lists {len(where)} POs, shipment has {len(pos)}")
    split_ok = "--split-oversize" in extra
    for p, cs in where.items():
        if p in oversize and not split_ok:
            if cs != {"UNASSIGNED"}:
                fails.append(f"oversize {p} assigned to {sorted(cs)} without --split-oversize")
        elif p not in oversize and len(cs) != 1:
            fails.append(f"{p} split across {sorted(cs)}")
    if "containers" in expect and summary.get("containers") != expect["containers"]:
        fails.append(f"containers {summary.get('containers')} != {expect['containers']}")
    if "oversize" in expect:
        if len(oversize) != expect["oversize"]:
            fails.append(f"oversize groups {sorted(oversize)}, expected {expect['oversize']}")
        for g in summary.get("oversize_groups", []):
            if not g.get("m3", 0) > CAP_M3:
                fails.append(f"oversize {g.get('group')} reported at {g.get('m3')} m3, not above {CAP_M3:.2f}")
    if summary.get("lower_bound") is None:
        fails.append("summary has no lower_bound")
    tol = summary.get("tolerance", {})
    if not all(k in tol for k in ("1mm", "2mm", "3mm")):
        fails.append(f"tolerance check missing, got keys {sorted(tol)}")
    if secs > LIMIT_S:
        fails.append(f"took {secs:.0f}s, limit {LIMIT_S}s")

    placed = [{"c": int(r["container"]), "po": r["po"], "sku": r["sku"],
               "pos": [float(r["x"]), float(r["y"]), float(r["z"])],
               "size": [float(r["l"]), float(r["w"]), float(r["h"])]}
              for r in csv.DictReader(open(out / "placements.csv"))]
    placed_pos = {p["po"] for p in placed}
    sub = out / "audited_shipment.csv"
    with open(sub, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(r for r in rows if r["po"] in placed_pos)
    (out / "audit.json").write_text(json.dumps(placed))
    audit = subprocess.run([sys.executable, str(AUDIT), str(sub), str(out / "audit.json")],
                           capture_output=True, text=True).stdout.strip()
    if not audit.endswith("VALID") or "INVALID" in audit:
        fails.append("audit: " + audit.splitlines()[-1])
    missing = pos - placed_pos - (oversize if not split_ok else set())
    if missing:
        fails.append(f"POs with no placed cartons: {sorted(missing)[:5]}")
    return fails, secs, summary


def main():
    cases = list(CASES)
    if "--quick" not in sys.argv:
        gen = HERE / "generate_shipment.py"
        for seed in (11, 23):
            d = pathlib.Path(tempfile.mkdtemp(prefix=f"seed{seed}-"))
            subprocess.run([sys.executable, str(gen), str(d), "--seed", str(seed)], capture_output=True, check=True)
            cases.append((f"generated seed {seed}", d / "shipment.csv", {"containers": 8}, []))
    ok = True
    for name, shipment, expect, extra in cases:
        fails, secs, summary = run(name, shipment, expect, extra)
        ok &= not fails
        got = summary.get("containers")
        lb = summary.get("lower_bound")
        print(f"{'PASS' if not fails else 'FAIL'}  {name:20s} containers={got} lower_bound={lb} {secs:.0f}s")
        for f in fails:
            print(f"      {f}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
