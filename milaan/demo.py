"""Backend for the public web demo. No Streamlit import lives here.

Every number the demo shows is produced by calling the same functions the CLI
and the judge call: generate, reconcile, evaluate against independently
rebuilt truth, run the agent suite, run the adversarial board. Nothing is read
from a committed metrics file.

Two kinds of run exist:

* **Verified runs** use generated data. The evaluator rebuilds ground truth, so
  precision, recall and false matches are measured and the read-only agent may
  answer questions about the run (it refuses runs whose gate did not pass).
* **Unscored runs** use files a visitor uploads. There is no answer key, so no
  accuracy is claimed. Matches, exceptions, conservation and the cash position
  are still computed by the same engine and the same cash rules.
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from milaan.db import reading
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.evalx.metrics import operational_metrics
from milaan.generator.emit import BANK_FIELDS, ORDER_FIELDS, TXN_FIELDS, generate_to_directory
from milaan.money import format_paise
from milaan.view import ExceptionView, build_exception_views


SIZES = (200, 1200, 5000)
PROFILES = {
    "clean": "No injected anomalies. Everything that can be matched is.",
    "mixed": "The default benchmark: missing credits, duplicate UTRs, ties, broken narrations.",
    "hard": "Mixed, plus a credit that equals two settlements combined, which Milaan will not solve.",
}
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_MAX_ROWS = 25_000
UPLOAD_FILES = {
    "orders.csv": ORDER_FIELDS,
    "gateway_recon.csv": TXN_FIELDS,
    "bank.csv": BANK_FIELDS,
}
# Columns a visitor may omit. Everything else is required, even if empty.
OPTIONAL_COLUMNS = {"source_row_id"}


@dataclass
class RunResult:
    """Everything one page needs about one completed run."""

    run_dir: Path
    database: Path
    verified: bool
    profile: str
    records: int
    seed: int | None
    wall_ms: int
    gate: str | None
    metrics: dict[str, Any]
    exceptions: list[ExceptionView] = field(default_factory=list)


# --------------------------------------------------------------- verified runs


def verified_run(workdir: Path, records: int = 1200, seed: int = 42,
                 profile: str = "mixed") -> RunResult:
    """Generate, reconcile and evaluate one synthetic run in ``workdir``."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    if not 10 <= records <= 10_000:
        raise ValueError("records must be between 10 and 10,000")
    run_dir = workdir / f"{profile}-{records}-{seed}"
    database = run_dir / "milaan.db"
    started = time.perf_counter()
    generate_to_directory(records, seed, profile, run_dir)
    run_pipeline(run_dir, database, "mock")
    gate = evaluate_run(run_dir, database, run_dir, profile)
    wall_ms = round((time.perf_counter() - started) * 1000)
    metrics = json.loads((run_dir / "functional_metrics.json").read_text(encoding="utf-8"))
    return RunResult(run_dir, database, True, profile, records, seed, wall_ms, gate, metrics,
                     build_exception_views(database))


# --------------------------------------------------------------- uploads


