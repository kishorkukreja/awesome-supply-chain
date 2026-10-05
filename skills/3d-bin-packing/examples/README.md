# Try the 3D bin packing planner

Sample shipments for testing `scripts/load_plan.py`, by itself or through the skill with Claude. Everything here is either anonymized real data or generated dummy data.

## Files

| File | What it is | Best possible answer |
|---|---|---|
| `export-22po.csv` | A real export shipment: 22 POs, 3,973 cartons, 40' HC. PO and SKU ids are anonymized; sizes, weights and counts are real. | 3 containers |
| `export-22po-booked-allocation.csv` | A commercial planner's PO-to-container plan for that shipment, for trying Advisor mode | LoadViewer's Advisor reached 94.9% fill |
| `large-49po.csv` | Generated: 49 POs, 6,839 cartons | 8 containers (built so 8 is provably the minimum) |
| `oversize-po.csv` | The same cargo, but one PO is bigger than a container | That PO can't ship whole; 8 if it is split |
| `template.csv` | A 3-line template showing the format | 1 container |

## Format

```
po,sku,length_cm,width_cm,height_cm,line_weight_kg,cartons,this_side_up
```

- One row per order line.
- `line_weight_kg` is the total weight of the line, not the weight of one carton.
- `this_side_up` is `1` if the carton must stay upright, and `0` if it can be turned any way.

## Run it

```
# Plan: fewest containers, every PO whole
python3 ../scripts/load_plan.py export-22po.csv --out plan/

# Other equipment, e.g. a 20' standard (internal cm, payload kg)
python3 ../scripts/load_plan.py template.csv --out plan20/ --length 589.8 --width 235.2 --height 239.3 --payload 28200

# Advisor: top up containers you have already booked
python3 ../scripts/load_plan.py export-22po.csv --out advice/ --advise --allocation export-22po-booked-allocation.csv
```

Read `plan/report.md` first. `summary.json`, `allocation.csv` and `placements.csv` hold the details.

The figures in the README are internal dimensions, which vary a few cm by manufacturer. Use your carrier's numbers for real bookings.

With Claude: put your CSV next to the skill and ask, for example, "Plan this shipment into 40' high-cubes, every PO whole."

## Make dummy data

`evals/3d-bin-packing/generate_shipment.py` (at the repo root) builds a shipment where the best answer is known in advance:

```
python3 evals/3d-bin-packing/generate_shipment.py my-test --containers 5 --seed 42
python3 evals/3d-bin-packing/generate_shipment.py my-test-oversize --seed 42 --merge-big-pos
```

Each command writes `shipment.csv`, plus `truth.json` with the planted container count. Change `--seed` for a new shipment and `--containers` for its size.

The generator builds its containers out of the same kind of stacks the planner uses. That makes it good for checking that nothing is broken, but weak evidence that the planner works well on real cargo. Real shipments are the better test.

## Not handled yet

- Pallets
- Crush or stacking-weight limits
- Weight balance and axle loads
- Loading order for multi-drop routes
- Non-box shapes
- Mixing container types in one plan

## Share a test

If you run it on a real shipment, please open an issue with:
- the anonymized CSV, or just its size and product mix;
- what the planner gave you;
- what your current tool or team gave you.

Cases where it does worse are the most useful.
