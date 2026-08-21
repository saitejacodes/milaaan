"""Render one self-contained, printable HTML reconciliation report."""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template

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


def _highlight(narration: str, start: int, end: int) -> str:
    return _e(narration[:start]) + "<mark>" + _e(narration[start:end]) + "</mark>" + _e(narration[end:])


def render_report(run_dir: Path, database_path: Path, out: Path) -> str:
    metrics = json.loads((run_dir / "functional_metrics.json").read_text(encoding="utf-8"))
    telemetry_path = run_dir / "runtime_telemetry.json"
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8")) if telemetry_path.exists() else {}
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
            '<p><strong>The books are code. The model only explains them.</strong> '
            'All functional results below come from deterministic rules and database constraints.</p>',
            '<h2>Functional results</h2><div class="grid">',
            _ratio_card("Plane A auto-match", metrics["planes"]["A"]["auto_match"]),
            _ratio_card("Plane B auto-match", metrics["planes"]["B"]["auto_match"]),
            _ratio_card("Exception recall", metrics["exceptions"]["recall"]),
            _ratio_card("Exception precision", metrics["exceptions"]["precision"]),
            _ratio_card("Completeness", metrics["completeness"]),
            f'<div class="card"><div class="muted">False matches</div><div class="value">{metrics["false_match_count"]}</div>'
            '<div>on this named synthetic benchmark</div></div></div>',
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