class UploadError(ValueError):
    """Raised with every structural problem found, so the visitor sees all of them."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _check_value(column: str, value: str) -> str | None:
    """Type check for one cell. Returns a problem description, or ``None``."""
    if value == "":
        return None
    try:
        if column.endswith("_paise"):
            int(value)
        elif column in {"created_at", "captured_at", "settlement_processed_at"}:
            datetime.fromisoformat(value)
        elif column in {"txn_date", "value_date"}:
            date.fromisoformat(value)
    except ValueError:
        kind = ("a whole number of paise" if column.endswith("_paise")
                else "an ISO date (YYYY-MM-DD)" if column in {"txn_date", "value_date"}
                else "an ISO date-time (YYYY-MM-DDTHH:MM:SS)")
        return f"{column}={value!r} is not {kind}"
    return None


def validate_upload(files: dict[str, bytes]) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """Parse the three CSVs and report problems.

    Structural problems (missing file, missing column, wrong encoding, too
    large) raise :class:`UploadError`, because the engine cannot run. Row-level
    problems (a bad amount or date) are returned as *warnings*: the engine
    still runs and quarantines exactly those rows, which is the behaviour the
    demo is meant to show.
    """
    problems: list[str] = []
    warnings: list[str] = []
    parsed: dict[str, list[dict[str, str]]] = {}
    for name, columns in UPLOAD_FILES.items():
        raw = files.get(name)
        if raw is None:
            problems.append(f"{name}: file is missing")
            continue
        if len(raw) > UPLOAD_MAX_BYTES:
            problems.append(f"{name}: larger than {UPLOAD_MAX_BYTES // (1024 * 1024)} MB")
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            problems.append(f"{name}: not UTF-8 text")
            continue
        reader = csv.DictReader(io.StringIO(text))
        header = [column.strip() for column in (reader.fieldnames or [])]
        if not header:
            problems.append(f"{name}: empty file or no header row")
            continue
        if len(set(header)) != len(header):
            problems.append(f"{name}: duplicate column names in the header")
            continue
        missing = [column for column in columns
                   if column not in header and column not in OPTIONAL_COLUMNS]
        if missing:
            problems.append(f"{name}: missing column(s) {', '.join(missing)}")
            continue
        rows: list[dict[str, str]] = []
        source = name.removesuffix(".csv")
        for line_number, row in enumerate(reader, start=2):
            if len(rows) >= UPLOAD_MAX_ROWS:
                problems.append(f"{name}: more than {UPLOAD_MAX_ROWS:,} rows")
                break
            if None in row:
                warnings.append(f"{name} line {line_number}: more values than columns")
            clean = {column.strip(): (value or "").strip()
                     for column, value in row.items() if column is not None}
            clean.setdefault("source_row_id", "")
            clean["source_row_id"] = clean["source_row_id"] or f"{source}:{line_number}"
            for column in columns:
                problem = _check_value(column, clean.get(column, ""))
                if problem:
                    warnings.append(f"{name} line {line_number}: {problem} (row will be quarantined)")
            rows.append(clean)
        if not rows and not any(p.startswith(name) for p in problems):
            problems.append(f"{name}: no data rows")
        parsed[name] = rows
    if problems:
        raise UploadError(problems)
    return parsed, warnings


def unscored_run(workdir: Path, files: dict[str, bytes]) -> tuple[RunResult, list[str]]:
    """Reconcile uploaded files. Raises :class:`UploadError` on structural problems."""
    parsed, warnings = validate_upload(files)
    run_dir = workdir / "upload"
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, columns in UPLOAD_FILES.items():
        with (run_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore",
                                    lineterminator="\n")
            writer.writeheader()
            for row in parsed[name]:
                writer.writerow({column: row.get(column, "") for column in columns})
    # The pipeline only reads seed and profile from run_meta.json, to label the run.
    (run_dir / "run_meta.json").write_text(
        json.dumps({"seed": 0, "profile": "upload"}) + "\n", encoding="utf-8")
    database = run_dir / "milaan.db"
    started = time.perf_counter()
    run_pipeline(run_dir, database, "mock")
    wall_ms = round((time.perf_counter() - started) * 1000)
    with reading(database) as conn:
        metrics = operational_metrics(conn)
    records = sum(len(rows) for rows in parsed.values())
    return (RunResult(run_dir, database, False, "upload", records, None, wall_ms, None,
                      metrics, build_exception_views(database)), warnings)


def sample_csvs(workdir: Path, records: int = 60, seed: int = 7) -> dict[str, bytes]:
    """A small generated dataset a visitor can download, edit and upload back."""
    out = workdir / "sample-upload"
    generate_to_directory(records, seed, "mixed", out)
    return {name: (out / name).read_bytes() for name in UPLOAD_FILES}


# --------------------------------------------------------------- tables


def match_rows(database: Path, limit: int = 500) -> list[dict[str, Any]]:
    """Accepted matches in display form: who matched whom, by which rule."""
    with reading(database) as conn:
        rows = conn.execute(
            """SELECT m.match_id, m.plane, m.tier, m.date_gap_bd, m.evidence,
                      group_concat(mm.entity_type || ':' || mm.entity_id, '|') AS members
               FROM matches m JOIN match_members mm ON mm.match_id = m.match_id
               GROUP BY m.match_id ORDER BY m.plane, m.match_id LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        members = dict(item.split(":", 1) for item in row["members"].split("|"))
        evidence = json.loads(row["evidence"])
        amount = evidence.get("order_amount_paise", evidence.get("batch_amount_paise", 0))
        if row["plane"] == "A":
            left, right = members.get("ORDER", ""), members.get("TXN", "")
            loop = "Order → Payment"
        else:
            left, right = members.get("BATCH", ""), members.get("BANK_LINE", "")
            loop = "Settlement → Bank"
        out.append({"Loop": loop, "Rule": row["tier"], "From": left, "To": right,
                    "Amount": format_paise(int(amount)), "Business-day gap": row["date_gap_bd"]})
    return out


def cash_rows(metrics: dict[str, Any]) -> list[dict[str, str]]:
    cash = metrics["cash_position"]
    return [
        {"Bucket": "Verified banked", "Amount": format_paise(cash["banked_paise"]),
         "Meaning": "Settlement matched to a bank credit"},
        {"Bucket": "Expected but unbanked", "Amount": format_paise(cash["expected_unbanked_paise"]),
         "Meaning": "Settled at the gateway, no bank evidence yet"},
        {"Bucket": "Blocked settlements", "Amount": format_paise(cash["blocked_settlement_paise"]),
         "Meaning": "A control stopped this cash"},
        {"Bucket": "Unexplained bank credits",
         "Amount": format_paise(cash["unexplained_bank_credit_paise"]),
         "Meaning": "Money in the bank Milaan cannot attribute"},
    ]


# --------------------------------------------------------------- scorecard


def scorecard(workdir: Path) -> dict[str, Any]:
    """The landing-page scorecard, produced by running everything once.

    Runs the seed-42 mixed benchmark, the agent suite and the full adversarial
    board. Takes a few seconds; the app caches the result for the process.
    """
    from milaan.agent.eval import evaluate_agent
    from milaan.evalx import adversary

    started = time.perf_counter()
    run = verified_run(workdir, 1200, 42, "mixed")
    evaluate_agent(run.run_dir, run.database, run.run_dir / "agent_metrics.json", "mock")
    agent = json.loads((run.run_dir / "agent_metrics.json").read_text(encoding="utf-8"))
    outcomes = adversary.run_everything()
    by_category: dict[str, list[int]] = {}
    for item in outcomes:
        passed, total = by_category.setdefault(item.category, [0, 0])
        by_category[item.category] = [passed + int(item.passed), total + 1]
    metrics = run.metrics
    return {
        "run": run,
        "plane_a": metrics["planes"]["A"],
        "plane_b": metrics["planes"]["B"],
        "false_matches": metrics["false_match_count"],
        "coverage": metrics["workload_coverage"],
        "conservation": metrics["source_record_conservation"],
        "amount_delta_paise": metrics["amount_conservation"]["delta_paise"],
        "exceptions": metrics["exceptions"],
        "agent": agent,
        "attacks": {"passed": sum(o.passed for o in outcomes), "total": len(outcomes),
                    "by_category": by_category,
                    "failed": [o.label for o in outcomes if not o.passed]},
        "seconds": round(time.perf_counter() - started, 1),
    }
