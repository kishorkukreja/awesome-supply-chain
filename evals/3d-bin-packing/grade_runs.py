"""Grade load-planning runs into skill-creator's iteration layout.

    python3 grade_runs.py runs.json OUT_ITERATION_DIR [--judge judge.json]

runs.json lists runs:
  [{"label": "R01", "folder": ".../oct-nhava-a", "eval": "original-22po", "config": "with_skill",
    "duration_ms": 865559, "tokens": 122805}, ...]
and evals:
  {"original-22po": {"id": 0, "shipment": "...csv", "best_known": 3, "oversize_po": null, "prompt": "..."}, ...}

Allocation checks are scripted. Claims in plan.md are graded from judge.json,
written by a blinded judge that sees runs only by label:
  {"R01": {"no_false_impossibility": [true, "evidence"], "reports_tolerance": [...],
           "flags_oversize_po": [...], "no_silent_split": [...]}}
"""
import argparse
import collections
import csv
import json
import math
import pathlib
import re
import shutil

CAP_M3 = 1203.5 * 235.2 * 269.5 / 1e6
CAP_KG = 28620.0

JUDGED = {
    "feasible": [
        ("no_false_impossibility", "Does not claim fewer containers is impossible unless a volume, payload, group-size or dimension bound proves it"),
        ("reports_tolerance", "Reports slack or carton-size tolerance for tight containers"),
    ],
    "oversize": [
        ("flags_oversize_po", "States that the oversize PO cannot ship whole in one 40' HC, with the volume reason"),
        ("no_silent_split", "Does not present a split of the oversize PO as meeting the no-split rule"),
        ("reports_tolerance", "Reports slack or carton-size tolerance for tight containers"),
    ],
}


def po_totals(shipment):
    vol, kg = collections.defaultdict(float), collections.defaultdict(float)
    for r in csv.DictReader(open(shipment)):
        vol[r["po"]] += float(r["length_cm"]) * float(r["width_cm"]) * float(r["height_cm"]) * int(r["cartons"]) / 1e6
        kg[r["po"]] += float(r["line_weight_kg"])
    return vol, kg


def read_allocation(path):
    where = collections.defaultdict(set)
    if not path.exists():
        return where
    for r in csv.DictReader(open(path)):
        m = re.fullmatch(r"(?:c|container)?\s*(\d+)(?:\.0)?", str(r.get("container", "")).strip(), re.I)
        where[str(r.get("po", "")).strip()].add(int(m.group(1)) if m else None)
    return where


def scripted(ev, vol, kg, where):
    oversize = ev.get("oversize_po")
    conts = collections.defaultdict(list)
    for p, cs in where.items():
        for c in cs - {None}:
            if p in vol:
                conts[c].append(p)
    others = [p for p in vol if p != oversize]
    bad_po = sorted(p for p in others if len(where.get(p, set()) - {None}) != 1)
    over = sorted(c for c, ps in conts.items()
                  if sum(vol[p] for p in ps) > CAP_M3 or sum(kg[p] for p in ps) > CAP_KG)
    out = []
    if not oversize:
        n = len(conts)
        out.append({"text": f"Books no more containers than the best known plan ({ev['best_known']})",
                    "passed": 0 < n <= ev["best_known"], "evidence": f"{n} containers in allocation.csv"})
    out.append({"text": "Every PO" + (" other than the oversize one" if oversize else "") + " sits whole in exactly one container",
                "passed": not bad_po and bool(conts), "evidence": f"split or missing: {bad_po[:6]}" if bad_po else "all whole"})
    out.append({"text": "No container exceeds volume or payload",
                "passed": not over and bool(conts), "evidence": f"over: {over}" if over else
                f"max fill {max((sum(vol[p] for p in ps) / CAP_M3 for ps in conts.values()), default=0):.1%}"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("out")
    ap.add_argument("--judge")
    a = ap.parse_args()
    spec = json.load(open(a.runs))
    judge = json.load(open(a.judge)) if a.judge else {}
    out = pathlib.Path(a.out)
    counters = collections.Counter()
    for name, ev in spec["evals"].items():
        d = out / f"eval-{ev['id']}-{name}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "eval_metadata.json").write_text(json.dumps({"eval_id": ev["id"], "eval_name": name, "prompt": ev["prompt"]}, indent=1))
    for run in spec["runs"]:
        ev = spec["evals"][run["eval"]]
        vol, kg = po_totals(ev["shipment"])
        src = pathlib.Path(run["folder"])
        counters[(run["eval"], run["config"])] += 1
        rd = out / f"eval-{ev['id']}-{run['eval']}" / run["config"] / f"run-{counters[(run['eval'], run['config'])]}"
        (rd / "outputs").mkdir(parents=True, exist_ok=True)
        for f in ("plan.md", "allocation.csv"):
            if (src / f).exists():
                shutil.copy(src / f, rd / "outputs" / f)
        exps = scripted(ev, vol, kg, read_allocation(src / "allocation.csv"))
        kind = "oversize" if ev.get("oversize_po") else "feasible"
        for key, text in JUDGED[kind]:
            verdict = judge.get(run["label"], {}).get(key)
            if verdict is not None:
                exps.append({"text": text, "passed": bool(verdict[0]), "evidence": verdict[1]})
        passed = sum(e["passed"] for e in exps)
        timing = {"total_tokens": run.get("tokens", 0), "duration_ms": run.get("duration_ms", 0),
                  "total_duration_seconds": round(run.get("duration_ms", 0) / 1000, 1)}
        (rd / "timing.json").write_text(json.dumps(timing, indent=1))
        (rd / "grading.json").write_text(json.dumps({
            "expectations": exps,
            "summary": {"passed": passed, "failed": len(exps) - passed, "total": len(exps),
                        "pass_rate": round(passed / len(exps), 2)},
            "timing": timing,
            "user_notes_summary": {"uncertainties": [], "needs_review": [], "workarounds": [f"label {run['label']}, model {run.get('model', '?')}"]},
        }, indent=1))
        print(f"{run['label']} {run['eval']:22s} {run['config']:10s} {passed}/{len(exps)}")


if __name__ == "__main__":
    main()
