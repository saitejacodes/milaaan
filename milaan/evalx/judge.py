"""One-command verification for a competition judge.

Everything printed by this command is produced by this execution. No number is
read from a committed metrics file, a README table or a screenshot: the dataset
is regenerated, reconciled, evaluated against independently reconstructed truth,
attacked, and only then reported.
"""

from __future__ import annotations

import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

from milaan.agent.eval import evaluate_agent
from milaan.config import repository_root
from milaan.engine.pipeline import run_pipeline
from milaan.evalx import adversary
from milaan.evalx.ai_probes import run_probes
from milaan.evalx.harness import evaluate_run
from milaan.evalx.truth import config_hashes
from milaan.generator.emit import generate_to_directory
from milaan.generator.manifest import GENERATOR_VERSION
from milaan.report.render import render_report


RULE = "=" * 68
LABEL_WIDTH = 42

# Internal tier names stay in the data; a judge should never have to decode them.
PLANE_NAMES = {"A": "ORDER → PAYMENT", "B": "SETTLEMENT → BANK"}
TIER_NAMES = {
    "A0": "A0 - exact gateway order reference",
    "A0B": "A0B - merchant-recorded payment reference",
    "A1": "A1 - unique amount and date evidence",
    "B0": "B0 - exact settlement reference and amount",
    "B1": "B1 - unique amount and date evidence",
    "B2": "B2 - controlled reference recovery",
}


