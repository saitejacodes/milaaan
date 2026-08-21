from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from milaan.config import load_environment
from milaan.llm.live import LiveLLM, _json_object


RESULT = {"narrative": "Checked evidence.", "guidance": ["Review source."], "confidence": 1}


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHTTPError(Exception):
    pass


class ProviderAdapterTests(unittest.TestCase):
    def environment(self, tmp: str, **values: str) -> dict[str, str]:
        return {
            "MILAAN_CACHE_PATH": str(Path(tmp) / "cache.sqlite"),
            "MILAAN_LLM_PROVIDER": values.get("provider", "openai-compatible"),
            "MILAAN_LLM_MODEL": values.get("model", "test-model"),
            "MILAAN_LLM_API_KEY": values.get("api_key", ""),
            "MILAAN_LLM_BASE_URL": values.get("base_url", ""),
            "MILAAN_LLM_JSON_MODE": values.get("json_mode", "native"),
        }

    def test_openai_compatible_supports_local_server_without_key(self) -> None:
        payload = {
            "choices": [{"message": {"content": json.dumps(RESULT)}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        post = MagicMock(return_value=FakeResponse(payload))
        fake_httpx = types.SimpleNamespace(post=post, HTTPError=FakeHTTPError)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            self.environment(tmp, base_url="http://localhost:9999/v1"),
            clear=True,
        ), patch.dict("sys.modules", {"httpx": fake_httpx}):
            client = LiveLLM()
            self.assertTrue(client.available)
            self.assertEqual(client.complete_json("test", "prompt"), RESULT)

        endpoint = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(endpoint, "http://localhost:9999/v1/chat/completions")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(client.last_call.tokens_in, 11)
        self.assertEqual(client.last_call.tokens_out, 7)

    def test_ollama_shortcut_supplies_default_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            self.environment(tmp, provider="ollama", base_url=""),
            clear=True,
        ):
            client = LiveLLM()
            self.assertTrue(client.available)
            self.assertEqual(client.base_url, "http://localhost:11434/v1")

    def test_anthropic_messages_contract(self) -> None:
        payload = {
            "content": [{"type": "text", "text": f"```json\n{json.dumps(RESULT)}\n```"}],
            "usage": {"input_tokens": 13, "output_tokens": 9},
        }
        post = MagicMock(return_value=FakeResponse(payload))
        fake_httpx = types.SimpleNamespace(post=post, HTTPError=FakeHTTPError)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            self.environment(tmp, provider="anthropic", api_key="secret", base_url=""),
            clear=True,
        ), patch.dict("sys.modules", {"httpx": fake_httpx}):
            client = LiveLLM()
            self.assertEqual(client.complete_json("test", "prompt"), RESULT)

        endpoint = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(endpoint, "https://api.anthropic.com/v1/messages")
        self.assertEqual(kwargs["headers"]["x-api-key"], "secret")
        self.assertEqual(kwargs["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(kwargs["json"]["system"], "Return JSON only. You may rewrite language, never facts.")
        self.assertEqual(client.last_call.tokens_in, 13)
        self.assertEqual(client.last_call.tokens_out, 9)

    def test_gemini_generate_content_contract(self) -> None:
        payload = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(RESULT)}]}}],
            "usageMetadata": {"promptTokenCount": 17, "candidatesTokenCount": 8},
        }
        post = MagicMock(return_value=FakeResponse(payload))
        fake_httpx = types.SimpleNamespace(post=post, HTTPError=FakeHTTPError)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            self.environment(tmp, provider="gemini", model="models/test-model",
                             api_key="secret", base_url=""),
            clear=True,
        ), patch.dict("sys.modules", {"httpx": fake_httpx}):
            client = LiveLLM()
            self.assertEqual(client.complete_json("test", "prompt"), RESULT)

        endpoint = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(
            endpoint,
            "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
        )
        self.assertEqual(kwargs["params"], {"key": "secret"})
        self.assertEqual(kwargs["json"]["generationConfig"]["responseMimeType"],
                         "application/json")
        self.assertEqual(client.last_call.tokens_in, 17)
        self.assertEqual(client.last_call.tokens_out, 8)

    def test_prompt_json_mode_omits_native_openai_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            self.environment(tmp, base_url="https://example.test/v1", json_mode="prompt"),
            clear=True,
        ):
            _endpoint, _headers, body, _params = LiveLLM()._request("prompt")
        self.assertNotIn("response_format", body)

    def test_unsupported_provider_is_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            self.environment(tmp, provider="unknown", base_url="https://example.test"),
            clear=True,
        ):
            client = LiveLLM()
            self.assertFalse(client.available)
            with self.assertRaisesRegex(RuntimeError, "unsupported MILAAN_LLM_PROVIDER"):
                client.complete_json("test", "prompt")

    def test_invalid_numeric_configuration_is_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                **self.environment(tmp, base_url="https://example.test/v1"),
                "MILAAN_LLM_MAX_TOKENS": "many",
            },
            clear=True,
        ):
            client = LiveLLM()
            self.assertFalse(client.available)
            with self.assertRaisesRegex(RuntimeError, "MILAAN_LLM_MAX_TOKENS must be an integer"):
                client.complete_json("test", "prompt")

    def test_json_parser_accepts_fence_but_rejects_non_object(self) -> None:
        self.assertEqual(_json_object("```json\n{\"ok\": true}\n```"), {"ok": True})
        with self.assertRaises(ValueError):
            _json_object("[1, 2, 3]")

    def test_env_file_does_not_override_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("MILAAN_LLM_MODEL=file-model\nexport EXTRA='safe value'\n")
            with patch.dict(os.environ, {"MILAAN_LLM_MODEL": "shell-model"}, clear=True):
                self.assertEqual(load_environment(path), path)
                self.assertEqual(os.environ["MILAAN_LLM_MODEL"], "shell-model")
                self.assertEqual(os.environ["EXTRA"], "safe value")


if __name__ == "__main__":
    unittest.main()
