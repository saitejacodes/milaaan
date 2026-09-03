"""Reproducible end-to-end throughput sweep."""

from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


def run_benchmark(out: Path, sizes: tuple[int, ...] = (50, 200, 1200, 5000, 10000),
                  repetitions: int = 3) -> str:
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for records in sizes:
            samples = []
            source_counts = []
            for repetition in range(repetitions):
                run = root / f"n{records}-r{repetition}"
                generate_to_directory(records, 42 + repetition, "mixed", run)
                database = run / "m.db"
                run_pipeline(run, database, "mock")
                evaluate_run(run, database, run, "mixed")
                telemetry = json.loads((run / "runtime_telemetry.json").read_text())
                samples.append(float(telemetry["source_records_per_second"]))
                source_counts.append(int(telemetry["source_records"]))
            results.append({
                "orders": records,
                "median_source_records": int(statistics.median(source_counts)),
                "median_source_records_per_second": round(statistics.median(samples), 2),
                "min_source_records_per_second": round(min(samples), 2),
                "max_source_records_per_second": round(max(samples), 2),
                "repetitions": repetitions,
            })
    payload = {
        "schema_version": "1.0.0",
        "profile": "mixed",
        "seed_sequence": [42 + index for index in range(repetitions)],
        "measurement": "end-to-end reconciliation wall time; generation and evaluation excluded",
        "results": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"benchmark written: {out} | {len(sizes)} sizes x {repetitions} repetitions"
