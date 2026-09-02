"""Provider adapters for bounded tool selection and strict language preservation.

Matching, exception classification, financial answers, and metrics never depend
on provider prose. The
supported HTTP contracts are OpenAI-compatible Chat Completions, Anthropic
Messages, and Google Gemini generateContent. Ollama is a configured shortcut
for its OpenAI-compatible endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any
from urllib.parse import quote

from milaan.config import load_environment
from milaan.llm.cache import ResponseCache
from milaan.llm.client import LLMCall


PROMPT_VERSION = "4.0"
SYSTEM_MESSAGE = "Return JSON only. Never make or alter financial facts, matches, or postings."

_PROVIDER_ALIASES = {
    "openai-compatible": "openai-compatible",
    "openai": "openai-compatible",
    "ollama": "openai-compatible",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
}
_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "google": "https://generativelanguage.googleapis.com/v1beta",
}


def _json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating only common markdown wrapping."""
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    candidates = [text]
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last and (first != 0 or last != len(text) - 1):
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("provider did not return a JSON object")


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def _nonnegative_int(name: str, default: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} must not be negative")
    return value


class LiveLLM:
    def __init__(self) -> None:
        initialization_errors: list[str] = []
        try:
            load_environment()
        except (OSError, UnicodeError, ValueError) as exc:
            initialization_errors.append(f"could not load Milaan environment: {exc}")
        self.provider_name = os.getenv("MILAAN_LLM_PROVIDER", "openai-compatible").strip().lower()
        self.provider = _PROVIDER_ALIASES.get(self.provider_name, "")
        self.model = os.getenv("MILAAN_LLM_MODEL", "").strip()
        self.api_key = os.getenv("MILAAN_LLM_API_KEY", "").strip()
        configured_base = os.getenv("MILAAN_LLM_BASE_URL", "").strip()
        self.base_url = (configured_base or _DEFAULT_BASE_URLS.get(self.provider_name, "")).rstrip("/")
        self.json_mode = os.getenv("MILAAN_LLM_JSON_MODE", "native").strip().lower()
        try:
            self.timeout_seconds = float(os.getenv("MILAAN_LLM_TIMEOUT_SECONDS", "30"))
        except ValueError:
            self.timeout_seconds = 30.0
            initialization_errors.append("MILAAN_LLM_TIMEOUT_SECONDS must be numeric")
        if self.timeout_seconds <= 0:
            self.timeout_seconds = 30.0
            initialization_errors.append("MILAAN_LLM_TIMEOUT_SECONDS must be greater than zero")
        try:
            self.max_tokens = _positive_int("MILAAN_LLM_MAX_TOKENS", 600)
        except RuntimeError as exc:
            self.max_tokens = 600
            initialization_errors.append(str(exc))
        self.initialization_error = "; ".join(initialization_errors) or None
        label = f"{self.provider_name}:{self.model}" if self.model else "unconfigured"
        self.last_call = LLMCall(model=label)

    def _configuration_error(self) -> str | None:
        if self.initialization_error:
            return self.initialization_error
        if not self.provider:
            choices = ", ".join(("openai-compatible", "ollama", "anthropic", "gemini"))
            return f"unsupported MILAAN_LLM_PROVIDER={self.provider_name!r}; choose {choices}"
        if not self.model:
            return "MILAAN_LLM_MODEL is required"
        if not self.base_url:
            return "MILAAN_LLM_BASE_URL is required for OpenAI-compatible providers"
        if self.provider in {"anthropic", "gemini"} and not self.api_key:
            return "MILAAN_LLM_API_KEY is required for this provider"
        if self.json_mode not in {"native", "prompt"}:
            return "MILAAN_LLM_JSON_MODE must be 'native' or 'prompt'"
        return None

    @property
    def available(self) -> bool:
        return self._configuration_error() is None

    def _key(self, purpose: str, prompt: str) -> str:
        fields = (
            self.provider, self.base_url, self.model, self.json_mode,
            PROMPT_VERSION, purpose, prompt,
        )
        canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _openai_request(self, prompt: str) -> tuple[str, dict[str, str], dict, dict | None]:
        endpoint = self.base_url
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
        }
        if self.json_mode == "native":
            body["response_format"] = {"type": "json_object"}
        return endpoint, headers, body, None

    def _anthropic_request(self, prompt: str) -> tuple[str, dict[str, str], dict, dict | None]:
        endpoint = self.base_url
        if not endpoint.endswith("/messages"):
            endpoint += "/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "system": SYSTEM_MESSAGE,
            "messages": [{"role": "user", "content": prompt}],
        }
        return endpoint, headers, body, None

    def _gemini_request(self, prompt: str) -> tuple[str, dict[str, str], dict, dict | None]:
        model = self.model.removeprefix("models/")
        endpoint = f"{self.base_url}/models/{quote(model, safe='-._')}:generateContent"
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": SYSTEM_MESSAGE}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": self.max_tokens},
        }
        if self.json_mode == "native":
            body["generationConfig"]["responseMimeType"] = "application/json"
        return endpoint, {"Content-Type": "application/json"}, body, {"key": self.api_key}

    def _request(self, prompt: str) -> tuple[str, dict[str, str], dict, dict | None]:
        if self.provider == "openai-compatible":
            return self._openai_request(prompt)
        if self.provider == "anthropic":
            return self._anthropic_request(prompt)
        if self.provider == "gemini":
            return self._gemini_request(prompt)
        raise RuntimeError(self._configuration_error() or "unsupported LLM provider")

    def _response(self, payload: dict[str, Any]) -> tuple[str, int | None, int | None]:
        try:
            if self.provider == "openai-compatible":
                raw = payload["choices"][0]["message"]["content"]
                usage = payload.get("usage") or {}
                return str(raw), usage.get("prompt_tokens"), usage.get("completion_tokens")
            if self.provider == "anthropic":
                blocks = payload["content"]
                raw = next(block["text"] for block in blocks if block.get("type") == "text")
                usage = payload.get("usage") or {}
                return str(raw), usage.get("input_tokens"), usage.get("output_tokens")
            parts = payload["candidates"][0]["content"]["parts"]
            raw = "".join(str(part["text"]) for part in parts if "text" in part)
            usage = payload.get("usageMetadata") or {}
            return raw, usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
        except (KeyError, IndexError, StopIteration, TypeError) as exc:
            raise RuntimeError(f"malformed {self.provider_name} response") from exc

    def complete_json(self, purpose: str, prompt: str) -> dict:
        problem = self._configuration_error()
        if problem:
            raise RuntimeError(f"live LLM is not configured: {problem}; canonical templates remain active")
        key = self._key(purpose, prompt)
        with ResponseCache() as cache:
            cached = cache.get(key)
            if cached is not None:
                self.last_call = LLMCall(
                    model=f"{self.provider_name}:{self.model}",
                    prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                    cache_hit=True, latency_ms=0,
                )
                return cached

            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError("httpx is required for live mode") from exc
            endpoint, headers, body, params = self._request(prompt)
            started = time.perf_counter()
            try:
                response = httpx.post(
                    endpoint, headers=headers, params=params, json=body,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"{self.provider_name} request failed: {exc}") from exc
            raw, tokens_in, tokens_out = self._response(payload)
            parsed = _json_object(raw)
            price_in = _nonnegative_int("MILAAN_LLM_PRICE_IN_PAISE_PER_1K")
            price_out = _nonnegative_int("MILAAN_LLM_PRICE_OUT_PAISE_PER_1K")
            cost = ((tokens_in or 0) * price_in + (tokens_out or 0) * price_out + 999) // 1000
            self.last_call = LLMCall(
                model=f"{self.provider_name}:{self.model}",
                tokens_in=tokens_in, tokens_out=tokens_out,
                cost_paise=cost, latency_ms=round((time.perf_counter() - started) * 1000),
                prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(), cache_hit=False,
            )
            cache.put(key, self.provider, self.model, PROMPT_VERSION, parsed)
            return parsed
