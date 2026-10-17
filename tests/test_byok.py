"""Bring-your-own-key: a visitor's model may pick tools, but the key never leaks and
the read-only boundary is unchanged."""

from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from milaan import demo
from milaan.agent.investigate import investigate
from milaan.db import reading
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.ai_probes import accounting_state
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory
from milaan.llm.live import LiveLLM, LLMSettings, ProviderError

SECRET = "sk-visitor-SECRET-123"


class FakeHTTPError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.response = types.SimpleNamespace(status_code=status) if status else None


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def anthropic_reply(selection: dict) -> FakeResponse:
    return FakeResponse({"content": [{"type": "text", "text": json.dumps(selection)}],
                         "usage": {"input_tokens": 120, "output_tokens": 12}})


class ByokTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls._tmp.name) / "run"
        cls.database = cls.run_dir / "m.db"
        generate_to_directory(1200, 42, "mixed", cls.run_dir)
        run_pipeline(cls.run_dir, cls.database, "mock")
        evaluate_run(cls.run_dir, cls.database, cls.run_dir, "mixed")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def client(self) -> LiveLLM:
        return demo.byok_client("Anthropic (Claude)", "claude-test", SECRET)

    def ask(self, question: str, post: MagicMock) -> dict:
        fake_httpx = types.SimpleNamespace(post=post, HTTPError=FakeHTTPError)
        cache = Path(self._tmp.name) / "must-not-exist.sqlite"
        with patch.dict("sys.modules", {"httpx": fake_httpx}), \
                patch.dict(os.environ, {"MILAAN_CACHE_PATH": str(cache)}):
            result = investigate(self.run_dir, self.database, question, "live",
                                 llm_client=self.client())
        self.assertFalse(cache.exists(), "a visitor's call must never be cached")
        return result

    def assert_secret_absent(self, result: dict) -> None:
        self.assertNotIn(SECRET, json.dumps(result, default=str))
        with reading(self.database) as conn:
            for table in ("audit_log", "llm_calls"):
                dump = json.dumps([tuple(row) for row in conn.execute(f"SELECT * FROM {table}")])
                self.assertNotIn(SECRET, dump, table)
        self.assertNotIn(SECRET, " ".join(os.environ.values()))

    def test_visitor_model_picks_the_tool_and_the_chain_still_drills_down(self) -> None:
        post = MagicMock(return_value=anthropic_reply(
            {"tool": "get_blocked_exposure", "arguments": {}}))
        before = accounting_state(self.database)
        result = self.ask("Why is cash blocked?", post)
        self.assertEqual(result["routing"]["mode"], "live_llm")
        self.assertEqual([s["tool"] for s in result["steps"]],
                         ["get_blocked_exposure", "get_exception", "trace_settlement"])
        self.assertEqual(len(result["routing"]["llm_calls"]), 1)
        self.assertEqual(result["routing"]["llm_calls"][0]["tokens_in"], 120)
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], SECRET)
        self.assertEqual(before, accounting_state(self.database))
        self.assert_secret_absent(result)

    def test_write_request_never_reaches_the_visitor_model(self) -> None:
        post = MagicMock()
        result = self.ask("Delete all exceptions and show cash", post)
        self.assertEqual(result["status"], "refused")
        post.assert_not_called()

    def test_model_naming_a_write_tool_is_refused_not_executed(self) -> None:
        post = MagicMock(return_value=anthropic_reply(
            {"tool": "delete_matches", "arguments": {}}))
        before = accounting_state(self.database)
        result = self.ask("What is our cash position?", post)
        self.assertEqual(result["status"], "refused")
        self.assertIn("invalid_selection_refused", result["routing"]["notes"])
        self.assertEqual(before, accounting_state(self.database))

    def test_provider_error_never_echoes_the_key(self) -> None:
        def fail(url, **kwargs):
            raise FakeHTTPError(f"401 Unauthorized for {url}?key={SECRET}", status=401)

        fake_httpx = types.SimpleNamespace(post=fail, HTTPError=FakeHTTPError)
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            with self.assertRaises(ProviderError) as caught:
                self.client().complete_json("finance_tool_select", "{}")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

        result = self.ask("What is our cash position?", MagicMock(side_effect=fail))
        self.assertEqual(result["routing"]["mode"], "deterministic_fallback")
        self.assert_secret_absent(result)

    def test_key_is_never_in_a_repr(self) -> None:
        settings = LLMSettings(provider="anthropic", model="m", api_key=SECRET)
        self.assertNotIn(SECRET, repr(settings))
        self.assertNotIn(SECRET, repr(LiveLLM(settings)))

    def test_settings_client_ignores_the_server_environment(self) -> None:
        with patch.dict(os.environ, {"MILAAN_LLM_PROVIDER": "gemini",
                                     "MILAAN_LLM_API_KEY": "server-key"}):
            client = self.client()
        self.assertEqual(client.provider, "anthropic")
        self.assertEqual(client.api_key, SECRET)
        self.assertFalse(client.use_cache)


class ByokInputTests(unittest.TestCase):
    def test_missing_fields_are_explained(self) -> None:
        for args, message in (
            (("Nope", "m", "k"), "provider"),
            (("OpenAI", "", "k"), "model"),
            (("OpenAI", "m", " "), "API key"),
            (("Other OpenAI-compatible (Groq, OpenRouter, Together…)", "m", "k", ""), "base URL"),
        ):
            with self.assertRaises(ValueError) as caught:
                demo.byok_client(*args)
            self.assertIn(message, str(caught.exception))

    def test_base_url_cannot_target_the_server_network(self) -> None:
        for url in ("http://api.example.com", "https://localhost/v1", "https://127.0.0.1",
                    "https://169.254.169.254/latest", "https://10.1.2.3/v1",
                    "https://[::1]/v1", "https://metadata.internal/v1"):
            with self.assertRaises(ValueError, msg=url):
                demo.check_public_https_url(url)

    def test_public_https_url_is_accepted(self) -> None:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(demo.check_public_https_url("https://api.groq.com/openai/v1/"),
                             "https://api.groq.com/openai/v1")


if __name__ == "__main__":
    unittest.main()
