"""Language-only LLM protocol and telemetry record."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMCall:
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_paise: int | None = None
    latency_ms: int | None = None
    prompt_hash: str = ""
    cache_hit: bool = False


class LLMClient(Protocol):
    last_call: LLMCall

    def complete_json(self, purpose: str, prompt: str) -> dict: ...
