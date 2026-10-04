# Spec: bundled load planner for the 3d-bin-packing skill

Build `skills/3d-bin-packing/scripts/load_plan.py`. It must use only the Python 3.11 standard library, because agents running the skill often have no numpy. It plans a real shipment into containers, keeps each group (PO) whole, and checks its own output. The acceptance test is `evals/3d-bin-packing/check_packer.py`. Do not edit the test, the auditor, or anything under `evals/`.

## Command line

```
python3 load_plan.py SHIPMENT.csv --out DIR
    [--length 1203.5 --width 235.2 --height 269.5 --payload 28620]   # 40' HC defaults, cm and kg
    [--group-col po] [--split-oversize] [--time-limit 480] [--seed 1]
    [--support 0.8] [--clearance 0]
```

`SHIPMENT.csv` columns are `po, sku, length_cm, width_cm, height_cm, line_weight_kg, cartons, this_side_up`. `line_weight_kg` is the total for the line. `this_side_up = 1` means only rotation about the vertical axis is allowed. `this_side_up = 0` allows all 6 orientations.

## Outputs in DIR

- `allocation.csv` with columns `po,container`. Containers are numbered 1..N. An oversize group that is not split gets one row with `UNASSIGNED`. A split oversize group gets one row per container it uses.
- `placements.csv` with columns `container,po,sku,x,y,z,l,w,h`. Units are cm, z is up, and `l,w,h` are the placed extents. Every carton appears exactly once.
- `summary.json` containing:
  - `lower_bound`: max(ceil(volume / container volume), ceil(weight / payload)) over the groups that can be placed, plus the minimum containers each oversize group needs if `--split-oversize`.
  - `containers`: N.
  - `proven_optimal`: whether `containers == lower_bound`.
  - `oversize_groups`: list of `{group, m3, kg, reason}`. A group is oversize when its volume exceeds a container's volume, its weight exceeds the payload, or one of its cartons fits in no allowed orientation.
  - `containers_detail`: per container, `groups`, `cartons`, `m3`, `fill`, `kg`, and spare length, width and height in cm.
  - `tolerance`: keys `1mm`, `2mm`, `3mm`. Each holds a per-container result of re-packing that container's groups with every carton grown by that much in each dimension: `{"fits": bool, "unplaced": int}`.
  - `checks`: the self-verification results.
- `report.md`: a human-readable version, laid out like the skill's Output Format section. It states plainly when the count is "best found, not proven optimal" and never calls a count impossible without a bound.

## Data shape (choose this before writing logic)

- `CartonType`: sku, group, dims, unit weight, count, upright flag. The allowed orientations are precomputed per type.
- `Column`: a vertical stack of cartons that share one footprint (l × w). The base comes from one SKU stacked up to the roof. The top may then carry flat cartons whose footprint fits inside the column footprint, so they rest fully supported, and those cartons may come from other groups in the same container. A column knows its footprint, height, cartons and weight.
- `Lane`: a strip along the container length with a fixed width. Columns sit inside it end to end.
- `ContainerPlan`: lanes, plus the leftover cartons that need extra placement.
- `Assignment`: group → container. This is the search state for stage 1.

Keep state in these types. Do not scatter dicts with repeated shape assumptions.

## Method

The evidence comes from 16 agent runs on these shipments. The approach that reached the optimum on both shipments was column building, then lane packing, then headroom fill, with a search over group assignments.

1. **Bounds and oversize detection** come first.
2. **Pack one container (stage 2)**, given a set of groups:
   - Build columns per SKU. Pick the orientation that maximises tiers × footprint use.
   - Fill the gap under the roof with flat cartons from any group in the container. Rods, shelves and 10 cm boxes ride on taller columns.
   - Pack the column footprints onto the floor with lanes across the width. Choose lane widths to fit the column depths and keep the leftover width small. Fill lanes along the length, first-fit decreasing, and try both carton footprint orientations.
   - Place remaining cartons in leftover floor or height space with a support check (at least `--support`).
   - Return the plan or the list of unplaced cartons. Make it deterministic for a given seed.
3. **Assign groups to containers (stage 1).** Start from the lower bound N:
   - First-fit decreasing by group volume with a volume and payload check. Then verify each container with stage 2.
   - On failure, repair. Move small groups out of the failing container, swap groups between containers, and retry with alternative orderings, all within the time limit.
   - Raise N only when the time budget for N runs out. Cache stage 2 results by frozenset of groups.
4. **Tolerance.** Re-run stage 2 per final container with dims +0.1, +0.2 and +0.3 cm.
5. **Self-check.** Before writing outputs, confirm carton counts per line, containment, no overlap, upright kept, support at least the threshold, and payload. Exit non-zero if the check fails.

## Acceptance

`python3 evals/3d-bin-packing/check_packer.py` must print PASS for every case:

- The original shipment uses 3 containers.
- The large planted shipment uses 8.
- The oversize PO is flagged and left unassigned by default.
- With `--split-oversize`, that shipment uses 8.
- The generated seeds 11 and 23 use 8, their planted count.
- Each case finishes in under 600 s on one CPU.

If a target is out of reach in the time limit, report what you reached. Do not weaken the test.
