"""Optional Streamlit operator console for a completed Milaan run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from milaan.agent.router import ask_finance
from milaan.db import connect


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run", dest="run_dir", type=Path, default=Path("data/run42"))
    parser.add_argument("--db", type=Path, default=Path("data/run42/milaan.db"))
    return parser.parse_known_args()[0]


def _money(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def main() -> None:
    try:
        import streamlit as st
    except ImportError as exc:
        raise SystemExit("Install the dashboard extra: python -m pip install -e '.[dashboard]'") from exc

    args = _arguments()
    st.set_page_config(page_title="Milaan Finance Controller", page_icon="₹", layout="wide")
    st.title("Milaan · AI Finance Controller")
    st.caption("Deterministic accounting controls · bounded read-only AI investigation")
    metrics_path = args.run_dir / "functional_metrics.json"
    if not metrics_path.exists() or not args.db.exists():
        st.error("Completed run not found. Run `make demo` first.")
        st.stop()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    cash = metrics["cash_position"]
    coverage = metrics["workload_coverage"]

    first = st.columns(5)
    first[0].metric("Banked", _money(cash["banked_paise"]))
    first[1].metric("Expected unbanked", _money(cash["expected_unbanked_paise"]))
    first[2].metric("Blocked", _money(cash["blocked_settlement_paise"]))
    first[3].metric("Unexplained bank", _money(cash["unexplained_bank_credit_paise"]))
    first[4].metric("False matches", metrics["false_match_count"])

    overview, exceptions_tab, evidence_tab, ask_tab, audit_tab = st.tabs(
        ["Scorecard", "Exceptions", "Evidence", "Ask Milaan", "Audit"]
    )
    with overview:
        st.subheader("Honest workload coverage")
        cols = st.columns(4)
        cols[0].metric("Eligible orders", f"{coverage['plane_a_orders']['rate']:.2%}")
        cols[1].metric("Gateway payments", f"{coverage['plane_a_payments']['rate']:.2%}")
        cols[2].metric("Settlement batches", f"{coverage['plane_b_settlement_batches']['rate']:.2%}")
        cols[3].metric("Bank lines", f"{coverage['plane_b_bank_lines']['rate']:.2%}")
        st.info("Expected-match accuracy and operational coverage are intentionally reported separately.")
        st.json({"expected_match_accuracy": metrics["planes"],
                 "exception_quality": metrics["exceptions"]}, expanded=False)

    with connect(args.db) as conn:
        exception_rows = [dict(row) for row in conn.execute(
            "SELECT id,reason_code,scope_ids,confidence,suggested_action FROM exceptions ORDER BY id"
        )]
        order_ids = [row[0] for row in conn.execute("SELECT order_id FROM raw_orders ORDER BY order_id")]
        settlement_ids = [row[0] for row in conn.execute(
            "SELECT settlement_id FROM settlement_batches ORDER BY settlement_id"
        )]

    with exceptions_tab:
        st.subheader(f"Exceptions · {len(exception_rows)}")
        display = [{**row, "id": f"EXC-{int(row['id']):04d}"} for row in exception_rows]
        st.dataframe(display, use_container_width=True, hide_index=True)

    with evidence_tab:
        kind = st.radio("Trace", ["Order", "Settlement"], horizontal=True)
        choices = order_ids if kind == "Order" else settlement_ids
        selected = st.selectbox(f"{kind} identifier", choices)
        if selected:
            question = f"Trace {selected}"
            result = ask_finance(args.run_dir, args.db, question, "mock")
            st.success(result["answer"])
            st.caption("Evidence: " + ", ".join(result["evidence_ids"]))
            st.json(result["facts"], expanded=False)

    with ask_tab:
        st.write("The model may select one read-only tool. Deterministic code produces the answer and evidence.")
        mode = st.radio("Router", ["mock", "live"], horizontal=True,
                        help="Live requires your private provider environment variables.")
        question = st.text_input("Question", value="Why is cash blocked?")
        if st.button("Ask", type="primary"):
            result = ask_finance(args.run_dir, args.db, question, mode)
            if result["status"] == "ok":
                st.success(result["answer"])
            else:
                st.warning(result["answer"])
            st.caption("Routing: " + json.dumps(result["routing"], sort_keys=True))
            st.write("Evidence", result["evidence_ids"])
            st.json(result["facts"], expanded=False)

    with audit_tab:
        status = "PASS" if metrics["input_integrity"]["all_match"] else "FAIL"
        st.metric("Manifest ↔ input hashes", status)
        st.metric("Source-record conservation", f"{metrics['source_record_conservation']['rate']:.2%}")
        st.metric("Settlement amount delta", f"{metrics['amount_conservation']['delta_paise']} paise")
        st.json({"input_integrity": metrics["input_integrity"],
                 "source_record_conservation": metrics["source_record_conservation"],
                 "amount_conservation": metrics["amount_conservation"]})


if __name__ == "__main__":
    main()
