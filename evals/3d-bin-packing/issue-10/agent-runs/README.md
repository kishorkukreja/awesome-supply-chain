# Agent-surface reproduction for issue #10

The reported bug is about Claude's behavior when it uses the skill, not about the skill's Python alone. So the skill was handed to fresh agents with an organic request, and their plans were scored.

## Setup

Four agents ran in separate folders. Each folder held `shipment.csv` and `skills/3d-bin-packing/SKILL.md` and nothing else. The folder names and the prompt contained no evaluation wording, and no agent knew other agents existed. Two ran on Opus and two on Sonnet. Every agent got the same prompt.

> I'm planning an export shipment out of our warehouse. The order lines are in shipment.csv in that folder (22 POs, about 4,000 cartons). We load into 40' high-cube containers, internal 1203.5 x 235.2 x 269.5 cm, max payload 28,620 kg. Every PO has to ship whole in one container, no splitting. Cartons with this_side_up = 1 must stay upright.
>
> Use the 3D bin packing skill in skills/3d-bin-packing/SKILL.md to work out the load plan. I need to know how many containers to book and which POs go in each. Put the plan in plan.md and the PO to container assignment in allocation.csv with columns po,container. Reply with a short summary of the plan.

The PO ids in these files have been anonymized to match `../shipment.csv`. Rerun the scoring with `python3 ../verify_plan.py ../shipment.csv *.csv`. The output is in `scores.txt`.

## Results

| Run | Containers | Largest PO (P01) | Verdict |
|---|---|---|---|
| reference-solver | 3 | shares with 4 POs | pass |
| reported-screenshot | 3 + P01 "not packable whole" | left out | fail |
| opus-1 | 3 | shares with 3 POs | pass |
| sonnet-1 | 3 | shares with 3 POs | pass |
| opus-2 | 4 | alone | fail |
| sonnet-2 | 4 | alone | fail |

The carton coordinates of both passing agent plans were checked with `../audit_placements.py`. Every carton was placed, nothing overlapped or left the container, no upright carton was tipped, and the lowest base support was 0.95. The coordinates are not committed because they carry the original PO ids.

Both failing runs and the reported run have the same shape. P01 sits alone, and the other 21 POs are balanced across three containers at 56 to 62 percent fill.

## Mechanism

None of the four agents executed the skill's code. Each one judged it unfit (no upright rule, no grouping, no overlap check against placed boxes) and wrote its own packer. So on this surface the failure comes from the skill's guidance, not its snippets.

- **opus-2** imposed single-PO, single-footprint, full-height columns. Under that model the floor needs 3.01 containers, and the plan reports this as proof that 3 cannot work. It also rules out mixed-PO stacks as unloadable. The user never stated that rule, and the passing plans and the reference planner use mixed stacks.
- **sonnet-2** said that P01 fills 93 percent of a 5-tier floor, so "nothing else can share the container". That ignores the roughly 25 cm of headroom above five tiers, where 10 cm flat cartons fit. Both passing plans put flat cartons there. It labelled "not 3" as search evidence rather than proof, but still recommended booking 4.
- **opus-1** passed and still gave the screenshot's split as its fallback, reasoning from carton-size tolerance. P01 really is tight. An independent lane packing fits it at the listed sizes and at +1 mm per dimension, and fails from +2 mm.

The common root cause is that the skill never separates a true lower bound from a bound that only holds under a packing model the agent picked. It never says that POs in one container may share stacks. It never asks for slack or tolerance to be reported alongside a container count.

## Limits

Four runs, two per model. The failure rate is 1 of 2 on each model, so the sample shows the bug is intermittent but cannot rank the models. A fix should be judged on the same prompt with more runs.
