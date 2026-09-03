"""Evaluation entry point and fail-fast gate bundles."""

from __future__ import annotations

import json
from pathlib import Path

from milaan.db import connect
from milaan.evalx.metrics import compute_metrics


def _gate(metrics: dict, gate: str) -> None:
    a = metrics["planes"]["A"]["auto_match"]["rate"]
    b = metrics["planes"]["B"]["auto_match"]["rate"]
    false = metrics["false_match_count"]
    recall = metrics["exceptions"]["recall"]["rate"]
    exception_precision = metrics["exceptions"]["precision"]["rate"]
    completeness = metrics["completeness"]["rate"]
    source_conservation = metrics["source_record_conservation"]["rate"]
    amount_balanced = metrics["amount_conservation"]["balanced"]
    input_integrity = metrics["input_integrity"]["all_match"]
    failures: list[str] = []
    if gate == "clean":
        if a != 1.0: failures.append(f"Plane A expected-match recall {a:.2%} != 100%")
        if b != 1.0: failures.append(f"Plane B expected-match recall {b:.2%} != 100%")
        if metrics["exceptions"]["actual_count"] != 0:
            failures.append(f"exceptions {metrics['exceptions']['actual_count']} != 0")
    else:
        if a < 0.995: failures.append(f"Plane A expected-match recall {a:.2%} < 99.5%")
        if b < 0.95: failures.append(f"Plane B expected-match recall {b:.2%} < 95%")
        if recall != 1.0: failures.append(f"exception recall {recall:.2%} != 100%")
        if exception_precision != 1.0:
            failures.append(f"exception precision {exception_precision:.2%} != 100%")
        if completeness != 1.0: failures.append(f"completeness {completeness:.2%} != 100%")
    for plane in ("A", "B"):
        precision = metrics["planes"][plane]["match_precision"]["rate"]
        if precision != 1.0:
            failures.append(f"Plane {plane} match precision {precision:.2%} != 100%")
    if source_conservation != 1.0:
        failures.append(f"source record conservation {source_conservation:.2%} != 100%")
    if not amount_balanced:
        failures.append(
            f"settlement amount conservation delta "
            f"{metrics['amount_conservation']['delta_paise']} paise != 0"
        )
    if not input_integrity:
        failures.append("manifest input hashes do not match evaluated source files")
    if false:
        failures.append(f"false matches {false} != 0")
    if failures:
        raise RuntimeError("gate failed: " + "; ".join(failures))


def evaluate_run(run_dir: Path, database_path: Path, out_dir: Path, gate: str) -> str:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    conn = connect(database_path)
    try:
        metrics = compute_metrics(conn, manifest)
    finally:
        conn.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "functional_metrics.json").write_text(
        json.dumps(metrics, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    _gate(metrics, gate)
    a, b = metrics["planes"]["A"]["auto_match"], metrics["planes"]["B"]["auto_match"]
    exc = metrics["exceptions"]["recall"]
    conservation = metrics["source_record_conservation"]
    return (
        f"gate={gate} PASS | Plane-A {a['numerator']}/{a['denominator']} ({a['rate']:.2%}) | "
        f"Plane-B {b['numerator']}/{b['denominator']} ({b['rate']:.2%}) | "
        f"false={metrics['false_match_count']} | exceptions {exc['numerator']}/{exc['denominator']} | "
        f"source-conservation {conservation['numerator']}/{conservation['denominator']}"
    )
