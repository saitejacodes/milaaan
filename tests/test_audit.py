from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.generator.emit import generate_to_directory


class AuditTests(unittest.TestCase):
    def test_every_decision_and_polish_call_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(300, 19, "mixed", run)
            run_pipeline(run, run / "m.db", "mock")
            conn = sqlite3.connect(run / "m.db")
            matches = conn.execute("SELECT count(*) FROM matches").fetchone()[0]
            match_audits = conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='match_created'"
            ).fetchone()[0]
            exceptions = conn.execute("SELECT count(*) FROM exceptions").fetchone()[0]
            calls = conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
            conn.close()
            self.assertEqual(match_audits, matches)
            self.assertEqual(calls, exceptions)


if __name__ == "__main__":
    unittest.main()
