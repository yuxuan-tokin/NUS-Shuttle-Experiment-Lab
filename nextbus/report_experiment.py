"""Reproduce the two-window Gemini Flash-Lite comparison used in the report."""
import argparse
import csv
from dataclasses import asdict, replace
import json
import os
from pathlib import Path

from .experiments import save_json
from .protocol import (
    ARMS, PERIODS, ROOT, ExperimentLLM, ExperimentSimulation, NetworkRule,
    before_decision, before_events, difference, load_setup, totals,
)

OUTPUT = ROOT.parent / "dispatch_report"
WINDOWS = tuple(period for period in PERIODS if period[0] in ("Morning", "Evening"))


def run_report(output, *, balanced=False):
    config, data, _ = load_setup()
    config = replace(config, day="2026-08-24", seed=101, arm="BASE0",
                     llm_model="gemini-3.5-flash-lite", llm_timeout=60)
    if not config.llm_endpoint or not os.environ.get(config.llm_key_env):
        raise ValueError(f"Configure the Gemini endpoint and set {config.llm_key_env}")
    examples = json.loads((ROOT / "examples.json").read_text(encoding="utf-8"))
    if (not examples.get("frozen") or examples.get("checkpoint_source") != "BASE0"
            or examples.get("comparison") != "BASE0_vs_BASE1" or len(examples["cases"]) != 4):
        raise ValueError("Generate four frozen examples from BASE0 checkpoints first")
    manifest = dict(config=asdict(config), workbook_sha256=data.digest,
                    examples_version=examples["version"], checkpoint_source="BASE0",
                    periods=[list(window) for window in WINDOWS])
    if balanced:
        manifest["dispatch_objective"] = "balanced"
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Report run settings differ from the saved manifest")
    save_json(manifest_path, manifest)

    rows = []
    reference = ExperimentSimulation(data, config)
    for name, start, end in WINDOWS:
        before_decision(reference, start)
        for arm in ARMS:
            folder = output / "runs" / f"{name}_{arm}"
            row_path = folder / "row.json"
            if row_path.exists():
                rows.append(json.loads(row_path.read_text(encoding="utf-8")))
                continue

            print(f"{name} {arm}", flush=True)
            sim = reference.clone()
            if arm == "BASE1":
                sim.controller = NetworkRule()
            elif arm.startswith("LLM"):
                sim.controller = ExperimentLLM(replace(config, arm=arm), examples["cases"],
                                               folder / "llm_calls.json", balanced=balanced)
            opening = totals(sim)
            try:
                for boundary in range(start + 15, end + 1, 15):
                    before_events(sim, boundary)
            finally:
                if isinstance(sim.controller, ExperimentLLM):
                    controller = sim.controller
                    remaining = controller.saved_calls[len(controller.calls):]
                    save_json(folder / "llm_calls.json", controller.calls + remaining)
            row = dict(day=config.day, period=name, seed=config.seed, strategy=arm,
                       **difference(opening, totals(sim), config.capacity))
            save_json(folder / "dispatches.json", sim.dispatch_log)
            save_json(row_path, row)
            rows.append(row)

    save_json(output / "results.json", rows)
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} results to {output / 'results.csv'}", flush=True)


def main():
    import msvcrt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--balanced", action="store_true", help="Test the balanced dispatch prompt separately")
    args = parser.parse_args()
    output = OUTPUT.with_name("dispatch_report_balanced") if args.balanced else OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    with (output / "job.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except PermissionError as exc:
            raise SystemExit("A report experiment is already running") from exc
        run_report(output, balanced=args.balanced)


if __name__ == "__main__":
    main()
