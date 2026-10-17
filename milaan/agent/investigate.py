"""Multi-step, read-only investigation over the verified run.

``ask_finance`` answers one question with one tool. An operator's real
questions are often compound ("Why is cash blocked, and what is the biggest
exception?") or need a drill-down: the blocked-exposure list names a
settlement, the settlement names an exception, the exception names the next
action. This module plans and runs that chain.

The safety model is unchanged and deliberately simple:

* The authority boundary runs once, on the whole question, before planning.
* Every step is one allow-listed read-only tool with validated arguments; the
  planner can only choose from the same registry as ``ask_finance``.
* Follow-up steps take their arguments from identifiers the *previous tool
  returned from the database*, never from model or user prose.
* The chain is bounded (``MAX_STEPS``) and never repeats a step.

Every step is returned in a visible trace: which tool, with which arguments,
why it was chosen, what it answered and which evidence it cited.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from milaan import db
from milaan.agent.router import (
    _validated_selection, authority_boundary, select_tool,
)
from milaan.agent.tools import FinanceTools
from milaan.llm.live import LiveLLM


MAX_STEPS = 6
SETTLEMENT_ID_RE = re.compile(r"^setl_[A-Za-z0-9_-]+$")

# Split a compound question into parts. A part is only kept if it routes to a
# tool on its own, so "precision and recall" stays one question.
_PART_SPLIT_RE = re.compile(r"\?|;|\b(?:and then|and also|and|then|also|plus)\b", re.IGNORECASE)
# Words that ask Milaan to dig below the first answer.
_DRILL_RE = re.compile(
    r"\b(?:why|biggest|largest|top|worst|root cause|drill|details?|investigate|explain|"
    r"what should|next action|close the books|books ready)\b",
    re.IGNORECASE,
)


def _step_key(tool: str, arguments: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return tool, tuple(sorted(arguments.items()))


def _call_record(call: Any) -> dict[str, Any] | None:
    """What the trace shows about one model call. Never the key or the prompt."""
    if call is None:
        return None
    return {"model": call.model, "tokens_in": call.tokens_in, "tokens_out": call.tokens_out,
            "latency_ms": call.latency_ms, "cache_hit": call.cache_hit}


def _plan_parts(question: str, tools: FinanceTools, llm_mode: str,
                client: LiveLLM | None = None,
                ) -> tuple[list[tuple[str, dict[str, str], str]], dict[str, Any]]:
    """Route each independent part of the question to one tool."""
    parts = [part.strip(" ,.") for part in _PART_SPLIT_RE.split(question)]
    parts = [part for part in parts if len(part) >= 3]
    planned: list[tuple[str, dict[str, str], str]] = []
    routing: dict[str, Any] = {"mode": "deterministic", "notes": [], "unanswered_parts": [],
                               "llm_calls": []}
    candidates = parts if len(parts) > 1 else []
    for part in candidates:
        selection, mode, note, call = select_tool(part, tools, llm_mode, client)
        routing["mode"], _ = mode, routing["notes"].append(note)
        if (record := _call_record(call)) is not None:
            routing["llm_calls"].append(record)
        if selection is not None:
            planned.append((*selection, f'answers the part "{part}"'))
        else:
            routing["unanswered_parts"].append(part)
    if not planned:
        # A single question, or parts that only make sense together.
        selection, mode, note, call = select_tool(question, tools, llm_mode, client)
        routing["mode"], _ = mode, routing["notes"].append(note)
        if (record := _call_record(call)) is not None:
            routing["llm_calls"].append(record)
        routing["unanswered_parts"] = []
        if selection is not None:
            planned.append((*selection, "answers the question"))
    return planned, routing


def _follow_up(tool: str, result: dict[str, Any]) -> tuple[str, dict[str, str], str] | None:
    """Derive the next read-only step from evidence the last tool returned."""
    facts = result.get("facts") or {}
    if tool == "get_blocked_exposure" and facts.get("items"):
        top = facts["items"][0]
        return ("get_exception", {"exception_id": str(top["exception_id"])},
                f"drill into the largest blocked item ({top['settlement_id']}, {top['amount']})")
    if tool == "list_unexplained_bank_credits" and facts.get("items"):
        top = facts["items"][0]
        return ("explain_why_unmatched", {"entity_id": str(top["line_id"])},
                f"explain the largest unexplained credit ({top['line_id']}, {top['amount']})")
    if tool == "summarize_exception_queue" and facts.get("by_reason_code"):
        group = facts["by_reason_code"][0]
        return ("get_exception", {"exception_id": str(group["exception_ids"][0])},
                f"open the first exception in the top-ranked group ({group['reason_code']})")
    if tool == "get_cash_position" and int(facts.get("blocked_settlement_paise", 0)) > 0:
        return ("get_blocked_exposure", {},
                "cash is blocked, so find which settlements a control stopped")
    if tool in {"trace_order", "trace_settlement", "explain_why_unmatched"} and facts.get("exceptions"):
        first = facts["exceptions"][0]
        return ("get_exception", {"exception_id": str(first["exception_id"])},
                f"read the exception that holds this record ({first['reason_code']})")
    if tool == "get_exception":
        settlements = [value for value in facts.get("scope_ids", [])
                       if SETTLEMENT_ID_RE.match(str(value))]
        if settlements:
            return ("trace_settlement", {"settlement_id": settlements[0]},
                    f"trace the settlement named in the exception ({settlements[0]})")
    return None


def investigate(run_dir: Path, database_path: Path, question: str,
                llm_mode: str = "mock", max_steps: int = MAX_STEPS,
                llm_client: LiveLLM | None = None) -> dict[str, Any]:
    """Answer a question with a bounded chain of read-only tool calls."""
    if not question.strip() or len(question) > 2_000:
        refusal = FinanceTools.refusal("Provide one finance question of at most 2,000 characters.")
        return {**refusal, "question": question, "steps": [], "routing": {"mode": "input_check"}}
    if llm_mode not in {"mock", "live"}:
        raise ValueError("llm_mode must be 'mock' or 'live'")
    tools = FinanceTools(run_dir, database_path)

    boundary = authority_boundary(question)
    if boundary is not None:
        result = {**tools.refusal(boundary), "question": question, "steps": [],
                  "routing": {"mode": "authority_boundary", "selected_tool": None,
                              "note": "read_only_boundary_enforced"}}
        _record(database_path, question, result)
        return result

    planned, routing = _plan_parts(question, tools, llm_mode, llm_client)
    if not planned:
        result = {**tools.refusal("The question is outside Milaan's verified finance tool scope."),
                  "question": question, "steps": [], "routing": routing}
        _record(database_path, question, result)
        return result

    drill = bool(_DRILL_RE.search(question))
    queue = list(planned)
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    steps: list[dict[str, Any]] = []
    while queue and len(steps) < max_steps:
        tool, arguments, reason = queue.pop(0)
        key = _step_key(tool, arguments)
        if key in seen:
            continue
        # Every step, including follow-ups, goes through the same validator
        # the model's selection does.
        if _validated_selection({"tool": tool, "arguments": arguments}, tools) is None:
            continue
        seen.add(key)
        result = tools.registry[tool](**arguments)
        steps.append({
            "step": len(steps) + 1, "tool": tool, "arguments": arguments,
            "reason": reason, "status": result["status"], "answer": result["answer"],
            "evidence_ids": result["evidence_ids"], "facts": result.get("facts", {}),
        })
        if drill and result["status"] == "ok":
            follow = _follow_up(tool, result)
            if follow is not None:
                # Depth first: the drill-down runs before the next planned part.
                queue.insert(0, follow)

    evidence: list[str] = []
    for step in steps:
        evidence.extend(value for value in step["evidence_ids"] if value not in evidence)
    answered = [step for step in steps if step["status"] == "ok"]
    result = {
        "status": "ok" if answered else "refused",
        "tool": "investigate",
        "question": question,
        "answer": "\n".join(
            [f"{step['step']}. {step['answer']}" for step in steps]
            + [f'Not answered (outside the read-only tool scope): "{part}"'
               for part in routing["unanswered_parts"]]
        ),
        "steps": steps,
        "evidence_ids": evidence,
        "facts": {"tool_sequence": [step["tool"] for step in steps],
                  "unanswered_parts": routing["unanswered_parts"],
                  "drill_down": drill, "max_steps": max_steps},
        "routing": {**routing, "selected_tool": "investigate",
                    "tool_sequence": [step["tool"] for step in steps]},
    }
    _record(database_path, question, result)
    return result


def render_trace(result: dict[str, Any]) -> str:
    """Plain-text trace of an investigation, one block per tool call."""
    lines = [f"Q: {result['question']}", f"Status: {result['status']}"]
    if not result["steps"]:
        lines.append(f"Answer: {result['answer']}")
    for step in result["steps"]:
        arguments = ", ".join(f"{key}={value}" for key, value in step["arguments"].items())
        lines += [
            "",
            f"Step {step['step']} · {step['tool']}({arguments})",
            f"  why:      {step['reason']}",
            f"  answer:   {step['answer']}",
            f"  evidence: {', '.join(map(str, step['evidence_ids'][:8]))}"
            + (" …" if len(step["evidence_ids"]) > 8 else ""),
        ]
    for part in result.get("facts", {}).get("unanswered_parts", []):
        lines += ["", f'Not answered (outside the read-only tool scope): "{part}"']
    return "\n".join(lines)


def _record(database_path: Path, question: str, result: dict[str, Any]) -> None:
    """Append one audit row. Accounting tables are never touched."""
    conn = db.connect(database_path)
    try:
        with conn:
            run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
            db.audit(conn, run_id, "finance_agent", "investigation", {
                "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
                "status": result["status"],
                "tool_sequence": [step["tool"] for step in result.get("steps", [])],
                "routing_mode": result.get("routing", {}).get("mode"),
            })
    finally:
        conn.close()
