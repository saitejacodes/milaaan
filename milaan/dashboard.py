"""Optional Streamlit operator console for a completed Milaan run.

Reads the same :mod:`milaan.view` model the HTML report reads, so the two
surfaces cannot drift apart. No financial value is computed in this file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from milaan.agent.router import ask_finance
from milaan.db import connect
from milaan.view import (
    ATTENTION_DISCLAIMER, COVERAGE_DISCLAIMER, GateNotPassed, View, build_view, money,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run", dest="run_dir", type=Path, default=Path("data/run42"))
    parser.add_argument("--db", type=Path, default=Path("data/run42/milaan.db"))
    return parser.parse_known_args()[0]


def _first_screen(st, view: View) -> None:
    """What a judge must understand in thirty seconds."""
    head = view.headline
    st.title("Milaan · AI Finance Controller")
    st.caption("Deterministic accounting controls · bounded read-only AI investigation")
    st.markdown(f"### {head['loop']}")

    top = st.columns(5)
    top[0].metric("Physical source records", f"{head['physical_source_records']:,}")
    top[1].metric("Orders", f"{head['orders']:,}", f"{head['eligible_orders']:,} eligible")
    top[2].metric("Settlement batches", f"{head['settlement_batches']:,}")
    top[3].metric("Bank lines", f"{head['bank_lines']:,}")
    top[4].metric("False matches", view.metrics["false_match_count"])

    cash = {row["label"]: row for row in view.cash}
    second = st.columns(5)
    second[0].metric("Verified banked", cash["Verified banked"]["amount"])
    second[1].metric("Expected unbanked", cash["Expected but unbanked"]["amount"])
    second[2].metric("Blocked settlements", cash["Blocked settlements"]["amount"])
    second[3].metric("Unexplained credits", cash["Unexplained bank credits"]["amount"])
    second[4].metric("Open exceptions", len(view.exceptions))

    third = st.columns(4)
    for column, row in zip(third, view.coverage):
        column.metric(row["label"], row["percent"], row["fraction"])
    st.warning(f"**{COVERAGE_DISCLAIMER}** Accuracy and coverage are reported separately.")


def _scorecard(st, view: View) -> None:
    st.subheader("Measured accuracy")
    st.caption("Scored against ground truth the evaluator regenerates itself from the "
               "run's immutable generation inputs.")
    st.dataframe([
        {
            "Loop": plane["label"], "Expected": plane["expected"],
            "Produced": plane["produced"], "Correct": plane["correct"],
            "False": plane["false_matches"],
            "Precision": plane["precision"]["percent"],
            "Recall": plane["recall"]["percent"],
        }
        for plane in view.accuracy
    ], use_container_width=True, hide_index=True)

    quality = view.exception_quality
    st.subheader("Exception quality")
    st.dataframe([{
        "Expected": quality["expected"], "Detected": quality["detected"],
        "Correct": quality["correct"], "False": quality["false"],
        "Missed": quality["missed"],
        "Precision": quality["precision"]["percent"],
        "Recall": quality["recall"]["percent"],
    }], use_container_width=True, hide_index=True)

    st.subheader("Cash position")
    st.dataframe([{"Bucket": row["label"], "Amount": row["amount"], "Meaning": row["note"]}
                  for row in view.cash], use_container_width=True, hide_index=True)
    st.info(ATTENTION_DISCLAIMER)

    st.subheader("Which rule produced each match")
    st.dataframe(view.tiers, use_container_width=True, hide_index=True)

    st.subheader("Throughput")
    st.metric(f"{view.throughput['records_per_second']:,.0f} source records/s",
              f"{view.throughput['records']:,} records in {view.throughput['wall_ms']:,} ms")
    st.caption(view.throughput["scope"])


def _exceptions(st, view: View) -> None:
    st.subheader(f"Exceptions · {len(view.exceptions)}")
    st.caption("Every unresolved record, with the money it affects and the next action.")
    ordered = sorted(view.exceptions, key=lambda item: (-item.exposure_paise, item.exception_id))
    st.dataframe([
        {
            "Exception": item.exception_id, "What failed": item.headline,
            "Money affected": item.exposure, "Reason code": item.reason_code,
            "Blocked": item.blocked, "Tainted": item.tainted,
            "Next action": item.next_action,
        }
        for item in ordered
    ], use_container_width=True, hide_index=True)

    for item in ordered:
        with st.expander(f"{item.exception_id} · {item.headline} · {item.exposure}"):
            st.write(f"**Why Milaan abstained.** {item.explanation}")
            st.write(f"**Affected records.** {', '.join(item.scope_ids)}")
            if item.candidates:
                st.write(f"**Candidates considered.** {', '.join(item.candidates[:10])}")
            if item.source_row_ids:
                st.write(f"**Source rows.** {', '.join(item.source_row_ids[:10])}")
            st.write(f"**What finance should do next.** {item.next_action}")
            for line in item.guidance:
                st.write(f"- {line}")
            st.json(item.evidence, expanded=False)


def _evidence(st, view: View, run_dir: Path, database: Path) -> None:
    trace = view.trace
    if trace is not None:
        st.subheader("Verified evidence chain")
        for index, step in enumerate(trace.steps):
            st.markdown(
                f"**{step.label}** · `{step.entity_id}` · "
                f"**{money(step.amount_paise or 0)}**  \n{step.detail}"
            )
            if index < len(trace.steps) - 1:
                st.markdown("↓")
        st.caption(
            f"Members settle at net (gross minus fee and GST). {trace.member_count} members "
            "in total; the traced payment and every signed non-payment member are listed, "
            "with the remaining payments folded into one aggregate row that keeps the sum exact."
        )
        st.dataframe(trace.display_members, use_container_width=True, hide_index=True)
        columns = st.columns(3)
        columns[0].metric("Σ signed members", money(trace.member_total_paise))
        columns[1].metric("Bank credit", money(trace.bank_credit_paise))
        columns[2].metric("Difference", money(trace.difference_paise))
        st.success(trace.status) if trace.difference_paise == 0 else st.error(trace.status)

    st.subheader("Trace any record")
    with connect(database) as conn:
        order_ids = [row[0] for row in conn.execute(
            "SELECT order_id FROM raw_orders ORDER BY order_id")]
        settlement_ids = [row[0] for row in conn.execute(
            "SELECT settlement_id FROM settlement_batches ORDER BY settlement_id")]
    kind = st.radio("Trace", ["Order", "Settlement"], horizontal=True)
    choices = order_ids if kind == "Order" else settlement_ids
    selected = st.selectbox(f"{kind} identifier", choices)
    if selected:
        result = ask_finance(run_dir, database, f"Trace {selected}", "mock")
        st.success(result["answer"])
        st.caption("Evidence: " + ", ".join(result["evidence_ids"][:20]))
        st.json(result["facts"], expanded=False)


def _ask(st, view: View, run_dir: Path, database: Path) -> None:
    st.write("The model may select one allow-listed read-only tool. Deterministic code "
             "produces the answer and the evidence. Requests to change accounting state "
             "are refused before any model is consulted.")
    mode = st.radio("Router", ["mock", "live"], horizontal=True,
                    help="Live requires your own provider environment variables.")
    suggestions = [
        "What is our cash position?", "Why is cash blocked?",
        "Summarise the exception queue", "Show unexplained bank credits",
        "Delete all exceptions.",
    ]
    question = st.selectbox("Try one", suggestions)
    question = st.text_input("Question", value=question)
    if st.button("Ask", type="primary"):
        result = ask_finance(run_dir, database, question, mode)
        if result["status"] == "ok":
            st.success(result["answer"])
        else:
            st.warning(result["answer"])
        st.caption("Routing: " + json.dumps(result["routing"], sort_keys=True))
        st.write("Evidence", result["evidence_ids"])
        st.json(result["facts"], expanded=False)


def _integrity(st, view: View) -> None:
    st.subheader("Control integrity")
    st.dataframe([
        {"Control": row["label"], "Result": row["status"], "What it proves": row["detail"]}
        for row in view.integrity
    ], use_container_width=True, hide_index=True)
    st.caption(
        "Benchmark truth is not read from the run directory. The evaluator regenerates "
        "it from the run's immutable generation inputs and refuses to score a run whose "
        "data, database or shipped answer key does not match."
    )
    st.json({"truth_integrity": view.metrics["truth_integrity"],
             "input_hashes": view.metrics["input_hashes"],
             "gate": view.metrics["gate"]}, expanded=False)


def main() -> None:
    try:
        import streamlit as st
    except ImportError as exc:
        raise SystemExit(
            "Install the dashboard extra: python -m pip install -e '.[dashboard]'"
        ) from exc

    args = _arguments()
    st.set_page_config(page_title="Milaan · AI Finance Controller", page_icon="₹",
                       layout="wide")
    try:
        view = build_view(args.run_dir, args.db)
    except (GateNotPassed, FileNotFoundError, OSError) as exc:
        st.error(f"No verified run available: {exc}")
        st.info("Run `make judge` (or `make demo`) first, then point the dashboard at it.")
        st.stop()
        return

    _first_screen(st, view)
    scorecard, exceptions, evidence, ask, integrity = st.tabs(
        ["Scorecard", "Exceptions", "Evidence", "Ask Milaan", "Integrity"]
    )
    with scorecard:
        _scorecard(st, view)
    with exceptions:
        _exceptions(st, view)
    with evidence:
        _evidence(st, view, args.run_dir, args.db)
    with ask:
        _ask(st, view, args.run_dir, args.db)
    with integrity:
        _integrity(st, view)


if __name__ == "__main__":
    main()
