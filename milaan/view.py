"""The single presentation source shared by the report and the dashboard.

Neither surface computes a financial number. Both read this view model, which
reads exactly two things: the evaluator's published ``functional_metrics.json``
and the verified run database. That is what makes "the report and the dashboard
agree" a structural property rather than a promise, and it is enforced by
tests/test_surface_parity.py.

The vocabulary is also translated here. Internal tier names (A0, B2, ...) stay
in the data where they belong; a finance reader sees "Order to payment" and
"Settlement to bank".
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from milaan.db import connect


PLANE_LABELS = {
    "A": "Order → Payment reconciliation",
    "B": "Settlement → Bank reconciliation",
}
TIER_LABELS = {
    "A0": "A0 — exact gateway order reference",
    "A0B": "A0B — merchant-recorded payment reference",
    "A1": "A1 — unique amount and date evidence",
    "B0": "B0 — exact settlement reference and amount",
    "B1": "B1 — unique amount and date evidence",
    "B2": "B2 — controlled reference recovery",
}

# Mandatory on every surface. Reporting accuracy without this sentence invites
# exactly the misreading the track warns about.
COVERAGE_DISCLAIMER = (
    "100% match precision does not mean 100% of the workload was auto-resolved."
)
ATTENTION_DISCLAIMER = (
    "Gross evidence under attention is not a loss estimate. It can contain both "
    "sides of a single ambiguous item."
)

REASON_HEADLINES = {
    "MISSING_IN_BANK": "Settlement never arrived in the bank statement",
    "UNKNOWN_BANK_CREDIT": "Bank credit with no matching settlement batch",
    "PAID_ORDER_MISSING_FROM_GATEWAY": "Paid order absent from the gateway export",
    "AMOUNT_MISMATCH_BEYOND_TOL": "Amounts do not agree",
    "DATE_OUT_OF_WINDOW": "Dates fall outside the allowed business-day window",
    "AMBIGUOUS_TIE": "More than one candidate fits equally well",
    "AMBIGUOUS_COMBINED": "One credit appears to combine two settlements",
    "DUPLICATE_UTR": "One bank reference appears on two different credits",
    "DUPLICATE_BANK_LINE": "The same bank line appears twice",
    "UTR_CONFLICT_IN_BATCH": "Members of one batch carry different bank references",
    "UNSUPPORTED_MEMBER_IN_BATCH": "A batch member type is not supported",
    "TAINTED_SETTLEMENT": "A settlement member was rejected, so the whole batch is blocked",
    "UNSUPPORTED_TXN_TYPE": "Unsupported gateway transaction type",
    "DUPLICATE_SOURCE_ID": "The same source identifier appears twice",
    "IDENTITY_CONFLICT": "Order and payment identifiers contradict each other",
    "UNMATCHED_GATEWAY_PAYMENT": "Gateway payment with no merchant order",
    "NARRATION_UNPARSEABLE": "Bank narration carries no usable reference",
    "FEE_MODEL_VIOLATION": "Fee, GST or net arithmetic does not hold",
    "ORPHAN_REFUND": "Refund or chargeback with no original payment",
    "INGEST_REJECT": "Source row could not be read safely",
}

CANDIDATE_KEYS = (
    "candidate_edges", "bank_line_ids", "settlement_ids", "payment_ids", "order_ids",
    "known_member_txn_ids", "member_txn_ids", "quarantined_rows", "distinct_non_null_utrs",
)


def money(paise: int) -> str:
    sign = "−" if paise < 0 else ""
    return f"{sign}₹{abs(paise) / 100:,.2f}"


class GateNotPassed(ValueError):
    """Raised when a surface is pointed at a run that failed its own controls."""


@dataclass(frozen=True)
class ExceptionView:
    """One finance-operator-ready row: what, why, how much, what next."""

    exception_id: str
    reason_code: str
    headline: str
    scope_ids: list[str]
    source_row_ids: list[str]
    settlement_id: str | None
    exposure_paise: int
    exposure: str
    blocked: bool
    tainted: bool
    candidates: list[str]
    evidence: dict[str, Any]
    explanation: str
    next_action: str
    guidance: list[str]


@dataclass(frozen=True)
class TraceStep:
    label: str
    entity_id: str
    amount_paise: int | None
    detail: str


@dataclass(frozen=True)
class EvidenceTrace:
    order_id: str
    steps: list[TraceStep]
    members: list[dict[str, Any]]
    display_members: list[dict[str, Any]]
    member_count: int
    member_total_paise: int
    bank_credit_paise: int
    difference_paise: int
    status: str


@dataclass(frozen=True)
class View:
    metrics: dict[str, Any]
    telemetry: dict[str, Any]
    run: dict[str, Any]
    headline: dict[str, Any]
    accuracy: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    cash: list[dict[str, Any]]
    exception_quality: dict[str, Any]
    exceptions: list[ExceptionView]
    integrity: list[dict[str, Any]]
    throughput: dict[str, Any]
    tiers: list[dict[str, Any]]
    trace: EvidenceTrace | None
    blocked_settlements: list[dict[str, Any]] = field(default_factory=list)
    unexplained_credits: list[dict[str, Any]] = field(default_factory=list)


def _ratio_row(label: str, ratio: dict[str, Any], note: str = "") -> dict[str, Any]:
    return {
        "label": label, "rate": ratio["rate"],
        "percent": f"{ratio['rate']:.2%}",
        "numerator": ratio["numerator"], "denominator": ratio["denominator"],
        "fraction": f"{ratio['numerator']:,} / {ratio['denominator']:,}",
        "note": note,
    }


def _money_row(label: str, paise: int, note: str = "") -> dict[str, Any]:
    return {"label": label, "paise": paise, "amount": money(paise), "note": note}


def _candidates(evidence: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in CANDIDATE_KEYS:
        value = evidence.get(key)
        if isinstance(value, list):
            for item in value:
                found.append(" ↔ ".join(str(part) for part in item)
                             if isinstance(item, (list, tuple)) else str(item))
    seen: set[str] = set()
    return [item for item in found if not (item in seen or seen.add(item))]


def _exposure(scope_ids: list[str], evidence: dict[str, Any], batches: dict[str, int],
              bank: dict[str, int], orders: dict[str, int]) -> int:
    """Money the exception puts under review, using the engine's own figure first."""
    if "claimed_exposure_paise" in evidence:
        return int(evidence["claimed_exposure_paise"])
    for entity_id in scope_ids:
        if entity_id in batches:
            return batches[entity_id]
    for entity_id in scope_ids:
        if entity_id in bank:
            return bank[entity_id]
    for entity_id in scope_ids:
        if entity_id in orders:
            return orders[entity_id]
    for key in ("amount_paise", "credit_paise", "batch_amount_paise", "gross_paise"):
        if isinstance(evidence.get(key), int):
            return int(evidence[key])
    return 0


