# NextBus simulator

The Python package implements individual activity choices, building-level destinations, single-ride shuttle travel, queues, scheduled and extra runs, calibration, counterfactual cases, six controller arms and a local map interface.

## Current status

The repository contains the simulator, its approved calibration settings, and launchers for the dispatch experiments. The frozen calibration met seven of eight constraints in its recorded check. Attendance error exceeded its target, and full-calendar validation was incomplete.

## Launch

From PowerShell in this folder, run `./run_simulator.ps1` and keep that terminal open. Open **http://127.0.0.1:8765** in Chrome or another local browser. Opening `nextbus/web/index.html` directly is a file preview and cannot run the Python simulation.

Use Python 3.11 or newer and install the dependencies with `python -m pip install -e .`. Place your local copy of the simulator input workbook at the path in `config.json`. The workbook is supplied separately. Dispatch experiments require the workbook fingerprint recorded in `outputs/calibration_final.json`.

```powershell
$runtime = 'python'
& $runtime -m nextbus --config config.json serve
```

The map shows visible individuals, scheduled and extra runs, stop labels and occupancy. Select a teaching day and click **Create run**, then **Run**. Pause or advance five minutes, inspect controller data and export results through the interface. **Minutes per update** changes the amount of simulated time between screen updates; it does not change event timing or decision intervals. A full default day runs from 06:00 to 24:00. API credentials belong in the environment variable named by `llm_key_env`.

The server binds only to localhost. Browser asset requests run concurrently, while simulation changes are serialized. If loading fails, the page displays a startup error instead of enabling empty controls. Stop the server with Ctrl+C before restarting after Python source changes.

The map uses the supplied coordinates and straight stop connections. It provides a geographic schematic. Physical road paths and pedestrian geometry still need dedicated geometry data.

## Commands

```powershell
& $runtime -m nextbus --config config.json audit
& $runtime -m nextbus --config config.json run --output outputs/runs/example
& $runtime -m nextbus --config config.json calibrate --screen --candidates candidates.json --seeds 1 --output outputs/screen.json
& $runtime -m nextbus --config config.json calibrate --candidates finalists.json --seeds 2,3 --output outputs/calibration.json
& $runtime -m nextbus --config config.json case --time 720 --route D1 --output outputs/case1.json
& $runtime -m nextbus --config config.json freeze-examples --cases outputs/case1.json outputs/case2.json outputs/case3.json outputs/case4.json --test-days 2026-08-17
& $runtime -m nextbus --config config.json compare --days 2026-08-17 --seeds 1,2,3
& $runtime -m unittest discover -s tests -v
```

`candidates.json` and `finalists.json` contain lists of parameter dictionaries. Omit the file to check the current settings alone. Screening uses four instructional days with different expected class loads across both semesters. Specify `--screen-days` to check a particular subset. A screening result cannot freeze parameters. Final calibration covers every valid instructional day with multiple seeds. For validation, use seeds that did not participate in screening.

The calibration evaluates candidates jointly. The checks are 95% off-campus return and 90% on-campus return each day at 23:30, at least 60% campus lunch and campus dinner participation among all on-campus residents, at least 40 daily dining minutes for on-campus residents without a meal plan and at least 60 for those with a meal plan, and attendance absolute error no greater than 20% of total expected attendance. The meal and dining checks use averages over teaching days and seeds. The shuttle check defaults to at least 50% of all completed journeys of at least 2 km. The report also shows the shares for all completed journeys and for 1 km and 2 km trips with feasible direct service. Set `shuttle_calibration_scope` to `all`, `long_feasible` or `long_feasible_2km` to apply the gate to one of those shares. Distance bands use straight-line origin-to-destination distances. Report trip counts with the shares, particularly for the narrow 2 km band.

The walking multiplier defaults to 1.5. It applies to direct walks and both shuttle access walks. Keep it fixed across policy arms after choosing the travel assumptions.

Attendance RMSE ranks passing candidates. The report ranks near misses by the largest normalized gap and lists the failed checks. Student distributions include standard deviation, the 10th and 90th percentiles, the median and zero share. The report also shows how many days each resident starts lunch or dinner. Students keep their IDs and home assignments across dates, and the simulator uses aggregate class demand without assigning individual timetables.

Use `--attendance-diagnostics` for signed and absolute attendance errors by building, hour, expected session size and minutes since class start. It also reports the error after pooling across buildings or across time. These pooled figures help diagnose timing and location separately; the pass check still uses individual building-and-minute errors.

