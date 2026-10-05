"""Acceptance test for load_plan.py --advise (top up booked containers).

    python3 check_advisor.py

Benchmark: on the reported shipment with the reference planner's allocation,
LoadViewer's Advisor reached 94.94% fill in the same 3 containers.
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
SHIP = HERE / "issue-10" / "shipment.csv"
LARGE = HERE / "shipments" / "large-8box" / "shipment.csv"
SOLVER_ALLOC = HERE / "issue-10" / "agent-runs" / "reference-solver.csv"
LOADVIEWER_FILL = 0.9494
LIMIT_S = 600


def advise(shipment, *extra):
    out = pathlib.Path(tempfile.mkdtemp(prefix="advise-"))
    t0 = time.time()
    p = subprocess.run([sys.executable, str(PACKER), str(shipment), "--out", str(out), "--advise", *map(str, extra)],
                       capture_output=True, text=True, timeout=LIMIT_S + 60)
    return out, p, time.time() - t0


def common_checks(shipment, out, proc, secs, containers, fixed_alloc=None):
    fails = []
    if proc.returncode != 0:
        return [f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"], {}, {}
    s = json.loads((out / "summary.json").read_text())
    adv = s.get("advice")
    if not adv:
        return ["summary.json has no advice"], s, {}
    rows = list(csv.DictReader(open(shipment)))
    base = {(r["po"], r["sku"]): r for r in rows}
    advice = {(a["po"], a["sku"]): a for a in csv.DictReader(open(out / "advice.csv"))}
    alloc = {}
    for a in csv.DictReader(open(out / "allocation.csv")):
        alloc.setdefault(a["po"], set()).add(a["container"])
    if s.get("containers") != containers:
        fails.append(f"containers {s.get('containers')} != {containers}")
    if fixed_alloc:
        moved = sorted(p for p, c in fixed_alloc.items() if alloc.get(p) != {c})
        if moved:
            fails.append(f"POs moved from the given allocation: {moved[:5]}")
    if set(advice) != set(base):
        fails.append(f"advice.csv has {len(advice)} lines, shipment {len(base)}")
    for k, a in advice.items():
        if k not in base:
            continue
        if int(a["base_cartons"]) != int(base[k]["cartons"]):
            fails.append(f"{k} base_cartons {a['base_cartons']} != {base[k]['cartons']}")
        if int(a["extra_cartons"]) < 0 or int(a["new_cartons"]) != int(a["base_cartons"]) + int(a["extra_cartons"]):
            fails.append(f"{k} inconsistent counts {a}")
        if alloc.get(k[0]) != {a["container"]}:
            fails.append(f"{k} advised in container {a['container']} but PO is in {alloc.get(k[0])}")
    b, v = adv.get("base", {}), adv.get("advised", {})
    if v.get("fill", 0) + 1e-9 < b.get("fill", 0):
        fails.append(f"advised fill {v.get('fill')} below base {b.get('fill')}")
    tol = s.get("tolerance", {})
    if not all(k in tol for k in ("1mm", "2mm", "3mm")):
        fails.append("tolerance missing for advised load")
    else:
        # For an advised load, tolerance answers: with cartons k mm larger, do the base cartons still fit,
        # and how many of the recommended extras survive? Survivors can only shrink as cartons grow.
        for c in tol["1mm"]:
            kept = []
            for k in ("1mm", "2mm", "3mm"):
                r = tol[k].get(c, {})
                if not {"base_fits", "extras_recommended", "extras_kept"} <= set(r):
                    fails.append(f"tolerance {k} container {c} lacks base_fits/extras_recommended/extras_kept")
                    break
                if r["extras_kept"] > r["extras_recommended"]:
                    fails.append(f"tolerance {k} container {c} keeps more extras than recommended")
                kept.append(r["extras_kept"])
            if len(kept) == 3 and not kept[0] >= kept[1] >= kept[2]:
                fails.append(f"container {c} extras kept not non-increasing with growth: {kept}")
    if secs > LIMIT_S:
        fails.append(f"took {secs:.0f}s")

    grown = out / "advised_shipment.csv"
    with open(grown, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        for r in rows:
            k = (r["po"], r["sku"])
            n0, n1 = int(r["cartons"]), int(advice[k]["new_cartons"]) if k in advice else int(r["cartons"])
            w.writerow({**r, "cartons": n1, "line_weight_kg": float(r["line_weight_kg"]) / n0 * n1})
    placed = [{"c": int(r["container"]), "po": r["po"], "sku": r["sku"],
               "pos": [float(r["x"]), float(r["y"]), float(r["z"])], "size": [float(r["l"]), float(r["w"]), float(r["h"])]}
              for r in csv.DictReader(open(out / "placements.csv"))]
    (out / "audit.json").write_text(json.dumps(placed))
    audit = subprocess.run([sys.executable, str(AUDIT), str(grown), str(out / "audit.json")],
                           capture_output=True, text=True).stdout.strip()
    if not audit.endswith("VALID") or "INVALID" in audit:
        fails.append("audit of advised load: " + audit.splitlines()[-1])
    return fails, s, advice


def extras_value(advice, values):
    return sum(int(a["extra_cartons"]) * values.get(k, 0) for k, a in advice.items())


def main():
    results = []
    solver = {r["po"]: r["container"] for r in csv.DictReader(open(SOLVER_ALLOC))}

    out, p, secs = advise(SHIP, "--allocation", SOLVER_ALLOC)
    fails, s, adv_vol = common_checks(SHIP, out, p, secs, 3, solver)
    fill = s.get("advice", {}).get("advised", {}).get("fill", 0)
    base_fill = s.get("advice", {}).get("base", {}).get("fill", 0)
    if fill < 0.94:
        fails.append(f"advised fill {fill:.2%} below 94.0%")
    results.append(("solver allocation", fails, secs,
                    f"base {base_fill:.1%} -> advised {fill:.1%} (LoadViewer {LOADVIEWER_FILL:.1%}), "
                    f"+{s.get('advice', {}).get('extra_cartons')} cartons"))

    out2, p2, secs2 = advise(SHIP)
    fails2, s2, _ = common_checks(SHIP, out2, p2, secs2, 3)
    results.append(("own plan", fails2, secs2,
                    f"advised {s2.get('advice', {}).get('advised', {}).get('fill', 0):.1%}"))

    rows = list(csv.DictReader(open(SHIP)))
    lim = pathlib.Path(tempfile.mkdtemp()) / "limits.csv"
    with open(lim, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["po", "sku", "max_extra", "multiple", "value_per_carton"])
        for i, r in enumerate(rows):
            w.writerow([r["po"], r["sku"], 5 if i % 2 else 12, 4 if i % 3 == 0 else 1, (i * 37) % 11 + 1])
    limits = {(r["po"], r["sku"]): r for r in csv.DictReader(open(lim))}
    out3, p3, secs3 = advise(SHIP, "--allocation", SOLVER_ALLOC, "--limits", lim)
    fails3, _, adv3 = common_checks(SHIP, out3, p3, secs3, 3, solver)
    for k, a in adv3.items():
        e, L = int(a["extra_cartons"]), limits[k]
        if e > int(L["max_extra"]) or e % int(L["multiple"]):
            fails3.append(f"{k} extra {e} breaks max {L['max_extra']} / multiple {L['multiple']}")
    results.append(("limits", fails3, secs3, f"+{sum(int(a['extra_cartons']) for a in adv3.values())} cartons under caps"))

    values = {k: float(L["value_per_carton"]) for k, L in limits.items()}
    out4, p4, secs4 = advise(SHIP, "--allocation", SOLVER_ALLOC, "--limits", lim, "--objective", "value")
    fails4, _, adv4 = common_checks(SHIP, out4, p4, secs4, 3, solver)
    if adv4 and extras_value(adv4, values) + 1e-9 < extras_value(adv3, values):
        fails4.append(f"value objective {extras_value(adv4, values):.0f} < volume objective {extras_value(adv3, values):.0f}")
    results.append(("value objective", fails4, secs4,
                    f"value {extras_value(adv4, values):.0f} vs volume objective {extras_value(adv3, values):.0f}"))

    out5, p5, secs5 = advise(SHIP, "--allocation", SOLVER_ALLOC, "--objective", "cartons")
    fails5, _, adv5 = common_checks(SHIP, out5, p5, secs5, 3, solver)
    n_vol = sum(int(a["extra_cartons"]) for a in adv_vol.values())
    n_cart = sum(int(a["extra_cartons"]) for a in adv5.values())
    if adv5 and n_cart < n_vol:
        fails5.append(f"cartons objective added {n_cart} < volume objective {n_vol}")
    results.append(("cartons objective", fails5, secs5, f"+{n_cart} cartons vs +{n_vol} under volume"))

    out6, p6, secs6 = advise(LARGE)
    fails6, s6, _ = common_checks(LARGE, out6, p6, secs6, 8)
    results.append(("large own plan", fails6, secs6,
                    f"advised {s6.get('advice', {}).get('advised', {}).get('fill', 0):.1%}"))

    ok = True
    for name, fails, secs, note in results:
        ok &= not fails
        print(f"{'PASS' if not fails else 'FAIL'}  {name:18s} {secs:4.0f}s  {note}")
        for f in fails[:6]:
            print(f"      {f}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