def money(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{abs(paise) / 100:,.2f}"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository_root(), text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable (not a git checkout)"


def _pair(label: str, value: object, width: int = LABEL_WIDTH) -> str:
    return f"  {label:<{width}} {value}"


def _plane_block(name: str, plane: dict[str, Any]) -> list[str]:
    return [
        "",
        name,
        "-" * len(name),
        _pair("Expected matches", f"{plane['expected_count']:,}"),
        _pair("Matches produced", f"{plane['found_count']:,}"),
        _pair("Correct", f"{plane['true_match_count']:,}"),
        _pair("False matches", plane["false_match_count"]),
        _pair("Precision", f"{plane['match_precision']['rate']:.2%}"),
        _pair("Recall", f"{plane['expected_match_recall']['rate']:.2%}"),
    ]


def run_judge(out_dir: Path, records: int = 1200, seed: int = 42,
              profile: str = "mixed", skip_attacks: bool = False) -> int:
    started = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = out_dir
    database = out_dir / "milaan.db"

    generate_to_directory(records, seed, profile, run_dir)
    run_pipeline(run_dir, database, "mock")
    gate_message = evaluate_run(run_dir, database, run_dir, profile)
    metrics = json.loads((run_dir / "functional_metrics.json").read_text(encoding="utf-8"))
    telemetry = json.loads((run_dir / "runtime_telemetry.json").read_text(encoding="utf-8"))
    agent_message = evaluate_agent(run_dir, database, run_dir / "agent_metrics.json", "mock")
    agent_metrics = json.loads((run_dir / "agent_metrics.json").read_text(encoding="utf-8"))
    report_path = out_dir / "report.html"
    render_report(run_dir, database, report_path)

    ai_outcomes = run_probes(run_dir, database)
    truth_outcomes: list[adversary.AttackOutcome] = []
    if not skip_attacks:
        truth_outcomes = [
            adversary.run_attack(item) for item in adversary.REGISTRY
            if item.category == "evaluator_integrity"
        ]

    conservation = metrics["source_record_conservation"]
    cash = metrics["cash_position"]
    coverage = metrics["workload_coverage"]
    exceptions = metrics["exceptions"]
    elapsed = time.perf_counter() - started

    lines = [
        RULE,
        "MILAAN - TRACK 04 VERIFICATION",
        RULE,
        "",
        "RUN",
        "---",
        _pair("Commit", _git_sha()),
        _pair("Generator", GENERATOR_VERSION),
        _pair("Metrics schema", metrics["schema_version"]),
        _pair("Seed / profile", f"{metrics['seed']} / {metrics['profile']}"),
        _pair("Python", f"{platform.python_version()} on {platform.platform()}"),
        _pair("Orders requested", f"{records:,}"),
        _pair("Physical source records",
              f"{conservation['total_source_records']:,}"),
        _pair("Truth", "regenerated independently from run_meta.json"),
    ]

    lines += _plane_block(PLANE_NAMES["A"], metrics["planes"]["A"])
    lines += _plane_block(PLANE_NAMES["B"], metrics["planes"]["B"])

    lines += [
        "",
        "OPERATIONAL COVERAGE",
        "--------------------",
        "  100% match precision does NOT mean 100% of the workload was",
        "  auto-resolved. These two things are reported separately on purpose.",
        "",
        _pair("Eligible orders auto-matched",
              f"{coverage['plane_a_orders']['numerator']:,} / "
              f"{coverage['plane_a_orders']['denominator']:,} "
              f"({coverage['plane_a_orders']['rate']:.2%})"),
        _pair("Gateway payments auto-matched",
              f"{coverage['plane_a_payments']['numerator']:,} / "
              f"{coverage['plane_a_payments']['denominator']:,} "
              f"({coverage['plane_a_payments']['rate']:.2%})"),
        _pair("Settlement batches banked",
              f"{coverage['plane_b_settlement_batches']['numerator']:,} / "
              f"{coverage['plane_b_settlement_batches']['denominator']:,} "
              f"({coverage['plane_b_settlement_batches']['rate']:.2%})"),
        _pair("Bank lines explained",
              f"{coverage['plane_b_bank_lines']['numerator']:,} / "
              f"{coverage['plane_b_bank_lines']['denominator']:,} "
              f"({coverage['plane_b_bank_lines']['rate']:.2%})"),
        "",
        "CASH POSITION",
        "-------------",
        _pair("Verified banked", money(cash["banked_paise"])),
        _pair("Expected but unbanked", money(cash["expected_unbanked_paise"])),
        _pair("Blocked settlements", money(cash["blocked_settlement_paise"])),
        _pair("Unexplained bank credits", money(cash["unexplained_bank_credit_paise"])),
        _pair("Gross evidence under attention", money(cash["gross_attention_paise"])),
        "  Gross evidence under attention is not a loss estimate.",
        "",
        "EXCEPTIONS",
        "----------",
        _pair("Expected", exceptions["expected_count"]),
        _pair("Detected", exceptions["actual_count"]),
        _pair("Correct", exceptions["correct_count"]),
        _pair("False", exceptions["false_count"]),
        _pair("Missed", exceptions["missed_count"]),
        _pair("Precision", f"{exceptions['precision']['rate']:.2%}"),
        _pair("Recall", f"{exceptions['recall']['rate']:.2%}"),
        "",
        "CONSERVATION AND INTEGRITY",
        "--------------------------",
        _pair("False matches (both planes)", metrics["false_match_count"]),
        _pair("Source-record conservation",
              f"{conservation['numerator']:,} / {conservation['denominator']:,} "
              f"({conservation['rate']:.2%})"),
        _pair("Terminal buckets", json.dumps(conservation["terminal_buckets"])),
        _pair("Settlement amount conservation",
              f"{metrics['amount_conservation']['delta_paise']} paise delta"),
        _pair("Truth integrity", metrics["truth_integrity"]["status"]),
        _pair("Input integrity",
              "PASS" if metrics["truth_integrity"]["inputs"]["all_match"] else "FAIL"),
        _pair("Database binding",
              "PASS" if metrics["truth_integrity"]["database"]["all_match"] else "FAIL"),
        _pair("Shipped manifest vs canonical",
              "PASS" if metrics["truth_integrity"]["run_manifest"]["matches_canonical"] else "FAIL"),
        "",
        "AI AUTHORITY BOUNDARY",
        "---------------------",
    ]
    for outcome in ai_outcomes:
        lines.append(_pair(outcome.label, "PASS" if outcome.passed else f"FAIL {outcome.detail}"))
    lines.append(_pair("Offline routing suite",
                       f"{agent_metrics['routing']['correct']}/"
                       f"{agent_metrics['routing']['case_count']} "
                       f"(deterministic router, not model accuracy)"))
    lines.append(_pair("Write-request refusals",
                       f"{agent_metrics['safety']['correct_refusals']}/"
                       f"{agent_metrics['safety']['case_count']}"))

    if truth_outcomes:
        lines += ["", "EVALUATOR TAMPERING ATTACKS (live)", "----------------------------------"]
        for outcome in truth_outcomes:
            lines.append(_pair(outcome.label,
                               "FAILS CLOSED" if outcome.passed else f"BREACHED {outcome.detail}"))

    lines += [
        "",
        "THROUGHPUT",
        "----------",
        _pair("Reconciliation engine",
              f"{telemetry['source_records_per_second']:,.0f} source records/s "
              f"({telemetry['source_records']:,} records in {telemetry['wall_ms']:,} ms)"),
        _pair("Stage breakdown (ms)", json.dumps(telemetry["stage_ms"])),
        "  Run `make benchmark` for the multi-size, multi-repetition sweep.",
        "",
        "ARTEFACTS",
        "---------",
        _pair("Report", str(report_path)),
        _pair("Metrics", str(run_dir / "functional_metrics.json")),
        _pair("Agent suite", str(run_dir / "agent_metrics.json")),
        _pair("Provenance", str(run_dir / "artifact_provenance.json")),
        "",
        _pair("Gate", gate_message),
        _pair("Agent gate", agent_message),
        _pair("Verification wall time", f"{elapsed:.1f}s"),
        RULE,
    ]

    failures = [item for item in ai_outcomes if not item.passed]
    failures += [item for item in truth_outcomes if not item.passed]  # type: ignore[misc]
    verdict = "VERIFIED" if not failures else f"FAILED ({len(failures)} breaches)"
    lines.append(f"RESULT: {verdict}")
    lines.append(RULE)
    print("\n".join(lines))

    write_provenance(run_dir, metrics, telemetry, agent_metrics, report_path)
    return 0 if not failures else 1


def write_provenance(run_dir: Path, metrics: dict[str, Any], telemetry: dict[str, Any],
                     agent_metrics: dict[str, Any], report_path: Path) -> Path:
    """Bind every judging artefact to the exact code and inputs that made it."""
    payload = {
        "schema_version": "1.0.0",
        "git_sha": _git_sha(),
        "generator_version": GENERATOR_VERSION,
        "metrics_schema_version": metrics["schema_version"],
        "agent_suite_version": agent_metrics["schema_version"],
        "generation_inputs": metrics["truth_integrity"]["generation_inputs"],
        "config_hashes": config_hashes(),
        "input_hashes": metrics["input_hashes"],
        "truth_integrity": {
            "method": metrics["truth_integrity"]["method"],
            "status": metrics["truth_integrity"]["status"],
        },
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "artifacts": {
            "functional_metrics.json": str(run_dir / "functional_metrics.json"),
            "runtime_telemetry.json": str(run_dir / "runtime_telemetry.json"),
            "agent_metrics.json": str(run_dir / "agent_metrics.json"),
            "report.html": str(report_path),
        },
        "runtime_telemetry": telemetry,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = run_dir / "artifact_provenance.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="milaan-judge")
    parser.add_argument("--out", type=Path, default=Path("data/judge"))
    parser.add_argument("--records", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--profile", choices=("clean", "mixed", "hard"), default="mixed")
    parser.add_argument("--skip-attacks", action="store_true",
                        help="skip the live evaluator tampering attacks (faster smoke run)")
    args = parser.parse_args(argv)
    return run_judge(args.out, args.records, args.seed, args.profile, args.skip_attacks)


if __name__ == "__main__":
    raise SystemExit(main())
