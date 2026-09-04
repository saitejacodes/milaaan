"""SQLite persistence and database-enforced match exclusivity."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from milaan.models import BankLine, Decision, ExceptionItem, GatewayTxn, Order, Plane, SettlementBatch


DDL = """
CREATE TABLE runs(
  run_id TEXT PRIMARY KEY, seed INTEGER NOT NULL, profile TEXT NOT NULL,
  llm_mode TEXT NOT NULL, started_at TEXT NOT NULL,
  orders_sha256 TEXT NOT NULL, txns_sha256 TEXT NOT NULL, bank_sha256 TEXT NOT NULL,
  fees_config_sha256 TEXT NOT NULL, timing_config_sha256 TEXT NOT NULL, git_sha TEXT);

CREATE TABLE raw_orders(
  order_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, amount_paise INTEGER NOT NULL,
  status TEXT NOT NULL, channel TEXT NOT NULL, payment_id TEXT,
  source_row_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE raw_txns(
  txn_id TEXT PRIMARY KEY, txn_type TEXT NOT NULL, payment_id TEXT,
  original_payment_id TEXT, refund_id TEXT, order_ref TEXT, channel TEXT,
  gross_paise INTEGER NOT NULL, fee_paise INTEGER NOT NULL, tax_paise INTEGER NOT NULL,
  net_paise INTEGER NOT NULL, captured_at TEXT NOT NULL,
  settlement_id TEXT, settlement_utr TEXT, settlement_processed_at TEXT,
  source_row_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE raw_bank(
  line_id TEXT PRIMARY KEY, txn_date TEXT NOT NULL, value_date TEXT NOT NULL,
  narration TEXT NOT NULL, credit_paise INTEGER NOT NULL, debit_paise INTEGER NOT NULL,
  balance_paise INTEGER NOT NULL, ref_no TEXT NOT NULL,
  source_row_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE quarantine_rows(
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
  source TEXT NOT NULL, source_row_id TEXT NOT NULL, reason TEXT NOT NULL,
  settlement_id TEXT, raw_json TEXT NOT NULL);

CREATE TABLE settlement_batches(
  settlement_id TEXT PRIMARY KEY, settlement_utr TEXT,
  amount_paise INTEGER NOT NULL, processed_at TEXT NOT NULL,
  member_count INTEGER NOT NULL, tainted INTEGER NOT NULL DEFAULT 0,
  run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE matches(
  match_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
  plane TEXT NOT NULL CHECK(plane IN ('A','B')),
  kind TEXT NOT NULL, tier TEXT NOT NULL, right_id TEXT NOT NULL,
  amount_diff_paise INTEGER NOT NULL, date_gap_bd INTEGER NOT NULL,
  confidence REAL NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(match_id, run_id, plane));

-- Exclusivity is enforced by UNIQUE; the CHECK stops a member of the wrong kind
-- being attached to a plane at all, so a bank line can never be recorded as an
-- order and the evaluator can rely on the shape it reads back.
CREATE TABLE match_members(
  match_id INTEGER NOT NULL, run_id TEXT NOT NULL,
  plane TEXT NOT NULL CHECK(plane IN ('A','B')),
  entity_type TEXT NOT NULL CHECK(entity_type IN ('ORDER','TXN','BATCH','BANK_LINE')),
  entity_id TEXT NOT NULL,
  CHECK((plane = 'A' AND entity_type IN ('ORDER','TXN'))
     OR (plane = 'B' AND entity_type IN ('BATCH','BANK_LINE'))),
  UNIQUE(run_id, plane, entity_type, entity_id),
  FOREIGN KEY(match_id, run_id, plane) REFERENCES matches(match_id, run_id, plane));

CREATE TABLE exceptions(
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), scope_ids TEXT NOT NULL,
  reason_code TEXT NOT NULL, confidence REAL NOT NULL, evidence TEXT NOT NULL,
  narrative TEXT NOT NULL DEFAULT '', guidance TEXT NOT NULL DEFAULT '',
  suggested_action TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE audit_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
  actor TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);

CREATE TABLE llm_calls(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
  purpose TEXT NOT NULL, model TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER,
  cost_paise INTEGER, latency_ms INTEGER, prompt_hash TEXT NOT NULL, cache_hit INTEGER NOT NULL);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("SQLite foreign-key enforcement is unavailable")
    return conn


def create_fresh(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            raise ValueError(f"database path is not a file: {path}")
        path.unlink()
    conn = connect(path)
    conn.executescript(DDL)
    conn.commit()
    return conn


def insert_run(conn: sqlite3.Connection, *, run_id: str, seed: int, profile: str,
               llm_mode: str, hashes: dict[str, str], git_sha: str | None) -> None:
    conn.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, seed, profile, llm_mode, utc_now(), hashes["orders"], hashes["txns"],
         hashes["bank"], hashes["fees"], hashes["timing"], git_sha),
    )


def insert_orders(conn: sqlite3.Connection, run_id: str, orders: Iterable[Order]) -> None:
    conn.executemany(
        "INSERT INTO raw_orders VALUES(?,?,?,?,?,?,?,?)",
        ((o.order_id, o.created_at.isoformat(), o.amount_paise, o.status.value, o.channel.value,
          o.payment_id, o.source_row_id, run_id) for o in orders),
    )


def insert_txns(conn: sqlite3.Connection, run_id: str, txns: Iterable[GatewayTxn]) -> None:
    conn.executemany(
        "INSERT INTO raw_txns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ((t.txn_id, t.txn_type.value, t.payment_id, t.original_payment_id, t.refund_id,
          t.order_ref, t.channel.value if t.channel else None, t.gross_paise, t.fee_paise,
          t.tax_paise, t.net_paise, t.captured_at.isoformat(), t.settlement_id,
          t.settlement_utr, t.settlement_processed_at.isoformat() if t.settlement_processed_at else None,
          t.source_row_id, run_id) for t in txns),
    )


