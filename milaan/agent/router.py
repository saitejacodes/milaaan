"""Allow-listed tool router for read-only finance questions."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any

from milaan import db
from milaan.agent.tools import FinanceTools
from milaan.llm.live import LiveLLM


ORDER_RE = re.compile(r"\border_[A-Za-z0-9_-]+\b", re.IGNORECASE)
SETTLEMENT_RE = re.compile(r"\bsetl_[A-Za-z0-9_-]+\b", re.IGNORECASE)
EXCEPTION_RE = re.compile(r"\b(?:EXC-)?(\d{1,8})\b", re.IGNORECASE)


def deterministic_route(question: str) -> dict[str, Any] | None:
    text = question.strip()
    lowered = text.casefold()
    order = ORDER_RE.search(text)
    settlement = SETTLEMENT_RE.search(text)
    if order:
        return {"tool": "trace_order", "arguments": {"order_id": order.group(0)}}
    if settlement:
        return {"tool": "trace_settlement",
                "arguments": {"settlement_id": settlement.group(0)}}
    if "exception" in lowered or "exc-" in lowered:
        exception = EXCEPTION_RE.search(text)
        if exception:
            return {"tool": "get_exception",
                    "arguments": {"exception_id": f"EXC-{int(exception.group(1)):04d}"}}
    if any(token in lowered for token in (
        "throughput", "records per second", "how fast", "reconciliation fast", "latency",
    )):
        return {"tool": "get_throughput", "arguments": {}}
    if any(token in lowered for token in (
        "blocked", "exposure", "attention", "at risk", "largest issue", "stopped settlement",
    )):
        return {"tool": "get_blocked_exposure", "arguments": {}}
    if any(token in lowered for token in (
        "match rate", "match coverage", "accuracy", "accurate", "precision", "recall",
    )):
        return {"tool": "get_match_metrics", "arguments": {}}
    if any(token in lowered for token in (
        "cash", "banked", "unbanked", "close the books", "close books",
        "reached the bank", "books ready to close",
    )):
        return {"tool": "get_cash_position", "arguments": {}}
    return None


def _selection_prompt(question: str) -> str:
    return json.dumps({
        "instruction": (
            "Select exactly one read-only tool. Do not answer the finance question. "
            "Return JSON with tool and arguments only. Return tool=null when unsupported."
        ),
        "tools": {
            "get_cash_position": {},
            "get_match_metrics": {},
            "get_blocked_exposure": {},
            "get_exception": {"exception_id": "EXC-0001"},
            "trace_order": {"order_id": "order_<id>"},
            "trace_settlement": {"settlement_id": "setl_<id>"},
            "get_throughput": {},
        },
        "question": question,
    }, sort_keys=True, ensure_ascii=False)


def _validated_selection(value: Any, tools: FinanceTools) -> tuple[str, dict[str, str]] | None:
    if not isinstance(value, dict) or set(value) != {"tool", "arguments"}:
        return None
    tool = value.get("tool")
    arguments = value.get("arguments")
    if tool is None:
        return None
    if tool not in tools.registry or not isinstance(arguments, dict):
        return None
    expected = {
        "get_cash_position": set(),
        "get_match_metrics": set(),
        "get_blocked_exposure": set(),
        "get_throughput": set(),
        "get_exception": {"exception_id"},
        "trace_order": {"order_id"},
        "trace_settlement": {"settlement_id"},
    }[tool]
    if set(arguments) != expected or any(not isinstance(value, str) for value in arguments.values()):
        return None
    if tool == "trace_order" and not ORDER_RE.fullmatch(arguments["order_id"]):
        return None
    if tool == "trace_settlement" and not SETTLEMENT_RE.fullmatch(arguments["settlement_id"]):
        return None
    if tool == "get_exception" and not re.fullmatch(r"(?:EXC-)?\d{1,8}", arguments["exception_id"], re.I):
        return None
    return tool, arguments


def _record_query(database_path: Path, run_id: str, question: str,
                  routing: dict[str, Any], call: Any | None = None) -> None:
    conn = db.connect(database_path)
    try:
        with conn:
            db.audit(conn, run_id, "finance_agent", "question_routed", {
                "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
                "routing": routing,
            })
            if call is not None:
                conn.execute(
                    """INSERT INTO llm_calls(ts,run_id,purpose,model,tokens_in,tokens_out,
                       cost_paise,latency_ms,prompt_hash,cache_hit) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (db.utc_now(), run_id, "finance_tool_select", call.model, call.tokens_in,
                     call.tokens_out, call.cost_paise, call.latency_ms,
                     call.prompt_hash or "0" * 64, int(call.cache_hit)),
                )
    finally:
        conn.close()


def ask_finance(run_dir: Path, database_path: Path, question: str,
                llm_mode: str = "mock") -> dict[str, Any]:
    """Route a question to one deterministic tool; model prose never reaches output."""
    if not question.strip() or len(question) > 2_000:
        return FinanceTools.refusal("Provide one finance question of at most 2,000 characters.")
    tools = FinanceTools(run_dir, database_path)
    selection: tuple[str, dict[str, str]] | None = None
    call = None
    routing_mode = "deterministic"
    routing_note = "keyword_and_identifier_router"
    if llm_mode == "live":
        client = LiveLLM()
        try:
            raw = client.complete_json("finance_tool_select", _selection_prompt(question))
            call = client.last_call
            selection = _validated_selection(raw, tools)
            routing_mode = "live_llm"
            routing_note = "allow_list_validated" if selection else "invalid_selection_refused"
        except RuntimeError:
            selection = _validated_selection(deterministic_route(question), tools)
            routing_mode = "deterministic_fallback"
            routing_note = "live_provider_unavailable"
            call = getattr(client, "last_call", None)
    elif llm_mode == "mock":
        selection = _validated_selection(deterministic_route(question), tools)
    else:
        raise ValueError("llm_mode must be 'mock' or 'live'")

    if selection is None:
        result = tools.refusal("The question is outside Milaan's verified finance tool scope.")
        routing = {"mode": routing_mode, "selected_tool": None, "note": routing_note}
    else:
        tool, arguments = selection
        result = tools.registry[tool](**arguments)
        routing = {"mode": routing_mode, "selected_tool": tool,
                   "arguments": arguments, "note": routing_note}
    result["routing"] = routing
    # The reconciliation run id records its original LLM mode. Resolve that id
    # from the database instead of trusting the question-mode label.
    with db.connect(database_path) as conn:
        stored_run = conn.execute("SELECT run_id FROM runs").fetchone()[0]
    _record_query(database_path, stored_run, question, routing, call)
    return result
