"""Audit helpers kept separate so all state changes remain easy to trace."""

from __future__ import annotations

import sqlite3

from milaan.db import audit


def event(conn: sqlite3.Connection, run_id: str, action: str, payload: object,
          actor: str = "milaan") -> None:
    audit(conn, run_id, actor, action, payload)
