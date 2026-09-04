"""Reproducible throughput sweep with full per-repetition provenance.

Nothing here is quoted from a committed file. Every size is regenerated,
reconciled and evaluated on the machine running the benchmark, and each
repetition records the environment it ran in, the correctness gate it passed,
and the timings for each stage separately.

Reported throughput is **reconciliation engine** throughput: ingest, normalise,
aggregate, match both planes, triage exceptions, persist. Generation and
evaluation are timed too, but reported as their own numbers, because folding
data generation into a throughput claim would flatter the engine.
"""

from __future__ import annotations

import csv
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from milaan.config import repository_root
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


SCHEMA_VERSION = "2.0.0"
DEFAULT_SIZES = (50, 200, 1200, 5000, 10000)


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository_root(), text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def environment() -> dict[str, Any]:
    return {
        "git_sha": _git_sha(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _repetition(root: Path, records: int, seed: int, profile: str,
                index: int) -> dict[str, Any]:
    run = root / f"n{records}-r{index}"
    started = time.perf_counter()
    generate_to_directory(records, seed, profile, run)
    generate_ms = round((time.perf_counter() - started) * 1000)

    database = run / "m.db"
    run_pipeline(run, database, "mock")
    telemetry = json.loads((run / "runtime_telemetry.json").read_text(encoding="utf-8"))

    started = time.perf_counter()
    gate_message = evaluate_run(run, database, run, profile)
    evaluate_ms = round((time.perf_counter() - started) * 1000)
    metrics = json.loads((run / "functional_metrics.json").read_text(encoding="utf-8"))

    planes = metrics["planes"]
    return {
        "repetition": index,
        "seed": seed,
        "profile": profile,
        "requested_orders": records,
        "physical_source_records": int(telemetry["source_records"]),
        "reconcile_wall_ms": int(telemetry["wall_ms"]),
        "reconcile_records_per_second": float(telemetry["source_records_per_second"]),
        "reconcile_stage_ms": telemetry["stage_ms"],
        "generate_ms": generate_ms,
        "evaluate_ms": evaluate_ms,
        "total_ms": generate_ms + int(telemetry["wall_ms"]) + evaluate_ms,
        "gate": {"name": profile, "status": metrics["gate"]["status"], "message": gate_message},
        "plane_a": {
            "precision": planes["A"]["match_precision"]["rate"],
            "recall": planes["A"]["expected_match_recall"]["rate"],
            "expected": planes["A"]["expected_count"],
            "found": planes["A"]["found_count"],
        },
        "plane_b": {
            "precision": planes["B"]["match_precision"]["rate"],
            "recall": planes["B"]["expected_match_recall"]["rate"],
            "expected": planes["B"]["expected_count"],
            "found": planes["B"]["found_count"],
        },
        "false_match_count": metrics["false_match_count"],
        "exception_precision": metrics["exceptions"]["precision"]["rate"],
        "exception_recall": metrics["exceptions"]["recall"]["rate"],
        "source_conservation": metrics["source_record_conservation"]["rate"],
        "amount_delta_paise": metrics["amount_conservation"]["delta_paise"],
        "truth_integrity": metrics["truth_integrity"]["status"],
        "input_hashes": metrics["truth_integrity"]["inputs"]["canonical_input_hashes"],
    }


def _row_counts(run: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in ("orders", "gateway_recon", "bank"):
        with (run / f"{name}.csv").open(newline="", encoding="utf-8") as handle:
            counts[f"{name}_rows"] = sum(1 for _ in csv.DictReader(handle))
    return counts


def _summarise(values: list[float]) -> dict[str, float]:
    return {
        "median": round(statistics.median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def run_benchmark(out: Path, sizes: tuple[int, ...] = DEFAULT_SIZES,
                  repetitions: int = 3, profile: str = "mixed",
                  base_seed: int = 42) -> str:
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    if repetitions < 3:
        # Not fatal -- a smoke run is legitimate -- but the artefact must say so.
        note = f"NON-CANONICAL: {repetitions} repetition(s); the reported sweep uses 3 or more"
    else:
        note = "canonical sweep"

    results: list[dict[str, Any]] = []
    raw_runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="milaan-benchmark-") as tmp:
        root = Path(tmp)
        for records in sizes:
            samples: list[dict[str, Any]] = []
            for index in range(repetitions):
                sample = _repetition(root, records, base_seed + index, profile, index)
                sample.update(_row_counts(root / f"n{records}-r{index}"))
                samples.append(sample)
                raw_runs.append(sample)
            rates = [float(s["reconcile_records_per_second"]) for s in samples]
            walls = [float(s["reconcile_wall_ms"]) for s in samples]
            totals = [float(s["total_ms"]) for s in samples]
            results.append({
                "requested_orders": records,
                "median_physical_source_records": int(statistics.median(
                    [s["physical_source_records"] for s in samples]
                )),
                "reconcile_records_per_second": _summarise(rates),
                "reconcile_wall_ms": _summarise(walls),
                "total_demo_ms": _summarise(totals),
                "repetitions": repetitions,
                "all_gates_passed": all(s["gate"]["status"] == "PASS" for s in samples),
                "total_false_matches": sum(int(s["false_match_count"]) for s in samples),
                "min_plane_a_precision": min(s["plane_a"]["precision"] for s in samples),
                "min_plane_b_precision": min(s["plane_b"]["precision"] for s in samples),
                "min_source_conservation": min(s["source_conservation"] for s in samples),
                "max_abs_amount_delta_paise": max(abs(int(s["amount_delta_paise"]))
                                                  for s in samples),
            })

    payload = {
        "schema_version": SCHEMA_VERSION,
        "note": note,
        "profile": profile,
        "seed_sequence": [base_seed + index for index in range(repetitions)],
        "measurement": {
            "reported_throughput": (
                "reconciliation engine only: ingest, normalise, aggregate, match "
                "Plane A and Plane B, triage exceptions, persist to SQLite"
            ),
            "excluded_from_throughput": "synthetic data generation; evaluation; reporting",
            "also_recorded": "generate_ms, evaluate_ms and total_ms per repetition",
            "statistic": "median of all repetitions; min and max also reported",
        },
        "environment": environment(),
        "results": results,
        "raw_runs": raw_runs,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    unsafe = sum(int(row["false_match_count"]) for row in raw_runs)
    failed = [row for row in raw_runs if row["gate"]["status"] != "PASS"]
    if failed or unsafe:
        raise RuntimeError(
            f"benchmark gate failed: {len(failed)} failing runs, {unsafe} false matches"
        )
    headline = " | ".join(
        f"{row['requested_orders']}→{row['reconcile_records_per_second']['median']:,.0f} rec/s"
        for row in results
    )
    return (
        f"benchmark written: {out} | {len(sizes)} sizes x {repetitions} repetitions | "
        f"median engine throughput {headline}"
    )
