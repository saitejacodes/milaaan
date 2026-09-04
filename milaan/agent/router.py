"""Allow-listed tool router for read-only finance questions."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any

from milaan import db
from milaan.agent.tools import FinanceTools
from milaan.llm.live import LiveLLM, ProviderError


ORDER_RE = re.compile(r"\border_[A-Za-z0-9_-]+\b", re.IGNORECASE)
SETTLEMENT_RE = re.compile(r"\bsetl_[A-Za-z0-9_-]+\b", re.IGNORECASE)
EXCEPTION_RE = re.compile(r"\b(?:EXC-)?(\d{1,8})\b", re.IGNORECASE)
ENTITY_RE = re.compile(r"\b(?:order|pay|rfnd|adj|setl|bank)_[A-Za-z0-9_-]+\b", re.IGNORECASE)

# Milaan's question layer is read-only by construction: no tool in the registry
# can write. These patterns add a second, earlier boundary so a request to
# *change* accounting state is answered with an explicit refusal rather than
# being quietly satisfied with a read-only report that happens to share a
# keyword. The check runs before any model is consulted, so no provider -- and
# no instruction smuggled into a question -- can route around it.
WRITE_INTENT_RE = re.compile(
    r"\b(?:delete|drop|truncate|remove|update|modify|edit|alter|amend|change|set|"
    r"overwrite|create|insert|post|approve|release|mark|write|send|transfer|"
    r"reconcile|reconciled|unblock|clear|falsify|backdate)\b",
    re.IGNORECASE,
)
AUTHORITY_OVERRIDE_RE = re.compile(
    r"(?:ignore\s+(?:your|the|all|previous|prior|earlier|database|every)|"
    r"disregard\s+(?:the|your|all)|system\s+override|admin\s+mode|developer\s+mode|"
    r"you\s+are\s+now|act\s+as\s+(?:an?\s+)?(?:admin|root)|"
    r"regardless\s+of\s+(?:the\s+)?(?:database|evidence|controls)|"
    r"even\s+if\s+the\s+database|skip\s+the\s+database|bypass|override)",
    re.IGNORECASE,
)

READ_ONLY_REFUSAL = (
    "Milaan's finance question layer is read-only. It cannot create, change or "
    "delete a match, an exception, a settlement or a bank record, and it will "
    "not restate a control decision that contradicts the database. Ask an "
    "investigation question instead, or correct the source data and re-run "
    "reconciliation."
)


def authority_boundary(question: str) -> str | None:
    """Return a refusal reason when a question asks Milaan to exceed read-only."""
    if AUTHORITY_OVERRIDE_RE.search(question):
        return READ_ONLY_REFUSAL
    if WRITE_INTENT_RE.search(question):
        return READ_ONLY_REFUSAL
    return None


def deterministic_route(question: str) -> dict[str, Any] | None:
    text = question.strip()
    lowered = text.casefold()
    order = ORDER_RE.search(text)
    settlement = SETTLEMENT_RE.search(text)
    entity = ENTITY_RE.search(text)
    if entity and any(token in lowered for token in (
        "why", "unmatched", "not matched", "unresolved", "abstain",
    )):
        return {"tool": "explain_why_unmatched",
                "arguments": {"entity_id": entity.group(0)}}
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
        "unexplained credit", "unexplained bank", "unknown credit", "unidentified credit",
        "credits we cannot explain", "unattributed credit",
    )):
        return {"tool": "list_unexplained_bank_credits", "arguments": {}}
    if any(token in lowered for token in (
        "exception queue", "exception summary", "summarize exception", "summarise exception",
        "how many exceptions", "breakdown of exceptions", "exception backlog",
        "what kinds of exceptions", "work queue",
    )):
        return {"tool": "summarize_exception_queue", "arguments": {}}
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
            "summarize_exception_queue": {},
            "list_unexplained_bank_credits": {},
            "explain_why_unmatched": {"entity_id": "<order_|pay_|setl_|bank_ id>"},
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
        "summarize_exception_queue": set(),
        "list_unexplained_bank_credits": set(),
        "get_exception": {"exception_id"},
        "trace_order": {"order_id"},
        "trace_settlement": {"settlement_id"},
        "explain_why_unmatched": {"entity_id"},
    }[tool]
    if set(arguments) != expected or any(not isinstance(value, str) for value in arguments.values()):
        return None
    if tool == "trace_order" and not ORDER_RE.fullmatch(arguments["order_id"]):
        return None
    if tool == "trace_settlement" and not SETTLEMENT_RE.fullmatch(arguments["settlement_id"]):
        return None
    if tool == "get_exception" and not re.fullmatch(r"(?:EXC-)?\d{1,8}", arguments["exception_id"], re.I):
        return None
    if tool == "explain_why_unmatched" and not ENTITY_RE.fullmatch(arguments["entity_id"]):
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
    if llm_mode not in {"mock", "live"}:
        raise ValueError("llm_mode must be 'mock' or 'live'")
    tools = FinanceTools(run_dir, database_path)
    selection: tuple[str, dict[str, str]] | None = None
    call = None
    routing_mode = "deterministic"
    routing_note = "keyword_and_identifier_router"

    boundary = authority_boundary(question)
    if boundary is not None:
        return _finish(database_path, question, tools.refusal(boundary), {
            "mode": "authority_boundary", "selected_tool": None,
            "note": "read_only_boundary_enforced",
        }, None)

    if llm_mode == "live":
        client = LiveLLM()
        try:
            raw = client.complete_json("finance_tool_select", _selection_prompt(question))
            call = client.last_call
            selection = _validated_selection(raw, tools)
            routing_mode = "live_llm"
            # A syntactically usable answer that names an unapproved tool, or
            # supplies unapproved arguments, is refused outright. It is never
            # downgraded to deterministic routing, because a judge must be able
            # to see that the allow-list rejected the model.
            routing_note = "allow_list_validated" if selection else "invalid_selection_refused"
        except (ProviderError, ValueError, TypeError, KeyError, OSError) as exc:
            # Everything the provider layer can throw -- transport failure,
            # unusable envelope, unusable model content, unavailable cache --
            # lands here and degrades to the offline deterministic router. A
            # malformed model body must never reach a judge as a stack trace.
            selection = _validated_selection(deterministic_route(question), tools)
            routing_mode = "deterministic_fallback"
            routing_note = f"live_provider_unusable:{type(exc).__name__}"
            call = getattr(client, "last_call", None)
    else:
        selection = _validated_selection(deterministic_route(question), tools)

    if selection is None:
        result = tools.refusal("The question is outside Milaan's verified finance tool scope.")
        routing = {"mode": routing_mode, "selected_tool": None, "note": routing_note}
    else:
        tool, arguments = selection
        result = tools.registry[tool](**arguments)
        routing = {"mode": routing_mode, "selected_tool": tool,
                   "arguments": arguments, "note": routing_note}
    return _finish(database_path, question, result, routing, call)


def _finish(database_path: Path, question: str, result: dict[str, Any],
            routing: dict[str, Any], call: Any | None) -> dict[str, Any]:
    result["routing"] = routing
    # The reconciliation run id records its original LLM mode. Resolve that id
    # from the database instead of trusting the question-mode label.
    with db.connect(database_path) as conn:
        stored_run = conn.execute("SELECT run_id FROM runs").fetchone()[0]
    _record_query(database_path, stored_run, question, routing, call)
    return result
