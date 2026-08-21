"""Field firewall and evidence-subset check for language-only model output."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from typing import Any, Iterable

from milaan import db
from milaan.llm.live import LiveLLM
from milaan.llm.mock import MockLLM
from milaan.models import ExceptionItem


ID_RE = re.compile(r"\b(?:order|pay|rfnd|adj|setl|bank|trf)_[A-Za-z0-9_-]+\b")
RUPEE_RE = re.compile(r"₹\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
PAISE_RE = re.compile(r"\b([0-9]+)\s+paise\b", re.IGNORECASE)


def _walk(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list, tuple)):
                yield from _walk(child)
            else:
                yield str(key), child
    elif isinstance(value, (list, tuple)):
        for child in value:
            if isinstance(child, (dict, list, tuple)):
                yield from _walk(child)
            else:
                yield None, child


def _allowed_facts(item: ExceptionItem) -> tuple[set[str], set[int]]:
    ids = set(item.scope_ids)
    amounts: set[int] = set()
    for key, value in _walk(item.evidence):
        if isinstance(value, str):
            ids.update(ID_RE.findall(value))
        if key and any(token in key for token in ("amount", "credit", "fee", "tax", "diff")):
            if isinstance(value, int):
                amounts.add(value)
    return ids, amounts


def content_is_invariant(item: ExceptionItem, narrative: str, guidance: list[str]) -> bool:
    text = narrative + " " + " ".join(guidance)
    allowed_ids, allowed_amounts = _allowed_facts(item)
    if not set(ID_RE.findall(text)).issubset(allowed_ids):
        return False
    mentioned_paise = {int(value) for value in PAISE_RE.findall(text)}
    mentioned_rupees = {
        round(float(value.replace(",", "")) * 100) for value in RUPEE_RE.findall(text)
    }
    return (mentioned_paise | mentioned_rupees).issubset(allowed_amounts)


def _log_call(conn: sqlite3.Connection, run_id: str, purpose: str, call: Any) -> None:
    conn.execute(
        """INSERT INTO llm_calls(ts,run_id,purpose,model,tokens_in,tokens_out,cost_paise,
           latency_ms,prompt_hash,cache_hit) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (db.utc_now(), run_id, purpose, call.model, call.tokens_in, call.tokens_out,
         call.cost_paise, call.latency_ms, call.prompt_hash or "0" * 64, int(call.cache_hit)),
    )


def polish_exceptions(conn: sqlite3.Connection, run_id: str, items: list[ExceptionItem],
                      mode: str) -> list[ExceptionItem]:
    client = LiveLLM() if mode == "live" else MockLLM()
    polished: list[ExceptionItem] = []
    for item in items:
        canonical_guidance = [line.removeprefix("- ") for line in item.guidance.splitlines() if line]
        prompt = json.dumps({
            "reason_code": item.reason, "scope_ids": item.scope_ids,
            "evidence": item.evidence, "canonical_narrative": item.narrative,
            "canonical_guidance": canonical_guidance,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        try:
            output = client.complete_json("exception_narrate", prompt)
            narrative = str(output["narrative"]).strip()
            guidance_raw = output["guidance"]
            if not isinstance(guidance_raw, list) or len(guidance_raw) > 3:
                raise ValueError("guidance must be a list of at most three checks")
            guidance = [str(value).strip() for value in guidance_raw]
            if not content_is_invariant(item, narrative, guidance):
                db.audit(conn, run_id, "llm", "polish_rejected", {
                    "reason": item.reason, "scope_ids": item.scope_ids,
                    "cause": "identifier_or_amount_drift",
                })
                polished.append(item)
            else:
                polished.append(replace(item, narrative=narrative,
                                        guidance="\n".join(f"- {line}" for line in guidance)))
            _log_call(conn, run_id, "exception_narrate", client.last_call)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            db.audit(conn, run_id, "llm", "polish_unavailable", {
                "reason": item.reason, "cause": type(exc).__name__,
            })
            call = getattr(client, "last_call", None)
            if call is not None:
                _log_call(conn, run_id, "exception_narrate", call)
            polished.append(item)
    return polished
