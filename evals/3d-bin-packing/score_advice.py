"""Score top-up advice against a booked allocation.

    python3 score_advice.py shipment.csv booked_allocation.csv RUN_DIR [RUN_DIR ...]

Each RUN_DIR has advice.csv (po,sku,extra_cartons). If the run also wrote a
placements.csv (container,po,sku,x,y,z,l,w,h) anywhere under RUN_DIR, the
advised load is audited carton by carton with issue-10/audit_placements.py.
"""
import collections
import csv
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
AUDIT = HERE / "issue-10" / "audit_placements.py"
CAP_M3 = 1203.5 * 235.2 * 269.5 / 1e6
CAP_KG = 28620.0


def score(ship, alloc, run):
    rows = {(r["po"], r["sku"]): r for r in csv.DictReader(open(ship))}
    where = {r["po"]: r["container"] for r in csv.DictReader(open(alloc))}
    notes, extra = [], {}
    path = run / "advice.csv"
    if not path.exists():
        return {"run": run.name, "error": "no advice.csv"}
    for r in csv.DictReader(open(path)):
        k = (r.get("po", "").strip(), str(r.get("sku", "")).strip())
        n = int(float(r.get("extra_cartons") or 0))
        if k not in rows:
            notes.append(f"unknown line {k}")
        elif n < 0:
            notes.append(f"negative extra {k}")
        elif n:
            extra[k] = extra.get(k, 0) + n
    vol, kg = collections.defaultdict(float), collections.defaultdict(float)
    for k, r in rows.items():
        n0 = int(r["cartons"])
        n = n0 + extra.get(k, 0)
        c = where[r["po"]]
        vol[c] += float(r["length_cm"]) * float(r["width_cm"]) * float(r["height_cm"]) * n / 1e6
        kg[c] += float(r["line_weight_kg"]) / n0 * n
    over = sorted(c for c in vol if vol[c] > CAP_M3 or kg[c] > CAP_KG)
    fill = sum(vol.values()) / (CAP_M3 * len(vol))
    audit = "no placements written"
    found = sorted(run.rglob("placements.csv"))
    if found:
        grown = pathlib.Path(tempfile.mkdtemp()) / "grown.csv"
        with open(grown, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=next(iter(rows.values())).keys())
            w.writeheader()
            for k, r in rows.items():
                n0 = int(r["cartons"])
                n = n0 + extra.get(k, 0)
                w.writerow({**r, "cartons": n, "line_weight_kg": float(r["line_weight_kg"]) / n0 * n})
        best = None
        for pf in found:
            placed = [{"c": int(float(p["container"])), "po": p["po"], "sku": p["sku"],
                       "pos": [float(p["x"]), float(p["y"]), float(p["z"])],
                       "size": [float(p["l"]), float(p["w"]), float(p["h"])]} for p in csv.DictReader(open(pf))]
            j = pathlib.Path(tempfile.mkdtemp()) / "p.json"
            j.write_text(json.dumps(placed))
            out = subprocess.run([sys.executable, str(AUDIT), str(grown), str(j)], capture_output=True, text=True).stdout
            last = out.strip().splitlines()[-1].strip() if out.strip() else "audit produced no output"
            if last == "VALID":
                best = f"VALID ({pf.relative_to(run)})"
                break
            best = best or f"{last[:120]} ({pf.relative_to(run)})"
        audit = best
    return {"run": run.name, "extra_cartons": sum(extra.values()), "extra_lines": len(extra),
            "fill": round(fill, 4), "per_container": {c: round(v / CAP_M3, 4) for c, v in sorted(vol.items())},
            "over_capacity": over, "notes": notes[:5], "audit": audit}


if __name__ == "__main__":
    ship, alloc, *runs = sys.argv[1:]
    for r in runs:
        print(json.dumps(score(pathlib.Path(ship), pathlib.Path(alloc), pathlib.Path(r))))
