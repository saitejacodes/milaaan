"""The one deterministic reconciliation sequence. No model can affect it."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from milaan import db
from milaan.audit import event
from milaan.config import load_fees, load_timing, repository_root
from milaan.engine.plane_a import match_plane_a
from milaan.engine.plane_b import match_b0_b1
from milaan.engine.recovery import apply_recovery, collect_recovery_hits, deferred_bank_ids
from milaan.exceptions.actions import with_canonical_language
from milaan.exceptions.triage import triage_plane_b
from milaan.llm.polish import polish_exceptions
from milaan.ingest.aggregate import aggregate_batches
from milaan.ingest.normalize import normalize_inputs


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository_root(), text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def run_pipeline(data_dir: Path, database_path: Path, llm_mode: str = "mock") -> str:
    started = time.perf_counter()
    stage_ms: dict[str, int] = {}
    meta = json.loads((data_dir / "run_meta.json").read_text(encoding="utf-8"))
    seed, profile = int(meta["seed"]), str(meta["profile"])
    run_id = f"{seed}-{profile}-{llm_mode}"
    fees, timing = load_fees(), load_timing()
    hashes = {
        "orders": _sha256(data_dir / "orders.csv"),
        "txns": _sha256(data_dir / "gateway_recon.csv"),
        "bank": _sha256(data_dir / "bank.csv"),
        "fees": _sha256(repository_root() / "config" / "fees.toml"),
        "timing": _sha256(repository_root() / "config" / "timing.toml"),
    }

    conn = db.create_fresh(database_path)
    try:
        db.insert_run(conn, run_id=run_id, seed=seed, profile=profile,
                      llm_mode=llm_mode, hashes=hashes, git_sha=_git_sha())
        event(conn, run_id, "run_started", {"seed": seed, "profile": profile, "llm_mode": llm_mode})
        conn.commit()

        mark = time.perf_counter()
        ingested = normalize_inputs(data_dir, fees)
        aggregated = aggregate_batches(ingested)
        stage_ms["ingest_aggregate"] = round((time.perf_counter() - mark) * 1000)
        with conn:
            db.insert_orders(conn, run_id, ingested.orders)
            db.insert_txns(conn, run_id, ingested.txns)
            db.insert_bank(conn, run_id, ingested.bank)
            db.insert_batches(conn, run_id, aggregated.batches)
            conn.executemany(
                """INSERT INTO quarantine_rows(run_id,source,source_row_id,reason,settlement_id,raw_json)
                   VALUES(?,?,?,?,?,?)""",
                ((run_id, row.source, row.source_row_id, row.reason, row.settlement_id,
                  db.dumps(row.raw)) for row in ingested.quarantined),
            )
            event(conn, run_id, "ingest_completed", {
                "orders": len(ingested.orders), "txns": len(ingested.txns),
                "bank_lines": len(ingested.bank), "batches": len(aggregated.batches),
                "quarantined": len(ingested.quarantined),
            })

        mark = time.perf_counter()
        plane_a = match_plane_a(ingested.orders, ingested.txns, timing.window_a_bd)
        for decision in plane_a.decisions:
            db.insert_decision(conn, run_id, decision)
        stage_ms["plane_a"] = round((time.perf_counter() - mark) * 1000)

        mark = time.perf_counter()
        recovery_hits = collect_recovery_hits(
            aggregated.batches, ingested.bank, timing.window_b_bd
        )
        plane_b = match_b0_b1(
            aggregated.batches, ingested.bank, timing.window_b_bd, timing.tol_b_paise,
            initially_blocked_batches=aggregated.blocked_settlement_ids,
            deferred_bank_ids=deferred_bank_ids(recovery_hits),
        )
        apply_recovery(plane_b, aggregated.batches, ingested.bank, recovery_hits,
                       timing.window_b_bd)
        for decision in plane_b.decisions:
            db.insert_decision(conn, run_id, decision)
        stage_ms["plane_b"] = round((time.perf_counter() - mark) * 1000)

        residual = triage_plane_b(
            aggregated.batches, ingested.bank, plane_b, timing.window_b_bd,
            ingested.exceptions + aggregated.exceptions,
        )
        all_exceptions = (
            ingested.exceptions + aggregated.exceptions + plane_a.exceptions + plane_b.exceptions
            + residual
        )
        all_exceptions = [with_canonical_language(item) for item in all_exceptions]
        all_exceptions = polish_exceptions(conn, run_id, all_exceptions, llm_mode)
        for item in all_exceptions:
            db.insert_exception(conn, run_id, item)
        event(conn, run_id, "run_completed", {
            "plane_a_matches": len(plane_a.decisions),
            "plane_b_matches": len(plane_b.decisions),
            "exceptions": len(all_exceptions),
        })
        conn.commit()
    finally:
        conn.close()

    telemetry = {
        "seed": seed, "profile": profile, "llm_mode": llm_mode,
        "wall_ms": round((time.perf_counter() - started) * 1000),
        "stage_ms": stage_ms,
    }
    (data_dir / "runtime_telemetry.json").write_text(
        json.dumps(telemetry, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return (
        f"run={run_id} Plane-A={len(plane_a.decisions)} Plane-B={len(plane_b.decisions)} "
        f"exceptions={len(all_exceptions)} db={database_path}"
    )
