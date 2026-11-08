# NextBus Experiment Lab

A separate research workspace derived from the original NextBus simulator. The original checkout and GitHub repository are preserved. **Both BASE1 implementations are unchanged.** The LLM objective prompt now balances five metrics without a prescribed ranking or weighting formula.

## Start

Run `./run_simulator.ps1 -Port 8766`, then visit http://127.0.0.1:8766/experiments. The original can continue using port 8765.

Requires Python 3.11+, numpy, openpyxl, and the separately supplied workbook at the path in `config.json`. Install with `python -m pip install -e .`. The fixed calibration and workbook fingerprint are checked before experiments. Calibration has its original known limitations; this UI does not turn an exploratory run into a formally validated result.

## Experiment setup

The left panel creates, renames, edits and discards dispatcher profiles. Choose BASE0, unchanged BASE1, or an LLM. LLM profiles specify a model name, HTTPS chat-completions-compatible endpoint, examples, memory, test days, time window and seed. Gemini 3.5 Flash-Lite is the default. API keys are accepted per run or through `NEXTBUS_API_KEY`; they are not saved in profiles, manifests, result exports or request logs.

Memory defaults to 30 minutes, can be disabled, and is capped at 120 minutes in 15-minute steps. This configures LLM context only: it does not alter BASE1's lookback or thresholds. All selected examples retain their stated horizons. Four historical examples are provided; select any subset or generate additional examples from other teaching-day checkpoints and lengths (15–120 minutes). Training and test days must be disjoint. New examples do not alter the frozen historical example file.

The default Gemini input limit is 1,048,576 tokens, per https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite . For another model, enter the provider's documented context limit. Before each request a conservative UTF-8 byte budget reserves 8,192 tokens for instructions, framing and response. Oversized requests fail with guidance rather than silently dropping selected examples. This is conservative budgeting, not a provider tokenizer; custom provider limits must be entered correctly.

A time-window run first simulates scheduled BASE0 service from the day start to the checkpoint, then enables the chosen dispatcher. Warm-up retains the complete student, bus, queue and event state. Scores and charts start at the checkpoint. Windows include opening-minute events and exclude closing-minute events. See the historical boundary distinction in `BASE1_ANALYSIS.md`.

## Watching and comparing

The map shows all individuals or only bus riders and queues. Red route segments indicate that the BASE1 crowding conditions are met; shared route segments can overlap on this geographic schematic. A brief green highlight marks a new extra dispatch when its next frame appears. Hourly small charts show waiting passenger-minutes, denied-attempt percentage, and fleet utilization percentage. A partial first or last hour is marked with an asterisk. Missing percentages are gaps, not zeroes.

Experiment frames are persisted every five simulated minutes, including all visible individuals, buses, metrics, congestion observations and dispatch events. You can watch live, scrub, or play a completed run after restarting the server. Five metric cards sit below the map. All buildings and destinations are available in an expandable list, alongside the existing facilities table.

Each completed day becomes a row in the bottom results panel: dispatcher and model, test case, memory, seed, five metrics, example count and expanded example dates/horizons/settings. Export all current rows to spreadsheet-compatible CSV. Discard removes a result from the table and export; its raw run directory is retained for audit. Discarding a profile does not modify completed results.

## Metrics and diagnostics

The five balanced criteria are total waiting passenger-minutes, capacity failure rate, overall fleet utilization, extra buses dispatched, and extra passenger-service hours. Capacity failure means denied eligible **attempts**, not distinct students. Overall utilization is passenger-minutes divided by 88 times passenger-service minutes. Resource hours exclude depot and return legs; waiting includes unfinished queues through the measurement boundary. Additional extra-bus utilization is retained in raw results, but is not a sixth weighted objective.

`BASE1_ANALYSIS.md` explains the observed failures and no-dispatch decisions with stop-level numbers. `docs/base1_diagnosis.json` contains the supporting audits and denial events. Reproduce with `python diagnose_base1.py`.

Runtime data lives under `outputs/lab/`: profiles, added examples, results and `runs/<id>/`. Runs contain compressed replay frames, configuration/calibration provenance, boarding events, BASE1 decision audits, dispatches and LLM calls. Data survives server restart. A failed run is shown as failed and is not added as a completed result; earlier persisted frames remain available while that job is selected. Restarting a run creates a fresh run rather than mixing settings with older results.

## Verification

Run `python -m unittest discover -s tests -v`. Tests cover the existing simulator and protocol plus window checkpoints, replay, CSV, credential exclusion, memory bounds, hourly intervals and observational diagnostics. The original full-size morning/evening BASE1 report was reproduced exactly. Browser visual QA and live paid LLM requests were not performed in this session.

The original simulator documentation is retained in `README_ORIGINAL.md`. Its old experiment UI instructions and waiting-first prompt descriptions apply to the original version, not this lab.
