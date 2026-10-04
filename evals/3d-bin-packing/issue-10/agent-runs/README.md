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

The carton coordinates of both passing agent plans were checked with `../audit_placements.py`. Every carton was placed, nothing overlapped or left the container, no upright carton was tipped, and the lowest base support was 0.95. Those coordinates are not committed because they carry the original PO ids, so this check cannot be rerun from the repo. What can be rerun is the auditor itself: `../reproduce.py` T5 passes a known-good plan through it and plants four faults, and it catches all four.

`verify_plan.py` checks volume and payload per container, not 3D fit. Its "3D fit unproven" note fires above 91.51 percent, the tightest container in the reference plan. That threshold is informational and never turns a pass into a fail.

Run folders, for anyone with the session scratchpad: opus-1 `sbws2-sep24`, opus-2 `export-sep24`, sonnet-1 `sbws2-loads`, sonnet-2 `sep24-loadplan`.

Both failing runs and the reported run have the same shape. P01 sits alone, and the other 21 POs are balanced across three containers at 56 to 62 percent fill.

## Mechanism

None of the four agents executed the skill's code. No script in any run folder imports or calls a function from SKILL.md. Each agent wrote its own packer, some citing the skill's approach. So in these runs the failure came from the skill's guidance, not its snippets.

There are two failure paths, and both are real. An agent that runs the skill's snippets gets the symptom from `../reproduce.py` T1 and T2, with 5 to 6 containers and the largest PO not packable. An agent that writes its own packer can still fail the way the runs below did.

- **opus-2** imposed single-PO, single-footprint, full-height columns. Under that model the floor needs 3.01 containers, and the plan reports this as proof that 3 cannot work. It also rules out mixed-PO stacks as unloadable. The user never stated that rule, and the passing plans and the reference planner use mixed stacks.
- **sonnet-2** said that P01 fills 93 percent of a 5-tier floor, so "nothing else can share the container". That ignores the roughly 25 cm of headroom above five tiers, where 10 cm flat cartons fit. Both passing plans put the POs with 10 cm flat cartons in P01's container, and opus-1's plan says they sit on top of P01's stacks. It labelled "not 3" as search evidence rather than proof, but still recommended booking 4.
- **opus-1** passed and still gave the screenshot's split as its fallback, reasoning from carton-size tolerance. P01 really is tight. `../reproduce.py` T4 fits it at the listed sizes and at +1 mm per dimension, and finds no fit from +2 mm.

The likely common cause, inferred from the two failing plans' own text, is that the skill never separates a true lower bound from a bound that only holds under a packing model the agent picked. It never says that POs in one container may share stacks. It never asks for slack or tolerance to be reported alongside a container count.

## Limits

Four runs, two per model. The failure rate is 1 of 2 on each model, so the sample shows the bug is intermittent but cannot rank the models. A fix should be judged on the same prompt with more runs.