def _build_exceptions(conn: sqlite3.Connection, batches: dict[str, int], bank: dict[str, int],
                      orders: dict[str, int], tainted: set[str],
                      matched: set[str]) -> list[ExceptionView]:
    rows = conn.execute("SELECT * FROM exceptions ORDER BY id").fetchall()
    source_rows = {
        row["source_row_id"]: row["reason"]
        for row in conn.execute("SELECT source_row_id,reason FROM quarantine_rows")
    }
    views: list[ExceptionView] = []
    for row in rows:
        scope_ids = json.loads(row["scope_ids"])
        evidence = json.loads(row["evidence"])
        settlement_id = next((entity for entity in scope_ids if entity in batches), None)
        exposure = _exposure(scope_ids, evidence, batches, bank, orders)
        cited = [str(value) for key, value in evidence.items()
                 if key.endswith("source_row_id") and value]
        cited += [item for item in scope_ids if item in source_rows]
        views.append(ExceptionView(
            exception_id=f"EXC-{int(row['id']):04d}",
            reason_code=row["reason_code"],
            headline=REASON_HEADLINES.get(row["reason_code"], row["reason_code"]),
            scope_ids=scope_ids,
            source_row_ids=sorted(set(cited)),
            settlement_id=settlement_id,
            exposure_paise=exposure,
            exposure=money(exposure),
            blocked=not any(entity in matched for entity in scope_ids),
            tainted=bool(settlement_id and settlement_id in tainted),
            candidates=_candidates(evidence),
            evidence=evidence,
            explanation=row["narrative"],
            next_action=row["suggested_action"],
            guidance=[line.removeprefix("- ") for line in row["guidance"].splitlines() if line],
        ))
    return views


