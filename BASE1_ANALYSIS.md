# Why BASE1 dispatched no buses

Reproduced the original report windows on 24 August 2026, seed 101, using the unchanged BASE1 implementations. Both window metrics match the saved original results. No thresholds, sampling intervals, cooldowns, action schemas, or baseline dispatch policies were changed.

## Mechanism

A boarding attempt is eligible only when a queued student’s chosen route and boarding index match the arriving bus. Alighting happens before boarding. Once all 88 seats are occupied, remaining eligible students stay in the queue; each subsequent eligible bus can produce another denial. The experiment percentage counts attempts, not unique people. The legacy simulator metric named capacity_failure_rate counts distinct people instead; the new cards and hourly charts explicitly use the experiment attempt definition.

BASE1 does not observe boarding failures or queue lengths. The experiment rule samples categorical bus crowding every 15 minutes over the preceding 30 minutes, excluding the current decision instant. A run counts as crowded if it appears HIGH in any retained sample (66 or more passengers). Dispatch requires at least two crowded runs and at least 70% of observed distinct runs on the route being crowded, plus no pending extra bus and no cooldown.

## Findings

### Morning

Waiting: 33,789.32 passenger-minutes. Denied attempts: 2,212 / 8,259 = 26.78%. Overall utilization: 19.78%. Extra buses: 0.

| Route | Stop | Denied attempts | Eligible attempts |
|---|---|---:|---:|
| A2 | Prince George's Park Foyer | 1617 | 2434 |
| A2 | Opp Kent Ridge MRT | 183 | 547 |
| D1 | University Town | 148 | 889 |
| R2 | Prince George's Park | 116 | 372 |
| A1 | Kent Ridge MRT | 55 | 409 |
| A2 | S 17 | 36 | 118 |
| A1 | LT 27 | 18 | 45 |
| A1 | Opp University Health Centre | 12 | 18 |

No route met both crowding requirements at any decision in this window. Pending extras and cooldown were not the cause: there were no previous extra dispatches.

Highest sampled crowded-run shares among observations with at least two crowded runs:

- D2: 2/6 = 33.3% at 08:00, below 70%.
- A1: 2/6 = 33.3% at 08:15, below 70%.
- A2: 3/9 = 33.3% at 08:45, below 70%.
- D1: 3/8 = 37.5% at 09:00, below 70%.

### Evening

Waiting: 34,176.06 passenger-minutes. Denied attempts: 2,153 / 11,050 = 19.48%. Overall utilization: 18.60%. Extra buses: 0.

| Route | Stop | Denied attempts | Eligible attempts |
|---|---|---:|---:|
| D1 | Ventus | 379 | 1013 |
| D1 | Opp Yusof Ishak House | 351 | 486 |
| A2 | Kent Ridge Bus Terminal | 311 | 628 |
| A1 | BIZ 2 | 268 | 726 |
| A2 | S 17 | 243 | 726 |
| D1 | Information Technology | 221 | 492 |
| A1 | LT 13 | 106 | 491 |
| D2 | Kent Ridge MRT | 62 | 414 |

No route met both crowding requirements at any decision in this window. Pending extras and cooldown were not the cause: there were no previous extra dispatches.

Highest sampled crowded-run shares among observations with at least two crowded runs:

- D2: 2/12 = 16.7% at 18:30, below 70%.
- A1: 2/10 = 20.0% at 18:45, below 70%.
- D1: 2/9 = 22.2% at 18:45, below 70%.

## Interpretation

Morning A2 denials at Prince George’s Park Foyer alone account for about 73% of all morning denials. The system has localized and directional shortages while much of the fleet is lightly occupied. Thus high denied-boarding percentages can coexist with about 19–20% fleet utilization. The route-wide crowded-run share dilutes local bottlenecks, and 15-minute observations can miss short full-load segments. This is a limitation of the chosen baseline’s information and trigger, not evidence that the simulator forgot to launch an eligible extra bus.

The new lab logs boarding_events.json (time, route, stop, occupancy before boarding, attempts and denials) and rule_audit.json for each completed run. These diagnostics remain outside controller input.

## Window boundary convention

This diagnosis reproduces the original report’s before_decision checkpoint: non-decision events exactly at the opening minute have already happened. The new lab uses strictly start-inclusive/end-exclusive event accounting, so every policy begins from BASE0 immediately before opening-minute events. At 17:00, 11 successful boarding attempts fall on that boundary; this explains why a new half-open-window replay can report 11,061 attempts and 19.4648%, versus the historical report’s 11,050 and 19.4842%. Denials and dispatch decisions are unchanged. This is a measurement-boundary distinction, not a BASE1 policy change.