Apply the selected values to the experiment configuration and set `frozen_calibration` to the calibration file. Explicit objective weights and positive normalization scales are required to label counterfactual cases. No scalar objective is silently selected. Four cases must be frozen before using LLM-C or LLM-D. Cases and final test days must be disjoint. Formal comparison additionally requires `research_settings_confirmed` true. `--smoke` bypasses the formal calibration gate for software checks.

## Model rules

Buildings are the unit for general activities. Teaching rooms map to their building, while sessions retain their target, start and end. Individual class choice weights include `class_size_eta * log(expected_attendance / 50)` after temperature adjustment. A building's weight sums its eligible activities and class sessions. After drawing a building, the simulator draws the activity and session using their weights within that building.

Class attractiveness ignores distance while a student is off campus or at a residence, mapped UTown building, or PGP location. Distance still applies when choosing travel. The Kent Ridge MRT and opposite MRT gateways each receive a configurable `mrt_gateway_weight` of 4 for entry and exit, while other gateways start at 1. The gateway draw adjusts these weights by distance.

Screening can vary `class_destination_multipliers`, for example `{"UTOWN": 1.25}`. The multiplier changes the relative weights of class destinations at a decision while preserving the student's total class weight. The UTown zone groups its mapped teaching buildings and residential-college teaching venues. Other locations use their building codes.

Class attraction jumps to peak 15 minutes before class and stays there until class starts. It then declines linearly to zero at minute 40, or minute 15 for classes under 50 minutes, with a cutoff no later than the scheduled end. Students already attending stay until the scheduled end. Saved configurations that explicitly specify a numeric `class_post_start_peak_minutes` use the earlier ramp. E-Hybrid sessions have no physical attendance target, travel or commitment.

Study base weights follow library/ERC > residence > general building > canteen. Public stops offer transport gateway functions and have no study or dining choice. Restricted meal plans and residence access are checked before sampling. Known closed activities are excluded. A student arriving after a facility closes is released immediately. A failed meal visit releases that meal's reservation. Study arrivals commit for 30 minutes. Dining defaults to 30 minutes for breakfast, 40 for lunch and 50 for dinner. Older configurations with one dining duration still apply that duration to all three meals.

The run records the share of all students who start each meal. At the evening check, it separately records off-campus exit and the share of on-campus residents physically at their own residence. The on-campus target is 90%. Students studying at their own residence count toward that share.

Each student has one `study_total_weight` to distribute across open, eligible destinations using study quality, distance and the evening multipliers. After a study commitment, `study_continuation_share` moves part of that weight to the current building if it remains eligible. Dining uses a configurable peak and an additional pull as the window progresses for students who have yet to eat.

Morning study starts at 8% of the normal total weight and rises linearly from 07:30 to 09:30. Before 07:30, residence gets a configurable score boost for staying or returning home.

`class_base_score` and `dining_base_score` control the overall strength of class and dining choices. The defaults are behavioral settings for daily activity time. The attendance targets still come from the timetable and the class-type attendance rates.

General-building and canteen study declines at 17:30 and closes at 21:00. Residence study is available only at the assigned home. Open libraries/ERC and residence study gain an evening multiplier. Dining physical hours follow each facility's enabled meal windows: breakfast 07:00–11:00, lunch 11:00–17:30 and dinner 17:30–21:00. Kent Ridge MRT dining and Supper Stretch remain physically open 24 hours; meal selection still follows the meal windows.

Study facilities and dining facilities can map into existing buildings through `facility_buildings`, keyed by the activity ID in the workbook. Confirmed residence meal plans share the residence destination. Central Library uses CLB when that building label exists. Other unconfirmed facilities retain separate destinations until the owner supplies a mapping. Room counts do not multiply general study weights. General building coordinates currently use the mean of their supplied room coordinates.

Techno Edge, Frontier and The Deck are standalone destinations. The Terrace shares COM3. The map distinguishes dining, studying, attending class, residence activities and shuttle queues by color. Off-campus food destinations have a small display-only offset and a leader to their actual coordinates, so diners are distinguishable from passengers at nearby stops. This offset does not change travel calculations.

The transport comparison includes access walking, predicted wait, nominal riding and egress. Compare the fastest feasible direct itinerary among the mapped stop candidates against walking. Ties favor the shuttle. Observed progress reflects delays already experienced. Prediction adds nominal future travel times. Run IDs identify individual traversals. Each run travels from its depot to its starting stop, completes the ordered route, returns to the depot and ends. Published origin departures stay fixed. Students walk to their destination when no further eligible shuttle can collect them.

Counterfactual branches copy complete state and use identity-keyed randomness. They compare only the initial dispatch and follow no additional dispatch for 30 minutes. Passenger metrics include all affected passengers. Resource cost and utilization cover only the new run inside the same 30 minutes. Earlier extras keep operating in both branches.

