from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


class LLMModeInvarianceTests(unittest.TestCase):
    def test_functional_metrics_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {
                "MILAAN_LLM_API_KEY": "", "MILAAN_LLM_MODEL": "",
                "MILAAN_LLM_BASE_URL": "",
            }, clear=False,
        ):
            run = Path(tmp) / "run"
            generate_to_directory(500, 42, "mixed", run)
            mock_db, live_db = run / "mock.db", run / "live.db"

            run_pipeline(run, mock_db, "mock")
            evaluate_run(run, mock_db, run, "mixed")
            mock_metrics = (run / "functional_metrics.json").read_bytes()

            run_pipeline(run, live_db, "live")
            evaluate_run(run, live_db, run, "mixed")
            live_metrics = (run / "functional_metrics.json").read_bytes()

            self.assertEqual(mock_metrics, live_metrics)
            conn = sqlite3.connect(mock_db)
            try:
                exception_count = conn.execute("SELECT count(*) FROM exceptions").fetchone()[0]
                call_count = conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(call_count, exception_count)


if __name__ == "__main__":
    unittest.main()
