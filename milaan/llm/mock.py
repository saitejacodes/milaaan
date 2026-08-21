"""Deterministic template client used by tests and offline demos."""

from __future__ import annotations

import hashlib
import json

from milaan.llm.client import LLMCall


class MockLLM:
    def __init__(self) -> None:
        self.last_call = LLMCall(model="mock-template")

    def complete_json(self, purpose: str, prompt: str) -> dict:
        payload = json.loads(prompt)
        self.last_call = LLMCall(
            model="mock-template", tokens_in=0, tokens_out=0, cost_paise=0,
            latency_ms=0, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(), cache_hit=False,
        )
        return {
            "narrative": payload["canonical_narrative"],
            "guidance": payload["canonical_guidance"],
            "confidence": 1.0,
        }
