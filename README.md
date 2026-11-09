# NextBus Experiment Lab

A separate research workspace derived from the original NextBus simulator. The original checkout and GitHub repository are preserved. **Both BASE1 implementations are unchanged.** The LLM objective prompt now balances five metrics without a prescribed ranking or weighting formula.

## Start

Run `./run_simulator.ps1 -Port 8766`, then visit http://127.0.0.1:8766/. The original can continue using port 8765.

Requires Python 3.11+, numpy, openpyxl, and the separately supplied workbook at the path in `config.json`. Install with `python -m pip install -e .`. The fixed calibration and workbook fingerprint are checked before experiments. Calibration has its original known limitations; this UI does not turn an exploratory run into a formally validated result.

The home page is a full-window map workspace with independent dispatcher, experiment/playback, metrics/charts, buildings/inspection, map layers, and results panels. Click a panel header to minimize it or drag it to move it. Map only hides all panels; Show panels restores them. The old `/experiments` URL opens the same workspace. Advanced simulation settings remain unchanged: they provide a separate interactive preview with step/run/pause controls on the same map. Preview changes do not alter experiment calibration or create experiment result rows. Experiments still use the fixed simulator setup.

## Experiment setup

Dispatcher setup creates named LLM or rule-based dispatchers independently of experiments. The initial saved list contains BASE0, BASE1, LLM-A, LLM-B, LLM-C and LLM-D. BASE0 cannot be edited or discarded; the others can be discarded. LLM settings include model, HTTPS chat-completions-compatible endpoint, examples and memory. Gemini 3.5 Flash-Lite is the default. API keys entered and saved in Dispatcher Setup are kept in the current page session only and are sent when starting an experiment. Keys can also be supplied through `NEXTBUS_API_KEY`; they are not saved in profiles, manifests, result exports or request logs.

Custom rules configure crowding level, qualifying bus share, and minimum total distinct buses observed on the route in the preceding 30 minutes, sampled at 15-minute intervals. A bus qualifies if any observation reaches the selected crowding level. Defaults are HIGH, 70%, and 2 total buses. The original BASE1 preset retains its original minimum of two crowded buses. Editing that preset saves a separate custom rule; it does not change BASE1.

Experiment setup selects a saved dispatcher, test days or time window, and seed. Experiments have their own names and can be saved or discarded. Both panels show clickable saved lists. Run uses the saved experiment and the latest saved dispatcher it references. Playback is available for completed runs that match both current saved configurations. Old combined profiles are migrated into separate dispatcher and experiment catalogs, retaining the original file.

Memory defaults to 30 minutes, can be disabled, and is capped at 120 minutes in 15-minute steps. This configures LLM context only: it does not alter BASE1's lookback or thresholds. All selected examples retain their stated horizons. Four historical examples are provided; select any subset or generate additional examples from other teaching-day checkpoints and lengths (15–120 minutes). Training and test days must be disjoint. New examples do not alter the frozen historical example file. Each example has a Discard button: it removes the example from the picker and saved dispatcher selections, including after restart. Completed results retain their recorded examples. Original example data is retained for audit. Discard is available when no job is running.

The default Gemini input limit is 1,048,576 tokens, per https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite . For another model, enter the provider's documented context limit. Before each request a conservative UTF-8 byte budget reserves 8,192 tokens for instructions, framing and response. Oversized requests fail with guidance rather than silently dropping selected examples. This is conservative budgeting, not a provider tokenizer; custom provider limits must be entered correctly.

A time-window run first simulates scheduled BASE0 service from the day start to the checkpoint, then enables the chosen dispatcher. Warm-up retains the complete student, bus, queue and event state. Scores and charts start at the checkpoint. Windows include opening-minute events and exclude closing-minute events.

## Watching and comparing

The map shows all individuals or only bus riders and queues. Red route segments indicate that the BASE1 crowding conditions are met; shared route segments can overlap on this geographic schematic. A brief green highlight marks a new extra dispatch when its next frame appears. Hourly small charts show waiting passenger-minutes, denied-attempt percentage, and fleet utilization percentage. A partial first or last hour is marked with an asterisk. Missing percentages are gaps, not zeroes.

Experiment frames are persisted every five simulated minutes, including all visible individuals, buses, metrics, congestion observations and dispatch events. You can watch live, scrub, or play a completed run after restarting the server. Five metric cards and hourly charts occupy their own panel and follow the simulation displayed on the map. All buildings and destinations are available in a separate expandable inspection panel, alongside the existing facilities table.

Each completed day becomes a row in the bottom results panel: experiment name, dispatcher and model, test case, memory, seed, five metrics, example count and expanded example dates/horizons/settings. Completing a rerun replaces that experiment’s previous rows, including any days removed from its test setup. Export all current rows to spreadsheet-compatible CSV. Discard removes a result from the table and export; its raw run directory is retained for audit. Discarding a dispatcher or experiment does not modify completed results.

## Metrics and diagnostics

The five balanced criteria are total waiting passenger-minutes, capacity failure rate, overall fleet utilization, extra buses dispatched, and extra passenger-service hours. Capacity failure means denied eligible **attempts**, not distinct students. Overall utilization is passenger-minutes divided by 88 times passenger-service minutes. Resource hours exclude depot and return legs; waiting includes unfinished queues through the measurement boundary. Additional extra-bus utilization is retained in raw results, but is not a sixth weighted objective.


Runtime data lives under `outputs/lab/`: `dispatchers.json`, `experiments.json`, added examples, results and `runs/<id>/`. Runs contain compressed replay frames, configuration/calibration provenance, boarding events, rule decision audits, dispatches and LLM calls. Data survives server restart. A failed run is shown as failed and is not added as a completed result; earlier persisted frames remain available while that job is selected. Rerunning creates a fresh raw run directory while replacing the matching experiment/day result row.

## Verification

Run `python -m unittest discover -s tests -v` and `node tests/test_time_selectors.cjs`. Tests cover the existing simulator and protocol plus window checkpoints, replay, CSV, credential exclusion, memory bounds, hourly intervals, dispatcher/experiment catalogs, result deduplication and custom rule semantics. The original full-size morning/evening BASE1 report was reproduced exactly. Browser checks covered dispatcher defaults, protected BASE0, rule controls, independent panel dragging and map-only mode. Live paid LLM requests were not performed.
