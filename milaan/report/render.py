"""Render one self-contained, printable HTML reconciliation report."""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template

from milaan.agent.tools import FinanceTools
from milaan.config import repository_root
from milaan.db import connect


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _money(paise: int) -> str:
    sign = "−" if paise < 0 else ""
    return f"{sign}₹{abs(paise) / 100:,.2f}"


def _ratio_card(label: str, metric: dict) -> str:
    return (
        f'<div class="card"><div class="muted">{_e(label)}</div>'
        f'<div class="value">{metric["rate"]:.2%}</div>'
        f'<div>{metric["numerator"]}/{metric["denominator"]}</div></div>'
    )


def _money_card(label: str, paise: int, note: str = "") -> str:
    return (
        f'<div class="card"><div class="muted">{_e(label)}</div>'
        f'<div class="value">{_money(paise)}</div><div>{_e(note)}</div></div>'
    )


def _highlight(narration: str, start: int, end: int) -> str:
    return _e(narration[:start]) + "<mark>" + _e(narration[start:end]) + "</mark>" + _e(narration[end:])


def render_report(run_dir: Path, database_path: Path, out: Path) -> str:
    metrics = json.loads((run_dir / "functional_metrics.json").read_text(encoding="utf-8"))
    telemetry_path = run_dir / "runtime_telemetry.json"
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8")) if telemetry_path.exists() else {}
    finance_tools = FinanceTools(run_dir, database_path)
    verified_questions = [
        ("What is our cash position?", finance_tools.get_cash_position()),
        ("What is the honest match coverage?", finance_tools.get_match_metrics()),
        ("Which settlement exposure is blocked?", finance_tools.get_blocked_exposure()),
    ]
    conn = connect(database_path)
    try:
        run = conn.execute("SELECT * FROM runs").fetchone()
        match_rows = conn.execute(
            "SELECT plane,tier,count(*) AS n FROM matches GROUP BY plane,tier ORDER BY plane,tier"
        ).fetchall()
        exceptions = conn.execute("SELECT * FROM exceptions ORDER BY id").fetchall()
        proof = conn.execute(
            """SELECT m.match_id,m.evidence,b.settlement_id,b.amount_paise,b.processed_at,
                      r.line_id,r.credit_paise,r.narration
               FROM matches m
               JOIN match_members mb ON mb.match_id=m.match_id AND mb.entity_type='BATCH'
               JOIN settlement_batches b ON b.settlement_id=mb.entity_id
               JOIN match_members ml ON ml.match_id=m.match_id AND ml.entity_type='BANK_LINE'
               JOIN raw_bank r ON r.line_id=ml.entity_id
               WHERE m.plane='B' ORDER BY m.match_id LIMIT 1"""
        ).fetchone()
        b2 = conn.execute(
            """SELECT m.evidence,r.narration FROM matches m
               JOIN match_members ml ON ml.match_id=m.match_id AND ml.entity_type='BANK_LINE'
               JOIN raw_bank r ON r.line_id=ml.entity_id
               WHERE m.tier='B2'
               ORDER BY CASE WHEN json_extract(m.evidence,'$.recovery.kind')='CORRUPTED' THEN 0 ELSE 1 END,
                        m.match_id LIMIT 1"""
        ).fetchone()
        multi = conn.execute(
            """SELECT substr(processed_at,1,10) AS day,group_concat(settlement_id) AS ids,count(*) AS n
               FROM settlement_batches GROUP BY substr(processed_at,1,10)
               HAVING count(*)>1 ORDER BY day LIMIT 1"""
        ).fetchone()

        parts = [
            '<div class="eyebrow">AI Finance Controller · verified run</div>',
            '<h1>Milaan reconciliation report</h1>',
            f'<p class="muted">Seed {_e(metrics["seed"])} · profile {_e(metrics["profile"])} · '
            f'generator {_e(metrics["generator_version"])} · run {_e(run["run_id"])}</p>',
            '<p><strong>Deterministic code owns every rupee. AI investigates verified evidence.</strong> '
            'All functional results below come from deterministic rules and database constraints.</p>',
            '<h2>Measured correctness</h2><div class="grid">',
            _ratio_card("Plane A expected-match recall", metrics["planes"]["A"]["expected_match_recall"]),
            _ratio_card("Plane A match precision", metrics["planes"]["A"]["match_precision"]),
            _ratio_card("Plane B expected-match recall", metrics["planes"]["B"]["expected_match_recall"]),
            _ratio_card("Plane B match precision", metrics["planes"]["B"]["match_precision"]),
            _ratio_card("Exception recall", metrics["exceptions"]["recall"]),
            _ratio_card("Exception precision", metrics["exceptions"]["precision"]),
            _ratio_card("Source-record conservation", metrics["source_record_conservation"]),
            f'<div class="card"><div class="muted">False matches</div><div class="value">{metrics["false_match_count"]}</div>'
            '<div>on this named synthetic benchmark</div></div></div>',
            '<p class="muted">Expected-match recall measures labelled benchmark accuracy. '
            'Workload coverage below measures how much of the complete operational workload was auto-resolved.</p>',
            '<h2>Honest workload coverage</h2><div class="grid">',
            _ratio_card("Eligible orders auto-matched", metrics["workload_coverage"]["plane_a_orders"]),
            _ratio_card("Gateway payments auto-matched", metrics["workload_coverage"]["plane_a_payments"]),
            _ratio_card("Settlement batches banked", metrics["workload_coverage"]["plane_b_settlement_batches"]),
            _ratio_card("Bank lines explained by a match", metrics["workload_coverage"]["plane_b_bank_lines"]),
            '</div>',
            '<h2>Cash position</h2><div class="grid">',
            _money_card("Banked", metrics["cash_position"]["banked_paise"], "verified batch-to-bank matches"),
            _money_card("Expected but unbanked", metrics["cash_position"]["expected_unbanked_paise"], "missing bank evidence"),
            _money_card("Blocked settlements", metrics["cash_position"]["blocked_settlement_paise"], "requires finance review"),
            _money_card("Unexplained bank credits", metrics["cash_position"]["unexplained_bank_credit_paise"], "not auto-posted"),
            _money_card("Gross evidence under attention", metrics["cash_position"]["gross_attention_paise"], "not a net loss estimate"),
            '</div>',
            '<p class="muted">Cash buckets are code-derived from matched and unresolved records. '
            'Gross evidence under attention may contain both sides of an ambiguous item and must not be read as financial loss.</p>',
            '<h2>Control integrity</h2><div class="grid">',
            f'<div class="card"><div class="muted">Input hashes</div><div class="value">'
            f'{"PASS" if metrics["input_integrity"]["all_match"] else "FAIL"}</div>'
            '<div>manifest is bound to evaluated CSV files</div></div>',
            f'<div class="card"><div class="muted">Amount conservation</div><div class="value">'
            f'{"PASS" if metrics["amount_conservation"]["balanced"] else "FAIL"}</div>'
            f'<div>delta {_money(metrics["amount_conservation"]["delta_paise"])}</div></div>',
            f'<div class="card"><div class="muted">Throughput</div><div class="value">'
            f'{float(telemetry.get("source_records_per_second", 0)):,.0f}/s</div>'
            f'<div>{int(telemetry.get("source_records", 0)):,} source records · '
            f'{int(telemetry.get("wall_ms", 0)):,} ms</div></div></div>',
            '<h2>Tier coverage</h2><table><thead><tr><th>Plane</th><th>Tier</th><th>Matches</th></tr></thead><tbody>',
        ]
        parts.extend(f'<tr><td>{_e(row["plane"])}</td><td>{_e(row["tier"])}</td><td>{row["n"]}</td></tr>'
                     for row in match_rows)
        parts.append('</tbody></table>')

        if proof:
            members = conn.execute(
                "SELECT txn_id,txn_type,net_paise FROM raw_txns WHERE settlement_id=? ORDER BY txn_id",
                (proof["settlement_id"],),
            ).fetchall()
            member_rows = "".join(
                f'<tr><td><code>{_e(row["txn_id"])}</code></td><td>{_e(row["txn_type"])}</td><td>{_money(row["net_paise"])}</td></tr>'
                for row in members[:25]
            )
            parts.extend([
                '<h2>Proof drill-down: signed members → batch → credit</h2>',
                '<div class="evidence"><div class="card">',
                f'<h3><code>{_e(proof["settlement_id"])}</code></h3><table><tr><th>Member</th><th>Type</th><th>Signed net</th></tr>{member_rows}</table>',
                f'<p><strong>Σ signed nets:</strong> {_money(proof["amount_paise"])}</p></div>',
                f'<div class="card"><h3><code>{_e(proof["line_id"])}</code></h3>'
                f'<p><strong>Bank credit:</strong> {_money(proof["credit_paise"])}</p>'
                f'<p><strong>Δ:</strong> {_money(proof["credit_paise"] - proof["amount_paise"])}</p>'
                f'<p>{_e(proof["narration"])}</p></div></div>',
            ])

        if b2:
            evidence = json.loads(b2["evidence"]); recovery = evidence["recovery"]
            parts.extend([
                '<h2>Deterministic B2 recovery</h2><div class="card">',
                f'<p><strong>{_e(recovery["kind"])}</strong> recovery for '
                f'<code>{_e(recovery["settlement_id"])}</code>; confidence is reporting metadata, not acceptance.</p>',
                f'<p>{_highlight(b2["narration"], int(recovery["start"]), int(recovery["end"]))}</p>',
                f'<p class="muted">Code-derived span [{recovery["start"]}, {recovery["end"]}); '
                f'substitution position {_e(recovery.get("substitution_position"))}.</p></div>',
            ])

        if multi:
            parts.extend([
                '<h2>Grouping proof: two settlements, one processing date</h2><div class="card">',
                f'<p><strong>{_e(multi["day"])}</strong> contains {multi["n"]} separate batches: '
                f'<code>{_e(multi["ids"])}</code>.</p>',
                '<p>They remain separate because membership is keyed by <code>settlement_id</code>, never inferred from date.</p></div>',
            ])

        parts.extend([
            '<h2>Ask Milaan · bounded finance investigation</h2>',
            '<p class="muted">In live mode, the model may select one allow-listed read-only tool. '
            'Every amount and answer below is produced by deterministic code; model prose cannot create a fact or posting.</p>',
        ])
        for question, result in verified_questions:
            evidence = ", ".join(str(value) for value in result["evidence_ids"][:8])
            parts.extend([
                '<div class="card">',
                f'<h3>{_e(question)}</h3><p>{_e(result["answer"])}</p>',
                f'<p class="muted">Tool: <code>{_e(result["tool"])}</code> · Evidence: {_e(evidence)}</p>',
                '</div>',
            ])

        parts.append(f'<h2>Exceptions ({len(exceptions)})</h2>')
        if not exceptions:
            parts.append('<p class="pass">No exceptions in this run.</p>')
        for row in exceptions:
            parts.extend([
                '<details><summary>', _e(row["reason_code"]), ' · ', _e(row["scope_ids"]), '</summary>',
                f'<p>{_e(row["narrative"])}</p><p>{_e(row["guidance"])}</p>',
                f'<p><strong>Next action:</strong> {_e(row["suggested_action"])}</p>',
                f'<pre>{_e(json.dumps(json.loads(row["evidence"]), indent=2, sort_keys=True))}</pre></details>',
            ])

        parts.extend([
            '<h2>Runtime telemetry <span class="muted">(not a functional metric)</span></h2>',
            f'<div class="card"><pre>{_e(json.dumps(telemetry, indent=2, sort_keys=True))}</pre></div>',
            f'<footer>Input hashes: {_e(json.dumps(metrics["input_hashes"], sort_keys=True))}</footer>',
        ])
    finally:
        conn.close()

    template = Template((repository_root() / "milaan" / "report" / "templates" / "report.html.j2").read_text())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.safe_substitute(content="".join(parts)), encoding="utf-8")
    return f"report written: {out} ({out.stat().st_size} bytes)"