## Inputs requiring confirmation

- Semester start anchors currently follow the earliest explicit timetable dates, 3 August 2026 and 4 January 2027, with a recess after teaching week 6. Confirm these against the intended teaching-week mapping.
- The gateway set contains public-connected shuttle locations whose coordinates are available. Additional public stops await mapping.
- UTown Residence uses a provisional UTown point. Inherited residence coordinates, including Eusoff Hall, remain unverified.
- Techno Edge uses the previously requested IT and CLB access stops. RVRC and Supper Stretch use their agreed multi-stop exceptions.
- Depot speed, noise strengths, observation/decision intervals, study-weight ratios, evening multipliers, cooldown, long-wait and congestion thresholds are configurable starting values.

## Outputs and limits

Runs export configuration provenance, workbook hash, individual-level aggregate metrics, queue/state time series, session attendance, actions and dispatches as JSON. LLM runs also export structured inputs, responses, usage and latency. Exact occupancy and passenger metrics are excluded from controller observations. History contains sampled categorical operational observations.

The interface's **Export** saves the current run to `outputs/ui_run`, including a manifest with the configuration, workbook hash and completion status. This location is replaced by subsequent interface exports.

Observed wait percentiles include elapsed unfinished waits. `unfinished_waits` identifies this censoring, so an unfinished episode's percentile must not be interpreted as a final completed-wait percentile. Total waiting burden includes every queue minute up to the measurement boundary. Capacity failures count distinct people denied at least once among distinct people encountering an eligible bus during the measured period.

Formal research results require confirmed inputs, successful all-day calibration, chosen objective weights, frozen examples and live LLM connection checks. Persistent replay visualization, CSV comparison charts and API-specific NextBus field verification remain follow-up work.


## Dispatch experiment workflow

The Experiments link opens `/experiments`. It uses the approved fixed calibration in `outputs/calibration_final.json`, records its incomplete validation status, and verifies its model parameters and workbook fingerprint before running.

Run `./run_experiments.ps1 -Task examples` to generate four examples, `-Task baselines` for BASE0/BASE1, and `-Task evaluate` for all six arms. These actions are also available on the Experiments page. Completed evaluation days resume automatically. Completed draft examples are reused after interruption. A running process holds a lock to prevent concurrent jobs writing the same results.

The four training checkpoints are 08:00 on 25 January, 12:00 on 10 February, 18:00 on 5 March and 21:30 on 16 March 2027, using seed 201. Each checkpoint comes from BASE0 scheduled service. From that shared state, BASE0 runs for 60 minutes without dispatching and BASE1 applies its rule every 15 minutes. Waiting passenger-minutes, denied boarding attempts, and extra service hours determine the preferred outcome in that order. Exact ties select BASE0.

The full-day runner uses 24 and 26 August 2026 with seed 101. Each run continues from 06:00 to midnight, with decisions every 15 minutes. The LLM memory arms receive the preceding hour of 15-minute observations. BASE1 uses a 30-minute lookback with a 70% crowding threshold, at least two crowded runs, a pending-bus exclusion, and a cooldown.

Windows are start-inclusive and end-exclusive. Capacity failures count denied attempts over eligible attempts. Utilization is passenger-minutes divided by capacity times passenger-service minutes. Extra bus-hours include only passenger-service portions inside the window, including earlier dispatches. Depot and return travel remain simulated but are excluded from these service-hour and utilization denominators. Extra buses count launches inside the window. Reported waiting includes unfinished waits up to the window boundary. Null utilization means no corresponding service time.

`outputs/dispatch_experiment` holds the generated examples, run logs, and CSV exports. Each successful LLM decision is saved as it arrives. A retry replays saved decisions when the requests match and calls the provider for missing decisions.

Set the chat-completions-compatible endpoint and model in the GUI or `config.json`. Supply credentials through `NEXTBUS_API_KEY` before starting the server or runner. A decision retries up to three times and fails the run if it receives no valid response.

## Reproduce the report experiment

After generating the four frozen examples, run the two-window evaluation from PowerShell.

```powershell
$env:NEXTBUS_API_KEY = Read-Host 'Gemini API key' -MaskInput
python -m nextbus.report_experiment
```

This runs all six arms on 24 August 2026 with seed 101 and uses Gemini 3.5 Flash-Lite for the LLM arms. Each window starts from a shared BASE0 checkpoint prepared with scheduled service from 06:00. The scored windows are 07:00–09:30 and 17:00–19:30. The runner writes 12 rows to `outputs/dispatch_report/results.csv` and retains decisions and dispatch logs for each arm. The same command resumes an interrupted run.
