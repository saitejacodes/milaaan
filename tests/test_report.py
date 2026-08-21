from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory
from milaan.report.render import render_report


class ReportTests(unittest.TestCase):
    def test_report_is_self_contained_and_has_proof_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(500, 42, "mixed", run)
            run_pipeline(run, run / "m.db", "mock")
            evaluate_run(run, run / "m.db", run, "mixed")
            out = run / "report.html"
            render_report(run, run / "m.db", out)
            text = out.read_text()
            self.assertIn("Functional results", text)
            self.assertIn("Deterministic B2 recovery", text)
            self.assertIn("two settlements, one processing date", text)
            self.assertNotIn("<script src=", text)
            self.assertGreater(out.stat().st_size, 8_000)


if __name__ == "__main__":
    unittest.main()