def _display_members(members: list[dict[str, Any]], traced_txn_id: str,
                     max_rows: int = 12) -> list[dict[str, Any]]:
    """A readable member list that still adds up to the batch total.

    A real settlement batch can hold a hundred payments, which makes an
    exhaustive table useless to read and easy to skim past. Show the payment
    being traced and every signed non-payment member -- the refunds,
    chargebacks and adjustments that make the arithmetic interesting -- then
    fold the remaining ordinary payments into one explicit aggregate row, so
    the displayed rows still sum to the batch amount exactly.
    """
    highlighted: list[dict[str, Any]] = []
    folded: list[dict[str, Any]] = []
    for member in members:
        if member["txn_id"] == traced_txn_id or member["txn_type"] != "PAYMENT":
            highlighted.append({**member, "traced": member["txn_id"] == traced_txn_id})
        else:
            folded.append(member)
    rows = highlighted[:max_rows]
    overflow = highlighted[max_rows:] + folded
    if overflow:
        total = sum(int(member["net_paise"]) for member in overflow)
        rows.append({
            "txn_id": f"{len(overflow)} further members",
            "txn_type": "AGGREGATE", "net_paise": total,
            "amount": money(total), "traced": False,
        })
    return rows


def _build_trace(conn: sqlite3.Connection) -> EvidenceTrace | None:
    """One complete, verified order-to-bank chain, chosen deterministically."""
    row = conn.execute(
        """SELECT o.order_id, o.amount_paise AS order_amount, t.txn_id, t.gross_paise,
                  t.fee_paise, t.tax_paise, t.net_paise,
                  t.settlement_id, b.amount_paise AS batch_amount, b.processed_at,
                  r.line_id, r.credit_paise, r.value_date, r.narration
           FROM match_members mo
           JOIN raw_orders o ON o.order_id = mo.entity_id
           JOIN match_members mt ON mt.match_id = mo.match_id AND mt.entity_type='TXN'
           JOIN raw_txns t ON t.txn_id = mt.entity_id
           JOIN settlement_batches b ON b.settlement_id = t.settlement_id
           JOIN match_members mb ON mb.entity_id = b.settlement_id AND mb.entity_type='BATCH'
           JOIN match_members ml ON ml.match_id = mb.match_id AND ml.entity_type='BANK_LINE'
           JOIN raw_bank r ON r.line_id = ml.entity_id
           WHERE mo.plane='A' AND mo.entity_type='ORDER'
           -- Prefer a batch whose arithmetic is actually interesting: the most
           -- signed non-payment members (refunds, chargebacks, adjustments).
           ORDER BY (SELECT count(*) FROM raw_txns x
                     WHERE x.settlement_id = b.settlement_id
                       AND x.txn_type <> 'PAYMENT') DESC,
                    b.member_count ASC, o.order_id LIMIT 1"""
    ).fetchone()
    if row is None:
        return None
    members = [dict(item) for item in conn.execute(
        "SELECT txn_id,txn_type,net_paise FROM raw_txns WHERE settlement_id=? ORDER BY txn_id",
        (row["settlement_id"],),
    )]
    for member in members:
        member["amount"] = money(int(member["net_paise"]))
    member_total = sum(int(member["net_paise"]) for member in members)
    difference = int(row["credit_paise"]) - int(row["batch_amount"])
    return EvidenceTrace(
        order_id=row["order_id"],
        display_members=_display_members(members, row["txn_id"]),
        member_count=len(members),
        steps=[
            TraceStep("Order", row["order_id"], int(row["order_amount"]), "merchant order"),
            TraceStep("Gateway payment", row["txn_id"], int(row["gross_paise"]),
                      f"captured at the gateway · settles net "
                      f"{money(int(row['net_paise']))} after "
                      f"{money(int(row['fee_paise']) + int(row['tax_paise']))} fee and GST"),
            TraceStep("Settlement batch", row["settlement_id"], int(row["batch_amount"]),
                      f"{len(members)} signed net members, processed "
                      f"{row['processed_at'][:10]}"),
            TraceStep("Bank credit", row["line_id"], int(row["credit_paise"]),
                      f"value date {row['value_date']} · {row['narration']}"),
        ],
        members=members,
        member_total_paise=member_total,
        bank_credit_paise=int(row["credit_paise"]),
        difference_paise=difference,
        status="VERIFIED BANKED" if difference == 0 else "DIFFERENCE PRESENT",
    )


