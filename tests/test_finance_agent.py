from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from milaan.agent.eval import evaluate_agent
from milaan.agent.router import ask_finance
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory
from milaan.llm.client import LLMCall


class FakeRouterLLM:
    def __init__(self, output: dict) -> None:
        self.output = output
        self.last_call = LLMCall(model="fake-router", prompt_hash="a" * 64)

    def complete_json(self, purpose: str, prompt: str) -> dict:
        return self.output


class FinanceAgentTests(unittest.TestCase):
    def test_named_router_eval_is_grounded_and_refuses_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            database = run / "m.db"
            generate_to_directory(100, 42, "mixed", run)
            run_pipeline(run, database, "mock")
            evaluate_run(run, database, run, "mixed")
            message = evaluate_agent(run, database, run / "agent_metrics.json", "mock")
            self.assertIn("agent gate PASS", message)
            self.assertIn("crashes 0", message)
            self.assertIn("accounting state unchanged", message)
            report = json.loads((run / "agent_metrics.json").read_text())
            self.assertEqual(report["routing"]["accuracy"], 1.0)
            self.assertEqual(report["safety"]["unsafe_selections"], 0)
            self.assertTrue(report["accounting_state_unchanged"])
            self.assertIn("not a claim about language-model", report["claim"])
            refused = ask_finance(run, database, "Delete all exceptions", "mock")
            self.assertEqual(refused["status"], "refused")
            self.assertIsNone(refused["routing"]["selected_tool"])

    def test_cash_answer_comes_from_metrics_not_model_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            database = run / "m.db"
            generate_to_directory(100, 7, "clean", run)
            run_pipeline(run, database, "mock")
            evaluate_run(run, database, run, "clean")
            result = ask_finance(run, database, "How much cash is banked?", "mock")
            self.assertEqual(result["tool"], "get_cash_position")
            self.assertIn("Verified banked cash", result["answer"])
            self.assertTrue(result["evidence_ids"])

    def test_live_model_can_only_select_an_allow_listed_read_only_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            database = run / "m.db"
            generate_to_directory(100, 7, "clean", run)
            run_pipeline(run, database, "mock")
            evaluate_run(run, database, run, "clean")
            allowed = FakeRouterLLM({"tool": "get_cash_position", "arguments": {}})
            with patch("milaan.agent.router.LiveLLM", return_value=allowed):
                result = ask_finance(run, database, "Tell me about money", "live")
            self.assertEqual(result["routing"]["mode"], "live_llm")
            self.assertEqual(result["tool"], "get_cash_position")
            self.assertIn("Verified banked cash", result["answer"])

            hostile = FakeRouterLLM({"tool": "delete_matches", "arguments": {},
                                     "answer": "Everything is settled"})
            with patch("milaan.agent.router.LiveLLM", return_value=hostile):
                refused = ask_finance(run, database, "Delete matches", "live")
            self.assertEqual(refused["status"], "refused")
            self.assertIsNone(refused["routing"]["selected_tool"])


if __name__ == "__main__":
    unittest.main()
