from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory
from milaan.report.render import render_report
from milaan.view import ATTENTION_DISCLAIMER, COVERAGE_DISCLAIMER, build_view


class ReportTests(unittest.TestCase):
    text: str
    run_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = tempfile.TemporaryDirectory(prefix="milaan-report-")
        cls.run_dir = Path(cls._root.name) / "run"
        generate_to_directory(500, 42, "mixed", cls.run_dir)
        run_pipeline(cls.run_dir, cls.run_dir / "m.db", "mock")
        evaluate_run(cls.run_dir, cls.run_dir / "m.db", cls.run_dir, "mixed")
        render_report(cls.run_dir, cls.run_dir / "m.db", cls.run_dir / "report.html")
        cls.text = (cls.run_dir / "report.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._root.cleanup()

    def test_report_is_self_contained(self) -> None:
        """A judge may open this file offline, from a USB stick, on a plane."""
        self.assertTrue(self.text.startswith("<!doctype html>"))
        for pattern in (r"<script", r'src="http', r'href="http', r"<link\b"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, self.text, re.IGNORECASE))

    def test_report_carries_every_judging_section(self) -> None:
        for heading in (
            "Measured accuracy",
            "Exception quality",
            "Operational coverage",
            "Cash position",
            "Evidence trace: one order, followed to the bank",
            "Exceptions (",
            "Control integrity",
            "Which rule produced each match",
            "Ask Milaan",
            "Runtime telemetry",
        ):
            with self.subTest(section=heading):
                self.assertIn(heading, self.text)

    def test_report_states_the_mandatory_disclaimers(self) -> None:
        self.assertIn(COVERAGE_DISCLAIMER, self.text)
        self.assertIn(ATTENTION_DISCLAIMER, self.text)

    def test_report_uses_finance_vocabulary_not_internal_plane_names(self) -> None:
        self.assertIn("Order → Payment reconciliation", self.text)
        self.assertIn("Settlement → Bank reconciliation", self.text)
        # Internal tier codes may appear, but always with their explanation.
        self.assertIn("A0 — exact gateway order reference", self.text)
        self.assertIn("B0 — exact settlement reference and amount", self.text)

    def test_evidence_trace_balances_exactly(self) -> None:
        view = build_view(self.run_dir, self.run_dir / "m.db")
        trace = view.trace
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertEqual(trace.member_total_paise, trace.bank_credit_paise)
        self.assertEqual(trace.difference_paise, 0)
        self.assertEqual(trace.status, "VERIFIED BANKED")
        # The abridged member list must still sum to the true batch total.
        self.assertEqual(
            sum(int(row["net_paise"]) for row in trace.display_members),
            trace.member_total_paise,
        )
        self.assertIn("VERIFIED BANKED", self.text)

    def test_every_exception_answers_the_four_operator_questions(self) -> None:
        view = build_view(self.run_dir, self.run_dir / "m.db")
        self.assertTrue(view.exceptions)
        for item in view.exceptions:
            with self.subTest(exception=item.exception_id):
                self.assertIn(item.exception_id, self.text)
                self.assertTrue(item.headline)            # what failed
                self.assertTrue(item.explanation)         # why Milaan abstained
                self.assertTrue(item.exposure)            # how much money
                self.assertTrue(item.next_action)         # what to do next
                self.assertTrue(item.scope_ids)
                self.assertNotEqual(item.explanation, item.headline)
        for question in ("What failed", "Why Milaan abstained", "Money affected",
                         "What finance should do next"):
            self.assertIn(question, self.text)

    def test_report_shows_a_refused_write_request(self) -> None:
        self.assertIn("Delete all exceptions.", self.text)
        self.assertIn("read-only", self.text)

    def test_report_refuses_a_run_without_published_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp)
            with self.assertRaises(ValueError):
                render_report(empty, self.run_dir / "m.db", empty / "report.html")


if __name__ == "__main__":
    unittest.main()
