# National University of Singapore (NUS) Internal Shuttle Bus (ISB) Simulator and Experiment Lab

This simulator models NUS ISB System based on bus system information available on University Campus Infrostructure (UCI): https://uci.nus.edu.sg/campus-life/campus-services/transportation/internal-shuttle-bus/

Students movement in the campus is modeled primarily based on NUSmods class timetable, class sizes and venues. https://nusmods.com/

All buildings in the simulator are modeled based on real world lattitude and longtitude. Walking time and distance between buildings are thus calculated accordingly.

This simulator supports user to: 1) Create their own rule-based or LLM dispatcher (if they have their own model's API key). 2）Run experiments and check the dispatcher performance by comparing different evaluation metrixs calculated from the simulations. 3) Export their experiment result.

<img width="3066" height="1638" alt="image" src="https://github.com/user-attachments/assets/fa11d272-341d-43ff-b38c-a36d00f6edd3" />

## Start

Requires **Python 3.11 or newer**. The required workbook is included at `data/simulator.xlsx`; no separate data download is needed. It contains the Locations, Dining and Study, and Classes sheets with required column positions preserved. The loaded simulator data and all teaching-day schedules were verified against the original workbook. Calibration parameters remain the same; the workbook fingerprint was updated for the repackaged file.

### Download

Use **Code > Download ZIP** on GitHub, extract it, and open a terminal in the folder containing `pyproject.toml` and `config.json`. Or use Git:

```sh
git clone https://github.com/yuxuan-tokin/NUS-Shuttle-Experiment-Lab.git
cd NUS-Shuttle-Experiment-Lab
```

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m nextbus --config config.json serve --port 8766
```

### macOS or Linux

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e .
./.venv/bin/python -m nextbus --config config.json serve --port 8766
```

On Linux, install your distribution's Python venv package if environment creation reports it missing. Environment activation is unnecessary with these commands. Internet is needed to install dependencies and to call LLM APIs.

### Try it

1. Open **http://127.0.0.1:8766/** on the same computer. Keep the terminal open.
2. Inspect BASE0 or BASE1 in Dispatcher setup. Neither needs an API key.
3. Add an experiment in Experiment setup, select a dispatcher, choose an available teaching date and time window, and save it.
4. Click **Run saved experiment**. Inspect the map, playback, Metrics & charts, and Experiment results.
5. To try an LLM, select or add an LLM dispatcher, enter a compatible provider endpoint, model and API key, and save it before running an experiment. API usage may incur provider charges. Keys entered in the page last for that page session only.
6. Stop the server with **Ctrl+C**. Next time, open a terminal in the same folder and run only the server command.

If `py` is unavailable on Windows, use `python` and confirm `python --version` is at least 3.11. If port 8766 is occupied, use `--port 8767` and open the matching address. If the workbook is missing, check your working directory and that `data/simulator.xlsx` was extracted. Experiments check the supplied workbook fingerprint, so use the bundled file for your first run.

New checkouts start without saved experiments or results. Local settings, experiments, results, and replay files are stored in `outputs/lab/`, survive server restarts, and are excluded from Git. Bundled calibration and historical few-shot examples are baseline inputs. Calibration retains its documented limitations. The server listens locally; this setup does not publish a shared website.

The home page is a full-window map workspace with independent dispatcher, experiment/playback, metrics/charts, buildings/inspection, map layers, and results panels. Hover over, click, or focus a panel to bring it to the front. Click a panel header to minimize it or drag it to move it. This applies to both expanded and minimized panels. Map only hides all panels; Show panels restores them. The old `/experiments` URL opens the same workspace. Advanced simulation settings remain unchanged: they provide a separate interactive preview with step/run/pause controls on the same map. Preview changes do not alter experiment calibration or create experiment result rows. Experiments still use the fixed simulator setup.

## Experiment setup

Dispatcher setup creates named LLM or rule-based dispatchers independently of experiments. The initial saved list contains BASE0, BASE1, LLM-A, LLM-B, LLM-C and LLM-D. BASE0 and BASE1 are locked presets: neither can be edited or discarded. Their settings remain visible, and Save and Discard buttons are hidden for both. Other dispatchers can be edited or discarded. LLM settings include model, HTTPS chat-completions-compatible endpoint, examples and memory. Gemini 3.5 Flash-Lite is the default. API keys entered and saved in Dispatcher Setup are kept in the current page session only and are sent when starting an experiment. Keys can also be supplied through `NEXTBUS_API_KEY`; they are not saved in profiles, manifests, result exports or request logs.

Custom rules configure crowding level, qualifying bus share, and minimum total distinct buses observed on the route in the preceding 30 minutes, sampled at 15-minute intervals. A bus qualifies if any observation reaches the selected crowding level. Defaults are HIGH, 70%, and 2 total buses. BASE1 uses HIGH crowding, a 70% qualifying share, and at least two crowded runs. To use different settings, add a custom dispatcher. Dispatch decisions occur every 15 minutes. There is no additional 10-minute cooldown; a pending extra bus can still prevent dispatch on its route.

Experiment setup selects a saved dispatcher, test days or time window, and seed. Experiments have their own names and can be saved or discarded. Both panels show clickable saved lists. Run uses the saved experiment and the latest saved dispatcher it references. Playback is available for completed runs that match both current saved configurations. Old combined profiles are migrated into separate dispatcher and experiment catalogs, retaining the original file.

Memory defaults to 30 minutes, can be disabled, and is capped at 120 minutes in 15-minute steps. This configures LLM context only: it does not alter BASE1's lookback or thresholds. All selected examples retain their stated horizons. Four historical examples are provided; select any subset or generate additional examples from other teaching-day checkpoints and lengths (15â€“120 minutes). Training and test days must be disjoint. When generating an example, choose two distinct existing dispatchers for the factual and counterfactual branches from BASE0, BASE1, and custom rules. Checkpoints and horizons must fit within the simulator day (06:00 to 24:00). New examples do not alter the frozen historical example file. Each example has a Discard button: it removes the example from the picker and saved dispatcher selections, including after restart. Completed results retain their recorded examples. Original example data is retained for audit. Discard is available when no job is running.

The default Gemini input limit is 1,048,576 tokens, per https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite . For another model, enter the provider's documented context limit. Before each request a conservative UTF-8 byte budget reserves 8,192 tokens for instructions, framing and response. Oversized requests fail with guidance rather than silently dropping selected examples. This is conservative budgeting, not a provider tokenizer; custom provider limits must be entered correctly.

A time-window run first simulates scheduled BASE0 service from the day start to the checkpoint, then enables the chosen dispatcher. Warm-up retains the complete student, bus, queue and event state. Scores and charts start at the checkpoint. Windows include opening-minute events and exclude closing-minute events.

## Watching and comparing

The map shows all individuals or only bus riders and queues. Red route segments indicate that the displayed rule's crowding conditions are met; shared route segments can overlap on this geographic schematic. A brief green highlight marks a new extra dispatch when its next frame appears. The Metrics & charts panel can show the network or one route. Its interval charts show waiting passenger-minutes, denied-attempt percentage, fleet utilization, and average onboard load. Auto uses 15-minute intervals for windows up to three hours and hourly intervals for longer windows. Partial intervals have an asterisk, and missing percentages appear as gaps.

Experiment frames are persisted every five simulated minutes, including all visible individuals, buses, metrics, congestion observations and dispatch events. You can watch live, scrub, or play a completed run after restarting the server. The extra bus run chart follows each dispatched run’s onboard load as a percentage of capacity during passenger service. Dots mark dispatches, and colors identify routes. Earlier saved runs retain their overall hourly metrics. Buildings and destinations are available in the inspection panel; selecting one locates it on the map. Overlapping places offer a selection menu. Bus rectangles use colors to indicate onboard load.

Each completed day becomes a row in the bottom results panel: experiment name, dispatcher and model, test case, memory, seed, five metrics, example count and expanded example dates/horizons/settings. Completing a rerun replaces that experimentâ€™s previous rows, including any days removed from its test setup. Export all current rows to spreadsheet-compatible CSV. Discard removes a result from the table and export; its raw run directory is retained for audit. Discarding a dispatcher or experiment does not modify completed results.

The Metrics & charts panel exports an image with the experiment name, simulation date/time, window, route selection, and chart interval. Export map captures the map with experiment details and legends. Route selectors use the same colored line labels as the map legend.

## Metrics and diagnostics

The five balanced criteria are total waiting passenger-minutes, capacity failure rate, overall fleet utilization, extra bus runs, and extra passenger-service hours. Capacity failure means denied eligible **attempts**, not distinct students. Overall utilization (%) is passenger-minutes divided by (88 times bus passenger-service minutes), multiplied by 100. Resource hours exclude depot and return legs; waiting includes unfinished queues through the measurement boundary. Additional extra-bus utilization is retained in raw results, but is not a sixth weighted objective.


Runtime data lives under `outputs/lab/`: `dispatchers.json`, `experiments.json`, added examples, results and `runs/<id>/`. Runs contain compressed replay frames, configuration/calibration provenance, boarding events, rule decision audits, dispatches and LLM calls. Data survives server restart. A failed run is shown as failed and is not added as a completed result; earlier persisted frames remain available while that job is selected. Rerunning creates a fresh raw run directory while replacing the matching experiment/day result row.

## Verification

Run `python -m unittest discover -s tests -v` and `node tests/test_time_selectors.cjs`. Tests cover the existing simulator and protocol plus window checkpoints, replay, CSV, credential exclusion, memory bounds, hourly intervals, dispatcher/experiment catalogs, result deduplication and custom rule semantics. The latest automated verification passed all 42 Python tests and the JavaScript time-selector checks. Static JavaScript syntax and UI element-reference checks also passed. The latest panel stacking changes have not yet been visually verified in a browser. Live paid LLM requests were not performed.