def insert_bank(conn: sqlite3.Connection, run_id: str, lines: Iterable[BankLine]) -> None:
    conn.executemany(
        "INSERT INTO raw_bank VALUES(?,?,?,?,?,?,?,?,?,?)",
        ((b.line_id, b.txn_date.isoformat(), b.value_date.isoformat(), b.narration,
          b.credit_paise, b.debit_paise, b.balance_paise, b.ref_no, b.source_row_id, run_id)
         for b in lines),
    )


def insert_batches(conn: sqlite3.Connection, run_id: str,
                   batches: Iterable[SettlementBatch]) -> None:
    conn.executemany(
        "INSERT INTO settlement_batches VALUES(?,?,?,?,?,?,?)",
        ((b.settlement_id, b.settlement_utr, b.amount_paise, b.processed_at.isoformat(),
          len(b.member_txn_ids), int(b.tainted), run_id) for b in batches),
    )


def audit(conn: sqlite3.Connection, run_id: str, actor: str, action: str,
          payload: object) -> None:
    conn.execute(
        "INSERT INTO audit_log(ts,run_id,actor,action,payload) VALUES(?,?,?,?,?)",
        (utc_now(), run_id, actor, action, dumps(payload)),
    )


def insert_decisions(conn: sqlite3.Connection, run_id: str,
                     decisions: Iterable[Decision]) -> list[int]:
    """Persist a whole plane in one transaction.

    Committing per match cost a separate fsync each time and dominated the
    run. Batching also strengthens the guarantee: a plane is written whole or
    not at all, and the UNIQUE(run_id, plane, entity_type, entity_id) exclusivity
    constraint is still enforced on every individual member row.
    """
    with conn:
        return [_write_decision(conn, run_id, decision) for decision in decisions]


def insert_decision(conn: sqlite3.Connection, run_id: str, decision: Decision) -> int:
    with conn:
        return _write_decision(conn, run_id, decision)


def _write_decision(conn: sqlite3.Connection, run_id: str, decision: Decision) -> int:
    """Write one match. The caller owns the transaction."""
    cur = conn.execute(
        """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
           date_gap_bd,confidence,evidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (run_id, decision.plane.value, decision.kind.value, decision.tier.value,
         decision.right_id, decision.amount_diff_paise, decision.date_gap_bd,
         decision.confidence, dumps(decision.evidence), utc_now()),
    )
    match_id = int(cur.lastrowid)
    if decision.plane is Plane.A:
        members = [("ORDER", entity_id) for entity_id in decision.left_ids]
        members.append(("TXN", decision.right_id))
    else:
        members = [("BATCH", entity_id) for entity_id in decision.left_ids]
        members.append(("BANK_LINE", decision.right_id))
    conn.executemany(
        "INSERT INTO match_members(match_id,run_id,plane,entity_type,entity_id) VALUES(?,?,?,?,?)",
        ((match_id, run_id, decision.plane.value, typ, entity_id) for typ, entity_id in members),
    )
    audit(conn, run_id, "engine", "match_created", {
        "match_id": match_id, "plane": decision.plane.value,
        "tier": decision.tier.value, "members": members,
    })
    return match_id


def insert_exceptions(conn: sqlite3.Connection, run_id: str,
                      items: Iterable[ExceptionItem]) -> list[int]:
    with conn:
        return [_write_exception(conn, run_id, item) for item in items]


def insert_exception(conn: sqlite3.Connection, run_id: str, item: ExceptionItem) -> int:
    with conn:
        return _write_exception(conn, run_id, item)


def _write_exception(conn: sqlite3.Connection, run_id: str, item: ExceptionItem) -> int:
    """Write one exception. The caller owns the transaction."""
    cur = conn.execute(
        """INSERT INTO exceptions(run_id,scope_ids,reason_code,confidence,evidence,
           narrative,guidance,suggested_action,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (run_id, dumps(item.scope_ids), item.reason, item.confidence,
         dumps(item.evidence), item.narrative, item.guidance,
         item.suggested_action, utc_now()),
    )
    exception_id = int(cur.lastrowid)
    audit(conn, run_id, "engine", "exception_created", {
        "exception_id": exception_id, "reason": item.reason, "scope_ids": item.scope_ids,
    })
    return exception_id
