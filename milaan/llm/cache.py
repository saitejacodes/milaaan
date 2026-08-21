"""Persistent LLM response cache, intentionally separate from every run DB."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from milaan.config import repository_root


DDL = """
CREATE TABLE IF NOT EXISTS llm_cache(
  cache_key TEXT PRIMARY KEY,
  provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  response_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def cache_path() -> Path:
    raw = os.getenv("MILAAN_CACHE_PATH")
    return Path(raw) if raw else repository_root() / "data" / ".llm_cache.sqlite"


class ResponseCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(DDL)
        self.conn.commit()

    def get(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT response_json FROM llm_cache WHERE cache_key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, provider: str, model: str, prompt_version: str,
            response: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache VALUES(?,?,?,?,?,?)",
            (key, provider, model, prompt_version,
             json.dumps(response, sort_keys=True, separators=(",", ":")),
             datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ResponseCache":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
