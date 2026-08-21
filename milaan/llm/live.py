"""OpenAI-compatible JSON client used only to polish exception language."""

from __future__ import annotations

import hashlib
import json
import os
import time

from milaan.llm.cache import ResponseCache
from milaan.llm.client import LLMCall


PROMPT_VERSION = "3.0"


class LiveLLM:
    def __init__(self) -> None:
        self.provider = os.getenv("MILAAN_LLM_PROVIDER", "openai-compatible")
        self.model = os.getenv("MILAAN_LLM_MODEL", "")
        self.api_key = os.getenv("MILAAN_LLM_API_KEY", "")
        self.base_url = os.getenv("MILAAN_LLM_BASE_URL", "")
        self.last_call = LLMCall(model=self.model or "unconfigured")

    @property
    def available(self) -> bool:
        return bool(self.model and self.api_key and self.base_url)

    def _key(self, purpose: str, prompt: str) -> str:
        fields = (self.provider, self.model, PROMPT_VERSION, purpose, prompt)
        canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def complete_json(self, purpose: str, prompt: str) -> dict:
        if not self.available:
            raise RuntimeError("live LLM is not configured; canonical templates remain active")
        key = self._key(purpose, prompt)
        with ResponseCache() as cache:
            cached = cache.get(key)
            if cached is not None:
                self.last_call = LLMCall(
                    model=self.model, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                    cache_hit=True, latency_ms=0,
                )
                return cached

            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError("httpx is required for live mode") from exc
            endpoint = self.base_url.rstrip("/")
            if not endpoint.endswith("/chat/completions"):
                endpoint += "/chat/completions"
            body = {
                "model": self.model, "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Return JSON only. You may rewrite language, never facts."},
                    {"role": "user", "content": prompt},
                ],
            }
            started = time.perf_counter()
            response = httpx.post(
                endpoint, headers={"Authorization": f"Bearer {self.api_key}"},
                json=body, timeout=30.0,
            )
            response.raise_for_status()
            payload = response.json()
            raw = payload["choices"][0]["message"]["content"]
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                repair = dict(body)
                repair["messages"] = body["messages"] + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "Repair the previous output into valid JSON only."},
                ]
                response = httpx.post(endpoint, headers={"Authorization": f"Bearer {self.api_key}"},
                                      json=repair, timeout=30.0)
                response.raise_for_status()
                parsed = json.loads(response.json()["choices"][0]["message"]["content"])
            usage = payload.get("usage", {})
            tokens_in, tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
            price_in = int(os.getenv("MILAAN_LLM_PRICE_IN_PAISE_PER_1K", "0"))
            price_out = int(os.getenv("MILAAN_LLM_PRICE_OUT_PAISE_PER_1K", "0"))
            cost = ((tokens_in or 0) * price_in + (tokens_out or 0) * price_out + 999) // 1000
            self.last_call = LLMCall(
                model=self.model, tokens_in=tokens_in, tokens_out=tokens_out,
                cost_paise=cost, latency_ms=round((time.perf_counter() - started) * 1000),
                prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(), cache_hit=False,
            )
            cache.put(key, self.provider, self.model, PROMPT_VERSION, parsed)
            return parsed
