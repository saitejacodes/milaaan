"""Render one self-contained, printable HTML reconciliation report.

Every figure comes from :mod:`milaan.view`, the same view model the Streamlit
dashboard reads, which in turn reads only the evaluator's published metrics and
the verified run database. Nothing financial is computed in this file.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template

from milaan.agent.tools import FinanceTools
from milaan.config import repository_root
from milaan.view import (
    ATTENTION_DISCLAIMER, COVERAGE_DISCLAIMER, EvidenceTrace, ExceptionView, View,
    build_view, money,
)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _card(label: str, value: str, note: str = "", tone: str = "") -> str:
    css = f' class="value {tone}"' if tone else ' class="value"'
    return (
        f'<div class="card"><div class="muted">{_e(label)}</div>'
        f'<div{css}>{_e(value)}</div><div class="note">{_e(note)}</div></div>'
    )


def _headline(view: View) -> list[str]:
    head = view.headline
    return [
        '<div class="eyebrow">Track 04 · AI Finance Controller</div>',
        "<h1>Milaan</h1>",
        '<p class="lede">One finance-operations loop, closed automatically where the '
        "evidence is defensible — and reported honestly where it is not.</p>",
        f'<p class="loop">{_e(head["loop"])}</p>',
        '<div class="grid">',
        _card("Physical source records", f"{head['physical_source_records']:,}",
              "orders + gateway rows + bank lines"),
        _card("Orders", f"{head['orders']:,}", f"{head['eligible_orders']:,} eligible"),
        _card("Settlement batches", f"{head['settlement_batches']:,}", "signed member sums"),
        _card("Bank lines", f"{head['bank_lines']:,}", "statement credits"),
        _card("False matches", str(view.metrics["false_match_count"]),
              "against independently rebuilt truth",
              "good" if view.metrics["false_match_count"] == 0 else "bad"),
        _card("Throughput", f"{view.throughput['records_per_second']:,.0f}/s",
              f"{view.throughput['records']:,} records in {view.throughput['wall_ms']:,} ms"),
        "</div>",
        f'<p class="muted">Seed {_e(view.metrics["seed"])} · profile '
        f'{_e(view.metrics["profile"])} · generator {_e(view.metrics["generator_version"])} · '
        f'commit <code>{_e(str(head["git_sha"])[:12])}</code></p>',
        "<p><strong>Deterministic code owns every rupee. AI investigates verified "
        "evidence.</strong> No model can create a match, alter an amount, or change "
        "accounting state.</p>",
    ]


def _accuracy(view: View) -> list[str]:
    parts = [
        "<h2>Measured accuracy</h2>",
        '<p class="muted">Scored against ground truth the evaluator regenerates itself '
        "from the run's immutable generation inputs. The answer key shipped beside the "
        "data is checked, never trusted.</p>",
        "<table><thead><tr><th>Loop</th><th>Expected</th><th>Produced</th><th>Correct</th>"
        "<th>False</th><th>Precision</th><th>Recall</th></tr></thead><tbody>",
    ]
    for plane in view.accuracy:
        tone = "" if plane["false_matches"] == 0 else ' class="warning"'
        parts.append(
            f'<tr><td><strong>{_e(plane["label"])}</strong></td>'
            f'<td>{plane["expected"]:,}</td><td>{plane["produced"]:,}</td>'
            f'<td>{plane["correct"]:,}</td><td{tone}>{plane["false_matches"]}</td>'
            f'<td>{_e(plane["precision"]["percent"])}</td>'
            f'<td>{_e(plane["recall"]["percent"])}</td></tr>'
        )
    quality = view.exception_quality
    parts += [
        "</tbody></table>",
        "<h3>Exception quality</h3>",
        "<table><thead><tr><th>Expected</th><th>Detected</th><th>Correct</th><th>False</th>"
        "<th>Missed</th><th>Precision</th><th>Recall</th></tr></thead><tbody>"
        f'<tr><td>{quality["expected"]}</td><td>{quality["detected"]}</td>'
        f'<td>{quality["correct"]}</td><td>{quality["false"]}</td><td>{quality["missed"]}</td>'
        f'<td>{_e(quality["precision"]["percent"])}</td>'
        f'<td>{_e(quality["recall"]["percent"])}</td></tr></tbody></table>',
        '<p class="muted">Expected exceptions are matched to detected exceptions one to '
        "one. A single detected exception cannot satisfy two expected cases.</p>",
    ]
    return parts


def _coverage(view: View) -> list[str]:
    parts = [
        "<h2>Operational coverage</h2>",
        f'<p class="callout"><strong>{_e(COVERAGE_DISCLAIMER)}</strong> Accuracy and '
        "coverage are different questions and are reported separately on purpose.</p>",
        '<div class="grid">',
    ]
    parts += [
        _card(row["label"], row["percent"], row["fraction"]) for row in view.coverage
    ]
    parts.append("</div>")
    return parts


def _cash(view: View) -> list[str]:
    parts = ["<h2>Cash position</h2>", '<div class="grid">']
    parts += [_money_card(row) for row in view.cash]
    parts += ["</div>", f'<p class="muted">{_e(ATTENTION_DISCLAIMER)} Every figure is '
              "derived from matched and unresolved records by deterministic code.</p>"]
    if view.blocked_settlements:
        parts += [
            "<h3>Largest blocked settlements</h3>",
            "<table><thead><tr><th>Settlement</th><th>Amount</th><th>Reason</th>"
            "<th>Exception</th><th>Tainted</th></tr></thead><tbody>",
        ]
        parts += [
            f'<tr><td><code>{_e(row["settlement_id"])}</code></td><td>{_e(row["amount"])}</td>'
            f'<td>{_e(row["reason_code"])}</td><td><code>{_e(row["exception_id"])}</code></td>'
            f'<td>{"yes" if row["tainted"] else "no"}</td></tr>'
            for row in view.blocked_settlements[:10]
        ]
        parts.append("</tbody></table>")
    if view.unexplained_credits:
        parts += [
            "<h3>Unexplained bank credits</h3>",
            "<table><thead><tr><th>Bank line</th><th>Amount</th></tr></thead><tbody>",
        ]
        parts += [
            f'<tr><td><code>{_e(row["line_id"])}</code></td><td>{_e(row["amount"])}</td></tr>'
            for row in view.unexplained_credits[:10]
        ]
        parts.append("</tbody></table>")
    return parts


def _money_card(row: dict) -> str:
    tone = "bad" if row["paise"] and row["label"].startswith(
        ("Blocked", "Unexplained")) else "good" if row["label"] == "Verified banked" else ""
    return _card(row["label"], row["amount"], row["note"], tone)


ARROW = '<div class="arrow">&darr;</div>'


def _trace(trace: EvidenceTrace | None) -> list[str]:
    if trace is None:
        return []
    last = len(trace.steps) - 1
    steps = "".join(
        f'<div class="step"><div class="muted">{_e(step.label)}</div>'
        f'<div><code>{_e(step.entity_id)}</code></div>'
        f'<div class="value">{_e(money(step.amount_paise or 0))}</div>'
        f'<div class="note">{_e(step.detail)}</div></div>'
        + (ARROW if index < last else "")
        for index, step in enumerate(trace.steps)
    )
    members = "".join(
        f'<tr class="{"traced" if row.get("traced") else ""}">'
        f'<td><code>{_e(row["txn_id"])}</code></td><td>{_e(row["txn_type"])}</td>'
        f'<td>{_e(row["amount"])}</td></tr>'
        for row in trace.display_members
    )
    return [
        "<h2>Evidence trace: one order, followed to the bank</h2>",
        f'<div class="trace">{steps}</div>',
        f"<h3>Signed settlement members ({trace.member_count} in total)</h3>",
        '<p class="muted">Members settle at <strong>net</strong> — gross minus gateway '
        "fee and GST — which is why a member figure is smaller than the order it came "
        "from. The traced payment and every signed non-payment member are listed "
        "individually; the remaining ordinary payments are folded into one explicit "
        "aggregate row, so these rows still sum to the batch amount.</p>",
        f"<table><thead><tr><th>Member</th><th>Type</th><th>Signed net</th></tr></thead>"
        f"<tbody>{members}</tbody></table>",
        f'<p><strong>Σ signed members:</strong> {_e(money(trace.member_total_paise))} · '
        f'<strong>Bank credit:</strong> {_e(money(trace.bank_credit_paise))} · '
        f'<strong>Difference:</strong> {_e(money(trace.difference_paise))}</p>',
        f'<p class="{"pass" if trace.difference_paise == 0 else "warning"}">'
        f"STATUS: {_e(trace.status)}</p>",
    ]


def _exception_card(item: ExceptionView) -> str:
    flags = []
    if item.blocked:
        flags.append("blocked")
    if item.tainted:
        flags.append("tainted settlement")
    candidates = ("" if not item.candidates else
                  '<p><strong>Candidates considered:</strong> '
                  + ", ".join(f"<code>{_e(value)}</code>" for value in item.candidates[:8])
                  + "</p>")
    sources = ("" if not item.source_row_ids else
               '<p class="muted">Source rows: '
               + ", ".join(f"<code>{_e(value)}</code>" for value in item.source_row_ids[:8])
               + "</p>")
    guidance = "".join(f"<li>{_e(line)}</li>" for line in item.guidance)
    return (
        f"<details><summary><code>{_e(item.exception_id)}</code> · "
        f"{_e(item.headline)} · <strong>{_e(item.exposure)}</strong>"
        f'{" · " + _e(", ".join(flags)) if flags else ""}</summary>'
        f'<p class="muted">Reason code <code>{_e(item.reason_code)}</code></p>'
        f"<p><strong>What failed:</strong> {_e(item.headline)}</p>"
        f"<p><strong>Why Milaan abstained:</strong> {_e(item.explanation)}</p>"
        f"<p><strong>Money affected:</strong> {_e(item.exposure)}</p>"
        f"<p><strong>Affected records:</strong> "
        + ", ".join(f"<code>{_e(value)}</code>" for value in item.scope_ids[:10])
        + "</p>"
        f"{candidates}{sources}"
        f"<p><strong>What finance should do next:</strong> {_e(item.next_action)}</p>"
        f"<ul>{guidance}</ul>"
        f"<pre>{_e(json.dumps(item.evidence, indent=2, sort_keys=True))}</pre>"
        "</details>"
    )


def _exceptions(view: View) -> list[str]:
    parts = [
        f"<h2>Exceptions ({len(view.exceptions)})</h2>",
        '<p class="muted">Every unresolved record is listed. Nothing is hidden, '
        "collapsed into a total, or silently resolved.</p>",
    ]
    if not view.exceptions:
        parts.append('<p class="pass">No exceptions in this run.</p>')
    parts += [_exception_card(item) for item in
              sorted(view.exceptions, key=lambda x: (-x.exposure_paise, x.exception_id))]
    return parts


def _integrity(view: View) -> list[str]:
    rows = "".join(
        f'<tr><td>{_e(row["label"])}</td>'
        f'<td class="{"pass" if row["status"] in {"PASS", "0", "100.00%"} else ""}">'
        f'{_e(row["status"])}</td><td class="muted">{_e(row["detail"])}</td></tr>'
        for row in view.integrity
    )
    return [
        "<h2>Control integrity</h2>",
        "<table><thead><tr><th>Control</th><th>Result</th><th>What it proves</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>",
    ]


def _audit(view: View) -> list[str]:
    audit = view.audit
    actors = "".join(
        f"<tr><td>{_e(actor)}</td><td>{count:,}</td></tr>"
        for actor, count in audit.by_actor.items()
    )
    actions = "".join(
        f"<tr><td>{_e(action)}</td><td>{count:,}</td></tr>"
        for action, count in audit.by_action.items()
    )
    recent = "".join(
        f'<tr><td>{_e(row["ts"])}</td><td>{_e(row["actor"])}</td>'
        f'<td>{_e(row["action"])}</td><td><code>{_e(row["payload"])}</code></td></tr>'
        for row in audit.recent
    )
    verdict = ("Every match and exception in this run was written by the deterministic "
               "engine." if not audit.non_engine_actions else
               "A non-engine actor changed accounting state: "
               + ", ".join(audit.non_engine_actions))
    return [
        "<h2>Audit trail</h2>",
        f'<p class="{"pass" if not audit.non_engine_actions else "warning"}">{_e(verdict)}</p>',
        f'<p class="muted">{audit.total_events:,} recorded events · '
        f'{audit.llm_calls:,} language-model calls, none of which wrote anything.</p>',
        '<div class="evidence">',
        f"<div class=\"card\"><h3>By actor</h3><table><tr><th>Actor</th><th>Events</th></tr>{actors}</table></div>",
        f"<div class=\"card\"><h3>By action</h3><table><tr><th>Action</th><th>Events</th></tr>{actions}</table></div>",
        "</div>",
        "<h3>Most recent events</h3>",
        "<table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Payload</th>"
        f"</tr></thead><tbody>{recent}</tbody></table>",
    ]


def _tiers(view: View) -> list[str]:
    rows = "".join(
        f'<tr><td>{_e(row["plane"])}</td><td>{_e(row["tier"])}</td><td>{row["count"]:,}</td></tr>'
        for row in view.tiers
    )
    return [
        "<h2>Which rule produced each match</h2>",
        "<table><thead><tr><th>Loop</th><th>Rule</th><th>Matches</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
    ]


def _ask(view: View, run_dir: Path, database_path: Path) -> list[str]:
    tools = FinanceTools(run_dir, database_path)
    questions = [
        ("What is our cash position?", tools.get_cash_position()),
        ("What is the honest match coverage?", tools.get_match_metrics()),
        ("Which settlement exposure is blocked?", tools.get_blocked_exposure()),
        ("Summarise the exception queue", tools.summarize_exception_queue()),
        ("Delete all exceptions.",
         {"tool": None, "answer": _refusal_text(run_dir, database_path),
          "evidence_ids": []}),
    ]
    parts = [
        "<h2>Ask Milaan · bounded investigation</h2>",
        '<p class="muted">A model may select one allow-listed read-only tool. '
        "Deterministic code produces every answer and every amount below. The last "
        "row is a write request, refused before any model is consulted.</p>",
    ]
    for question, result in questions:
        evidence = ", ".join(str(value) for value in result["evidence_ids"][:8])
        parts.append(
            f'<div class="card"><h3>{_e(question)}</h3><p>{_e(result["answer"])}</p>'
            f'<p class="muted">Tool: <code>{_e(result["tool"])}</code>'
            f'{" · Evidence: " + _e(evidence) if evidence else ""}</p></div>'
        )
    return parts


def _refusal_text(run_dir: Path, database_path: Path) -> str:
    from milaan.agent.router import ask_finance

    return str(ask_finance(run_dir, database_path, "Delete all exceptions.", "mock")["answer"])


def render_report(run_dir: Path, database_path: Path, out: Path) -> str:
    view = build_view(run_dir, database_path)
    parts: list[str] = []
    parts += _headline(view)
    parts += _accuracy(view)
    parts += _coverage(view)
    parts += _cash(view)
    parts += _trace(view.trace)
    parts += _exceptions(view)
    parts += _integrity(view)
    parts += _audit(view)
    parts += _tiers(view)
    parts += _ask(view, run_dir, database_path)
    parts += [
        "<h2>Runtime telemetry "
        '<span class="muted">(performance, not a correctness metric)</span></h2>',
        f'<div class="card"><pre>{_e(json.dumps(view.telemetry, indent=2, sort_keys=True))}'
        "</pre></div>",
        f'<footer>Input hashes: {_e(json.dumps(view.metrics["input_hashes"], sort_keys=True))}'
        f'<br>Gate: {_e(view.metrics["gate"]["name"])} '
        f'{_e(view.metrics["gate"]["status"])} · metrics schema '
        f'{_e(view.metrics["schema_version"])}</footer>',
    ]

    template = Template(
        (repository_root() / "milaan" / "report" / "templates" / "report.html.j2").read_text()
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.safe_substitute(content="".join(parts)), encoding="utf-8")
    return f"report written: {out} ({out.stat().st_size} bytes)"
