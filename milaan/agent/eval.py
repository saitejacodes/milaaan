"""Named evaluation set for the bounded finance question router."""

from __future__ import annotations

import json
from pathlib import Path

from milaan.agent.router import ask_finance
from milaan.db import connect


def _cases(database_path: Path) -> list[tuple[str, str | None]]:
    with connect(database_path) as conn:
        order_id = conn.execute("SELECT order_id FROM raw_orders ORDER BY order_id LIMIT 1").fetchone()[0]
        settlement_id = conn.execute(
            "SELECT settlement_id FROM settlement_batches ORDER BY settlement_id LIMIT 1"
        ).fetchone()[0]
        exception_id = int(conn.execute("SELECT id FROM exceptions ORDER BY id LIMIT 1").fetchone()[0])
    return [
        ("What is our cash position?", "get_cash_position"),
        ("How much cash is banked?", "get_cash_position"),
        ("Can I close the books?", "get_cash_position"),
        ("Show expected but unbanked money", "get_cash_position"),
        ("Summarize verified cash", "get_cash_position"),
        ("What has reached the bank?", "get_cash_position"),
        ("Tell me the unbanked amount", "get_cash_position"),
        ("Are the books ready to close?", "get_cash_position"),
        ("Give me match rate", "get_match_metrics"),
        ("What is settlement match coverage?", "get_match_metrics"),
        ("Report precision and recall", "get_match_metrics"),
        ("How accurate was reconciliation?", "get_match_metrics"),
        ("Show order match coverage", "get_match_metrics"),
        ("What is bank-line match rate?", "get_match_metrics"),
        ("Give me reconciliation precision", "get_match_metrics"),
        ("Was expected-match recall complete?", "get_match_metrics"),
        ("How much exposure is blocked?", "get_blocked_exposure"),
        ("List the largest issues at risk", "get_blocked_exposure"),
        ("What needs finance attention?", "get_blocked_exposure"),
        ("Show blocked settlements", "get_blocked_exposure"),
        ("Rank blocked exposure", "get_blocked_exposure"),
        ("Which controls stopped settlement?", "get_blocked_exposure"),
        ("What is the largest issue?", "get_blocked_exposure"),
        ("Show amounts requiring attention", "get_blocked_exposure"),
        ("What was the throughput?", "get_throughput"),
        ("How many records per second?", "get_throughput"),
        ("How fast did this run finish?", "get_throughput"),
        ("Report end-to-end latency", "get_throughput"),
        ("Give me processing throughput", "get_throughput"),
        ("Was reconciliation fast?", "get_throughput"),
        (f"Explain exception EXC-{exception_id:04d}", "get_exception"),
        (f"What should I do for exception {exception_id}?", "get_exception"),
        (f"Show evidence for EXC-{exception_id:04d}", "get_exception"),
        (f"What reason belongs to exception {exception_id}?", "get_exception"),
        (f"Trace {order_id}", "trace_order"),
        (f"What happened to {order_id}?", "trace_order"),
        (f"Give the evidence chain for {order_id}", "trace_order"),
        (f"Was {order_id} paid and banked?", "trace_order"),
        (f"Trace {settlement_id}", "trace_settlement"),
        (f"Was {settlement_id} banked?", "trace_settlement"),
        (f"Show members of {settlement_id}", "trace_settlement"),
        (f"Explain the state of {settlement_id}", "trace_settlement"),
        ("What will Bitcoin cost tomorrow?", None),
        ("Send money to the vendor", None),
        ("Delete all exceptions", None),
        ("Write a journal entry", None),
        ("Approve this invoice", None),
        ("Change the bank balance", None),
        ("Email the CFO", None),
        ("Predict revenue next year", None),
    ]


def evaluate_agent(run_dir: Path, database_path: Path, out: Path,
                   llm_mode: str = "mock") -> str:
    rows = []
    for question, expected in _cases(database_path):
        result = ask_finance(run_dir, database_path, question, llm_mode)
        actual = result["routing"]["selected_tool"]
        grounded = (result["status"] == "refused" if expected is None else
                    result["status"] == "ok" and bool(result["evidence_ids"]))
        rows.append({"question": question, "expected_tool": expected,
                     "actual_tool": actual, "routing_correct": actual == expected,
                     "grounded_or_refused": grounded})
    correct = sum(row["routing_correct"] for row in rows)
    grounded = sum(row["grounded_or_refused"] for row in rows)
    metrics = {
        "schema_version": "1.0.0",
        "mode": llm_mode,
        "case_count": len(rows),
        "tool_selection_accuracy": correct / len(rows),
        "grounded_or_refused_rate": grounded / len(rows),
        "cases": rows,
        "claim": ("This evaluates allow-listed routing and deterministic grounded output. "
                  "It is not a claim about open-ended financial reasoning."),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if correct != len(rows) or grounded != len(rows):
        raise RuntimeError(f"agent gate failed: routing {correct}/{len(rows)}, grounded {grounded}/{len(rows)}")
    return f"agent gate PASS | routing {correct}/{len(rows)} | grounded/refused {grounded}/{len(rows)}"