def build_view(run_dir: Path, database_path: Path) -> View:
    metrics_path = run_dir / "functional_metrics.json"
    if not metrics_path.is_file():
        raise GateNotPassed(
            f"{metrics_path} does not exist; run `milaan eval` (a failed gate publishes nothing)"
        )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if metrics.get("gate", {}).get("status") != "PASS":
        raise GateNotPassed("this run did not pass its evaluation gate; its numbers are not quotable")
    telemetry_path = run_dir / "runtime_telemetry.json"
    telemetry = (json.loads(telemetry_path.read_text(encoding="utf-8"))
                 if telemetry_path.exists() else {})

    conn = connect(database_path)
    try:
        run = dict(conn.execute("SELECT * FROM runs").fetchone())
        batches = {row["settlement_id"]: int(row["amount_paise"])
                   for row in conn.execute("SELECT settlement_id,amount_paise FROM settlement_batches")}
        tainted = {row["settlement_id"] for row in
                   conn.execute("SELECT settlement_id FROM settlement_batches WHERE tainted=1")}
        bank = {row["line_id"]: int(row["credit_paise"])
                for row in conn.execute("SELECT line_id,credit_paise FROM raw_bank")}
        orders = {row["order_id"]: int(row["amount_paise"])
                  for row in conn.execute("SELECT order_id,amount_paise FROM raw_orders")}
        matched = {row["entity_id"] for row in
                   conn.execute("SELECT entity_id FROM match_members")}
        tiers = [
            {"plane": PLANE_LABELS[row["plane"]], "tier": TIER_LABELS.get(row["tier"], row["tier"]),
             "count": int(row["n"])}
            for row in conn.execute(
                "SELECT plane,tier,count(*) AS n FROM matches GROUP BY plane,tier ORDER BY plane,tier"
            )
        ]
        exceptions = _build_exceptions(conn, batches, bank, orders, tainted, matched)
        trace = _build_trace(conn)
    finally:
        conn.close()

    conservation = metrics["source_record_conservation"]
    cash = metrics["cash_position"]
    coverage = metrics["workload_coverage"]
    integrity = metrics["truth_integrity"]

    blocked = sorted(
        (
            {"settlement_id": item.settlement_id, "reason_code": item.reason_code,
             "exception_id": item.exception_id, "amount_paise": item.exposure_paise,
             "amount": item.exposure, "tainted": item.tainted}
            for item in exceptions
            if item.settlement_id and item.reason_code != "MISSING_IN_BANK" and item.blocked
        ),
        key=lambda row: (-int(row["amount_paise"]), str(row["settlement_id"])),
    )
    unexplained = sorted(
        (
            {"line_id": line_id, "amount_paise": amount, "amount": money(amount)}
            for line_id, amount in bank.items() if line_id not in matched
        ),
        key=lambda row: (-int(row["amount_paise"]), str(row["line_id"])),
    )

    return View(
        metrics=metrics,
        telemetry=telemetry,
        run=run,
        headline={
            "orders": len(orders),
            "eligible_orders": coverage["plane_a_orders"]["denominator"],
            "bank_lines": len(bank),
            "settlement_batches": len(batches),
            "physical_source_records": conservation["total_source_records"],
            "seed": metrics["seed"],
            "profile": metrics["profile"],
            "generator_version": metrics["generator_version"],
            "git_sha": run.get("git_sha") or "unavailable",
            "loop": "Orders → Gateway payments → Settlement batches → Bank credits → Cash & exceptions",
        },
        accuracy=[
            {
                "label": PLANE_LABELS[plane],
                "expected": metrics["planes"][plane]["expected_count"],
                "produced": metrics["planes"][plane]["found_count"],
                "correct": metrics["planes"][plane]["true_match_count"],
                "false_matches": metrics["planes"][plane]["false_match_count"],
                "precision": _ratio_row("Precision", metrics["planes"][plane]["match_precision"]),
                "recall": _ratio_row("Recall", metrics["planes"][plane]["expected_match_recall"]),
            }
            for plane in ("A", "B")
        ],
        coverage=[
            _ratio_row("Eligible orders auto-matched", coverage["plane_a_orders"]),
            _ratio_row("Gateway payments auto-matched", coverage["plane_a_payments"]),
            _ratio_row("Settlement batches banked", coverage["plane_b_settlement_batches"]),
            _ratio_row("Bank lines explained by a match", coverage["plane_b_bank_lines"]),
        ],
        cash=[
            _money_row("Verified banked", cash["banked_paise"], "matched settlement → bank credit"),
            _money_row("Expected but unbanked", cash["expected_unbanked_paise"],
                       "settled at the gateway, no bank evidence yet"),
            _money_row("Blocked settlements", cash["blocked_settlement_paise"],
                       "a control stopped this cash"),
            _money_row("Unexplained bank credits", cash["unexplained_bank_credit_paise"],
                       "money in the bank Milaan cannot attribute"),
            _money_row("Gross evidence under attention", cash["gross_attention_paise"],
                       ATTENTION_DISCLAIMER),
            _money_row("Total bank credits", cash["total_bank_credit_paise"], "statement total"),
            _money_row("Total settlement control", cash["total_settlement_control_paise"],
                       "matched + unbanked + blocked"),
            _money_row("Matched amount delta", cash["matched_amount_delta_paise"],
                       "bank credited minus settlement claimed, across matches"),
        ],
        exception_quality={
            "precision": _ratio_row("Exception precision", metrics["exceptions"]["precision"]),
            "recall": _ratio_row("Exception recall", metrics["exceptions"]["recall"]),
            "expected": metrics["exceptions"]["expected_count"],
            "detected": metrics["exceptions"]["actual_count"],
            "correct": metrics["exceptions"]["correct_count"],
            "false": metrics["exceptions"]["false_count"],
            "missed": metrics["exceptions"]["missed_count"],
        },
        exceptions=exceptions,
        integrity=[
            {"label": "Truth integrity", "status": integrity["status"],
             "detail": "benchmark truth regenerated independently from run_meta.json"},
            {"label": "Input integrity",
             "status": "PASS" if integrity["inputs"]["all_match"] else "FAIL",
             "detail": "evaluated CSV files are byte-identical to the canonical dataset"},
            {"label": "Database binding",
             "status": "PASS" if integrity["database"]["all_match"] else "FAIL",
             "detail": "every stored source row is the canonical row"},
            {"label": "Shipped manifest",
             "status": "PASS" if integrity["run_manifest"]["matches_canonical"] else "FAIL",
             "detail": "the answer key in the run directory matches reconstructed truth"},
            {"label": "Source-record conservation",
             "status": f"{conservation['rate']:.2%}",
             "detail": f"{conservation['numerator']:,} of {conservation['denominator']:,} "
                       f"records reached a terminal state: "
                       f"{json.dumps(conservation['terminal_buckets'])}"},
            {"label": "Settlement amount conservation",
             "status": "PASS" if metrics["amount_conservation"]["balanced"] else "FAIL",
             "detail": f"{metrics['amount_conservation']['delta_paise']} paise delta between "
                       "signed member sums and batch totals"},
            {"label": "False matches", "status": str(metrics["false_match_count"]),
             "detail": "accepted matches that disagree with independently rebuilt truth"},
            {"label": "Cash classification",
             "status": f"{cash['classification']['settlements']['rate']:.2%}",
             "detail": f"every settlement lands in exactly one cash bucket "
                       f"({json.dumps(cash['classification']['settlement_buckets'])}); "
                       f"bank lines "
                       f"{json.dumps(cash['classification']['bank_line_buckets'])}"},
        ],
        throughput={
            "records": int(telemetry.get("source_records", 0)),
            "wall_ms": int(telemetry.get("wall_ms", 0)),
            "records_per_second": float(telemetry.get("source_records_per_second", 0)),
            "stage_ms": telemetry.get("stage_ms", {}),
            "scope": "reconciliation engine only; generation and evaluation excluded",
        },
        tiers=tiers,
        trace=trace,
        blocked_settlements=blocked,
        unexplained_credits=unexplained,
    )
