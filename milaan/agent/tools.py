"""Deterministic read-only tools exposed to the finance question router."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from milaan.db import connect


def money(paise: int) -> str:
    """Format signed integer paise without ever going through a float.

    ``paise / 100`` is exact for every amount this system will see, but money
    formatting is not the place to rely on that. Integer division has no
    magnitude at which it starts rounding.
    """
    sign = "-" if paise < 0 else ""
    whole, fraction = divmod(abs(int(paise)), 100)
    return f"{sign}₹{whole:,}.{fraction:02d}"


class FinanceTools:
    """Small allow-listed query surface; no method can create a match or posting."""

    def __init__(self, run_dir: Path, database_path: Path) -> None:
        self.run_dir = run_dir
        self.database_path = database_path
        self.metrics = json.loads((run_dir / "functional_metrics.json").read_text(encoding="utf-8"))
        gate = self.metrics.get("gate", {})
        if gate.get("status") != "PASS":
            # Only evidence from a run that passed its own controls is quotable.
            raise ValueError(
                "functional_metrics.json did not pass its evaluation gate; "
                "re-run `milaan eval` before asking questions about this run"
            )
        telemetry_path = run_dir / "runtime_telemetry.json"
        self.telemetry = (json.loads(telemetry_path.read_text(encoding="utf-8"))
                          if telemetry_path.exists() else {})

    def _connection(self) -> sqlite3.Connection:
        return connect(self.database_path)

    @staticmethod
    def _exception_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        return [
            {**dict(row), "scope_ids": json.loads(row["scope_ids"]),
             "evidence": json.loads(row["evidence"])}
            for row in conn.execute("SELECT * FROM exceptions ORDER BY id")
        ]

    def get_cash_position(self) -> dict[str, Any]:
        cash = self.metrics["cash_position"]
        with self._connection() as conn:
            evidence_ids = [row[0] for row in conn.execute(
                """SELECT entity_id FROM match_members
                   WHERE plane='B' AND entity_type IN ('BATCH','BANK_LINE')
                   ORDER BY match_id,entity_type LIMIT 12"""
            )]
        answer = (
            f"Verified banked cash is {money(cash['banked_paise'])}. "
            f"Expected but unbanked settlements total {money(cash['expected_unbanked_paise'])}; "
            f"blocked settlements total {money(cash['blocked_settlement_paise'])}; "
            f"unexplained bank credits total {money(cash['unexplained_bank_credit_paise'])}."
        )
        return {"status": "ok", "tool": "get_cash_position", "answer": answer,
                "facts": cash, "evidence_ids": evidence_ids}

    def get_match_metrics(self) -> dict[str, Any]:
        coverage = self.metrics["workload_coverage"]
        planes = self.metrics["planes"]
        answer = (
            f"Plane A workload coverage is {coverage['plane_a_orders']['rate']:.2%} "
            f"with {planes['A']['match_precision']['rate']:.2%} precision. "
            f"Plane B settlement-batch coverage is "
            f"{coverage['plane_b_settlement_batches']['rate']:.2%} with "
            f"{planes['B']['match_precision']['rate']:.2%} precision. "
            f"False matches: {self.metrics['false_match_count']}."
        )
        facts = {"planes": planes, "workload_coverage": coverage,
                 "exceptions": self.metrics["exceptions"],
                 "source_record_conservation": self.metrics["source_record_conservation"]}
        return {"status": "ok", "tool": "get_match_metrics", "answer": answer,
                "facts": facts, "evidence_ids": ["functional_metrics.json"]}

    def get_blocked_exposure(self) -> dict[str, Any]:
        with self._connection() as conn:
            matched = {row[0] for row in conn.execute(
                "SELECT entity_id FROM match_members WHERE plane='B' AND entity_type='BATCH'"
            )}
            batches = {row["settlement_id"]: int(row["amount_paise"])
                       for row in conn.execute("SELECT settlement_id,amount_paise FROM settlement_batches")}
            rows = self._exception_rows(conn)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if row["reason_code"] == "MISSING_IN_BANK":
                continue
            for entity_id in row["scope_ids"]:
                if entity_id not in batches or entity_id in matched or entity_id in seen:
                    continue
                seen.add(entity_id)
                amount = batches[entity_id]
                if "claimed_exposure_paise" in row["evidence"]:
                    amount = int(row["evidence"]["claimed_exposure_paise"])
                items.append({"settlement_id": entity_id, "reason": row["reason_code"],
                              "amount_paise": amount, "amount": money(amount),
                              "exception_id": f"EXC-{int(row['id']):04d}"})
        items.sort(key=lambda item: (-int(item["amount_paise"]), str(item["settlement_id"])))
        total = sum(int(item["amount_paise"]) for item in items)
        answer = (f"{len(items)} settlement batches are blocked by control exceptions, "
                  f"representing {money(total)} of gross settlement evidence. "
                  "Expected-but-unbanked items are reported separately in cash position.")
        evidence = [str(item["settlement_id"]) for item in items[:10]]
        return {"status": "ok", "tool": "get_blocked_exposure", "answer": answer,
                "facts": {"total_paise": total, "items": items}, "evidence_ids": evidence}

    def get_exception(self, exception_id: str) -> dict[str, Any]:
        raw = exception_id.strip().upper().removeprefix("EXC-")
        if not raw.isdigit():
            return self.refusal("Use an exception identifier such as EXC-0001.")
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM exceptions WHERE id=?", (int(raw),)).fetchone()
        if row is None:
            return self.refusal(f"No verified exception exists for {exception_id}.")
        scope_ids = json.loads(row["scope_ids"])
        evidence = json.loads(row["evidence"])
        canonical_id = f"EXC-{int(row['id']):04d}"
        answer = (f"{canonical_id} is {row['reason_code']}. {row['narrative']} "
                  f"Next action: {row['suggested_action']}")
        return {"status": "ok", "tool": "get_exception", "answer": answer,
                "facts": {"exception_id": canonical_id, "reason_code": row["reason_code"],
                          "scope_ids": scope_ids, "evidence": evidence,
                          "guidance": row["guidance"],
                          "suggested_action": row["suggested_action"]},
                "evidence_ids": [canonical_id, *scope_ids]}

    def trace_order(self, order_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            order = conn.execute("SELECT * FROM raw_orders WHERE order_id=?", (order_id,)).fetchone()
            if order is None:
                return self.refusal(f"No verified order exists for {order_id}.")
            match = conn.execute(
                """SELECT m.match_id FROM matches m JOIN match_members mm ON mm.match_id=m.match_id
                   WHERE m.plane='A' AND mm.entity_type='ORDER' AND mm.entity_id=?""",
                (order_id,),
            ).fetchone()
            payment = None
            settlement_id = None
            bank_line = None
            if match:
                payment_member = conn.execute(
                    "SELECT entity_id FROM match_members WHERE match_id=? AND entity_type='TXN'",
                    (match["match_id"],),
                ).fetchone()
                payment = conn.execute("SELECT * FROM raw_txns WHERE txn_id=?",
                                       (payment_member["entity_id"],)).fetchone()
                settlement_id = payment["settlement_id"] if payment else None
            if settlement_id:
                bank_member = conn.execute(
                    """SELECT bank.entity_id FROM match_members batch
                       JOIN match_members bank ON bank.match_id=batch.match_id
                       WHERE batch.plane='B' AND batch.entity_type='BATCH' AND batch.entity_id=?
                         AND bank.entity_type='BANK_LINE'""", (settlement_id,),
                ).fetchone()
                if bank_member:
                    bank_line = conn.execute("SELECT * FROM raw_bank WHERE line_id=?",
                                             (bank_member["entity_id"],)).fetchone()
            exceptions = [row for row in self._exception_rows(conn)
                          if order_id in row["scope_ids"]
                          or (payment and payment["txn_id"] in row["scope_ids"])
                          or (settlement_id and settlement_id in row["scope_ids"])]
        facts = {
            "order": dict(order),
            "payment": dict(payment) if payment else None,
            "settlement_id": settlement_id,
            "bank_line": dict(bank_line) if bank_line else None,
            "exceptions": [{"exception_id": f"EXC-{int(row['id']):04d}",
                            "reason_code": row["reason_code"]} for row in exceptions],
        }
        evidence = [order_id]
        if payment:
            evidence.append(payment["txn_id"])
        if settlement_id:
            evidence.append(settlement_id)
        if bank_line:
            evidence.append(bank_line["line_id"])
        state = "banked" if bank_line else "exception" if exceptions else "unresolved"
        return {"status": "ok", "tool": "trace_order",
                "answer": f"{order_id} traces to state {state}; see the verified evidence chain.",
                "facts": facts, "evidence_ids": evidence}

    def trace_settlement(self, settlement_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            batch = conn.execute("SELECT * FROM settlement_batches WHERE settlement_id=?",
                                 (settlement_id,)).fetchone()
            if batch is None:
                return self.refusal(f"No verified settlement exists for {settlement_id}.")
            members = [dict(row) for row in conn.execute(
                "SELECT txn_id,txn_type,net_paise FROM raw_txns WHERE settlement_id=? ORDER BY txn_id",
                (settlement_id,),
            )]
            bank_member = conn.execute(
                """SELECT bank.entity_id FROM match_members batch
                   JOIN match_members bank ON bank.match_id=batch.match_id
                   WHERE batch.plane='B' AND batch.entity_type='BATCH' AND batch.entity_id=?
                     AND bank.entity_type='BANK_LINE'""", (settlement_id,),
            ).fetchone()
            bank_line = (conn.execute("SELECT * FROM raw_bank WHERE line_id=?",
                                      (bank_member["entity_id"],)).fetchone()
                         if bank_member else None)
            exceptions = [row for row in self._exception_rows(conn)
                          if settlement_id in row["scope_ids"]]
        state = "banked" if bank_line else "blocked" if exceptions else "unresolved"
        facts = {"settlement": dict(batch), "members": members,
                 "bank_line": dict(bank_line) if bank_line else None,
                 "exceptions": [{"exception_id": f"EXC-{int(row['id']):04d}",
                                 "reason_code": row["reason_code"]} for row in exceptions]}
        evidence = [settlement_id, *[row["txn_id"] for row in members]]
        if bank_line:
            evidence.append(bank_line["line_id"])
        return {"status": "ok", "tool": "trace_settlement",
                "answer": f"{settlement_id} is {state} for {money(int(batch['amount_paise']))}.",
                "facts": facts, "evidence_ids": evidence}

    def summarize_exception_queue(self) -> dict[str, Any]:
        """Queue shape by reason code: what a finance team must work through."""
        with self._connection() as conn:
            rows = self._exception_rows(conn)
            batches = {row["settlement_id"]: int(row["amount_paise"]) for row in
                       conn.execute("SELECT settlement_id,amount_paise FROM settlement_batches")}
            bank = {row["line_id"]: int(row["credit_paise"]) for row in
                    conn.execute("SELECT line_id,credit_paise FROM raw_bank")}
            orders = {row["order_id"]: int(row["amount_paise"]) for row in
                      conn.execute("SELECT order_id,amount_paise FROM raw_orders")}
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = groups.setdefault(
                row["reason_code"], {"reason_code": row["reason_code"], "count": 0,
                                     "gross_evidence_paise": 0, "exception_ids": []}
            )
            bucket["count"] += 1
            bucket["exception_ids"].append(f"EXC-{int(row['id']):04d}")
            bucket["gross_evidence_paise"] += sum(
                batches.get(entity_id, bank.get(entity_id, orders.get(entity_id, 0)))
                for entity_id in row["scope_ids"]
            )
        items = sorted(groups.values(),
                       key=lambda item: (-int(item["count"]), str(item["reason_code"])))
        for item in items:
            item["gross_evidence"] = money(int(item["gross_evidence_paise"]))
        if rows:
            headline = "; ".join(f"{item['reason_code']} x{item['count']}" for item in items[:6])
            answer = (f"{len(rows)} open exceptions across {len(items)} reason codes: "
                      f"{headline}. Gross evidence is the amount under review, "
                      "not a loss estimate.")
        else:
            answer = "No open exceptions in this verified run."
        return {"status": "ok", "tool": "summarize_exception_queue", "answer": answer,
                "facts": {"total": len(rows), "by_reason_code": items},
                "evidence_ids": [item["exception_ids"][0] for item in items[:10]]}

    def list_unexplained_bank_credits(self) -> dict[str, Any]:
        """Bank money Milaan could not attribute to any settlement batch."""
        with self._connection() as conn:
            matched = {row[0] for row in conn.execute(
                "SELECT entity_id FROM match_members WHERE plane='B' AND entity_type='BANK_LINE'"
            )}
            lines = [dict(row) for row in conn.execute(
                "SELECT line_id,value_date,narration,credit_paise,ref_no FROM raw_bank "
                "ORDER BY credit_paise DESC,line_id"
            )]
        items = [
            {**line, "amount": money(int(line["credit_paise"]))}
            for line in lines if line["line_id"] not in matched
        ]
        total = sum(int(item["credit_paise"]) for item in items)
        answer = (f"{len(items)} bank credits totalling {money(total)} are not explained "
                  "by any verified settlement batch." if items else
                  "Every bank credit in this run is explained by a verified settlement batch.")
        return {"status": "ok", "tool": "list_unexplained_bank_credits", "answer": answer,
                "facts": {"total_paise": total, "items": items},
                "evidence_ids": [str(item["line_id"]) for item in items[:10]]}

    def explain_why_unmatched(self, entity_id: str) -> dict[str, Any]:
        """State, in verified terms, why one record carries no accounting match."""
        with self._connection() as conn:
            kind = None
            for table, column, label in (
                ("raw_orders", "order_id", "order"), ("raw_txns", "txn_id", "gateway_txn"),
                ("settlement_batches", "settlement_id", "settlement"),
                ("raw_bank", "line_id", "bank_line"),
            ):
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE {column}=?", (entity_id,)  # noqa: S608 - fixed table names
                ).fetchone()
                if row is not None:
                    kind, record = label, dict(row)
                    break
            if kind is None:
                return self.refusal(f"No verified record exists for {entity_id}.")
            matched = conn.execute(
                "SELECT plane,entity_type FROM match_members WHERE entity_id=?", (entity_id,)
            ).fetchall()
            exceptions = [row for row in self._exception_rows(conn)
                          if entity_id in row["scope_ids"]]
        planes = sorted({row["plane"] for row in matched})
        if matched and not exceptions:
            answer = (f"{entity_id} is matched on plane {'/'.join(planes)}; "
                      "it is not an unmatched record.")
            state = "matched"
        elif exceptions:
            reasons = ", ".join(sorted({row["reason_code"] for row in exceptions}))
            actions = sorted({row["suggested_action"] for row in exceptions})
            answer = (f"{entity_id} carries no accounting match because Milaan abstained: "
                      f"{reasons}. Next action: {actions[0]}")
            state = "excepted"
        else:
            answer = (f"{entity_id} is neither matched nor excepted in this run; "
                      "treat it as unaccounted and re-run evaluation.")
            state = "unaccounted"
        return {"status": "ok", "tool": "explain_why_unmatched", "answer": answer,
                "facts": {"entity_id": entity_id, "record_kind": kind, "state": state,
                          "record": record, "matched_planes": planes,
                          "exceptions": [{"exception_id": f"EXC-{int(row['id']):04d}",
                                          "reason_code": row["reason_code"],
                                          "evidence": row["evidence"],
                                          "suggested_action": row["suggested_action"]}
                                         for row in exceptions]},
                "evidence_ids": [entity_id, *[f"EXC-{int(row['id']):04d}" for row in exceptions]]}

    def get_throughput(self) -> dict[str, Any]:
        records = int(self.telemetry.get("source_records", 0))
        rate = float(self.telemetry.get("source_records_per_second", 0))
        wall_ms = int(self.telemetry.get("wall_ms", 0))
        answer = f"Milaan processed {records:,} source records in {wall_ms:,} ms ({rate:,.2f} records/s)."
        return {"status": "ok", "tool": "get_throughput", "answer": answer,
                "facts": self.telemetry, "evidence_ids": ["runtime_telemetry.json"]}

    @staticmethod
    def refusal(reason: str) -> dict[str, Any]:
        return {
            "status": "refused",
            "tool": None,
            "answer": reason,
            "facts": {},
            "evidence_ids": [],
            "supported_questions": [
                "cash position", "match coverage", "blocked exposure",
                "exception queue summary", "unexplained bank credits",
                "why is <id> unmatched", "exception EXC-0001",
                "trace order_<id>", "trace setl_<id>", "throughput",
            ],
        }

    @property
    def registry(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            "get_cash_position": self.get_cash_position,
            "get_match_metrics": self.get_match_metrics,
            "get_blocked_exposure": self.get_blocked_exposure,
            "get_exception": self.get_exception,
            "trace_order": self.trace_order,
            "trace_settlement": self.trace_settlement,
            "get_throughput": self.get_throughput,
            "summarize_exception_queue": self.summarize_exception_queue,
            "list_unexplained_bank_credits": self.list_unexplained_bank_credits,
            "explain_why_unmatched": self.explain_why_unmatched,
        }
