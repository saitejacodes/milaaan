"""Independent reconstruction of benchmark ground truth.

The evaluator must never trust truth that ships beside the artefacts it scores.
``manifest.json`` lives in the run directory and is therefore attacker-writable:
anyone who can edit an evaluated run can edit the answer key next to it.

This module removes that trust.  ``run_meta.json`` carries only *immutable
generation inputs* -- seed, profile, requested record count and generator
version.  Evaluation regenerates the canonical dataset from those inputs with
the same deterministic generator, and scores the run against the truth it just
rebuilt.  Four independent bindings must all hold:

1. ``inputs``    -- the evaluated CSV files are byte-identical to the canonical
   regenerated CSV files.
2. ``database``  -- every source row inside the evaluated database is the
   canonical row, and no canonical row silently disappeared.
3. ``manifest``  -- the truth shipped in the run directory is identical to the
   truth just reconstructed.  A tampered answer key is *reported*, not merely
   ignored.
4. ``facts``     -- the reconstructed truth is complete: every expected match
   carries mandatory monetary and membership facts.

Every check fails closed.  There is no code path in which a missing or damaged
fact downgrades to a weaker comparison.
"""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from milaan.config import repository_root
from milaan.generator.emit import generate_to_directory
from milaan.generator.manifest import GENERATOR_VERSION


SOURCE_FILES = {
    "orders": "orders.csv",
    "gateway_recon": "gateway_recon.csv",
    "bank": "bank.csv",
}
PROFILES = ("clean", "mixed", "hard")
MINIMUM_RECORDS = 50

PLANE_A_FACT_FIELDS = ("order_id", "payment_gross_paise", "order_amount_paise")
PLANE_B_FACT_FIELDS = (
    "bank_line_id", "member_txn_ids", "batch_amount_paise", "bank_credit_paise",
)


class TruthIntegrityError(RuntimeError):
    """Raised when reconstructed truth cannot be bound to the evaluated run."""


@dataclass(frozen=True)
class CanonicalRun:
    """The canonical dataset and truth rebuilt from immutable generation inputs."""

    seed: int
    profile: str
    records: int
    generator_version: str
    manifest: dict[str, Any]
    input_hashes: dict[str, str]
    rows: dict[str, dict[str, dict[str, str]]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_hashes() -> dict[str, str]:
    """Hash the generation configuration that shapes the canonical dataset."""
    config = repository_root() / "config"
    return {name: sha256_file(config / f"{name}.toml") for name in ("fees", "timing")}


def _read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    keyed: dict[str, dict[str, str]] = {}
    for row in rows:
        source_row_id = row.get("source_row_id") or ""
        if not source_row_id or source_row_id in keyed:
            raise TruthIntegrityError(
                f"{path.name} does not carry unique source_row_id values"
            )
        keyed[source_row_id] = row
    return keyed


def read_run_meta(run_dir: Path) -> dict[str, Any]:
    """Load and strictly validate the immutable generation inputs for a run."""
    path = run_dir / "run_meta.json"
    if not path.is_file():
        raise TruthIntegrityError(f"run_meta.json is missing from {run_dir}")
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TruthIntegrityError(f"run_meta.json is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise TruthIntegrityError("run_meta.json must contain a JSON object")

    missing = {"seed", "profile", "records", "generator_version"} - set(meta)
    if missing:
        raise TruthIntegrityError(
            f"run_meta.json is missing generation inputs: {sorted(missing)}"
        )
    if not isinstance(meta["seed"], int) or isinstance(meta["seed"], bool):
        raise TruthIntegrityError("run_meta.json seed must be an integer")
    if meta["profile"] not in PROFILES:
        raise TruthIntegrityError(
            f"run_meta.json profile must be one of {list(PROFILES)}"
        )
    if not isinstance(meta["records"], int) or isinstance(meta["records"], bool):
        raise TruthIntegrityError("run_meta.json records must be an integer")
    if meta["records"] < MINIMUM_RECORDS:
        raise TruthIntegrityError(
            f"run_meta.json records must be at least {MINIMUM_RECORDS}"
        )
    if meta["generator_version"] != GENERATOR_VERSION:
        raise TruthIntegrityError(
            f"run_meta.json generator_version {meta['generator_version']!r} cannot be "
            f"reconstructed by installed generator {GENERATOR_VERSION!r}"
        )
    declared_config = meta.get("config_hashes")
    if declared_config is not None:
        current = config_hashes()
        if declared_config != current:
            raise TruthIntegrityError(
                "generation configuration changed since this run was generated; "
                f"declared={declared_config} current={current}"
            )
    return meta


def reconstruct(meta: dict[str, Any]) -> CanonicalRun:
    """Regenerate the canonical dataset and truth into a throwaway directory."""
    with tempfile.TemporaryDirectory(prefix="milaan-canonical-") as tmp:
        canonical_dir = Path(tmp) / "canonical"
        generate_to_directory(
            int(meta["records"]), int(meta["seed"]), str(meta["profile"]), canonical_dir
        )
        manifest = json.loads((canonical_dir / "manifest.json").read_text(encoding="utf-8"))
        hashes = {
            name: sha256_file(canonical_dir / filename)
            for name, filename in SOURCE_FILES.items()
        }
        rows = {
            name: _read_rows(canonical_dir / filename)
            for name, filename in SOURCE_FILES.items()
        }
    require_complete_truth(manifest)
    return CanonicalRun(
        seed=int(meta["seed"]),
        profile=str(meta["profile"]),
        records=int(meta["records"]),
        generator_version=str(meta["generator_version"]),
        manifest=manifest,
        input_hashes=hashes,
        rows=rows,
    )


def require_complete_truth(manifest: dict[str, Any]) -> None:
    """Fail closed unless every expected match carries mandatory monetary facts.

    There is deliberately no "fact missing, so trust the identifier pair"
    fallback: that fallback is precisely what allowed a tampered run to publish
    a passing score in earlier versions of this evaluator.
    """
    for key in ("expectations", "tier_labels", "match_facts"):
        if key not in manifest:
            raise TruthIntegrityError(f"truth is incomplete: {key} is missing")
    expectations = manifest["expectations"]
    for key in ("plane_a_matches", "plane_b_matches", "exceptions"):
        if not isinstance(expectations.get(key), list):
            raise TruthIntegrityError(f"truth is incomplete: expectations.{key} is missing")

    facts = manifest["match_facts"]
    if not isinstance(facts, dict) or set(facts) != {"A", "B"}:
        raise TruthIntegrityError("match_facts must declare exactly planes A and B")

    expected_a = [tuple(pair) for pair in expectations["plane_a_matches"]]
    expected_b = [tuple(pair) for pair in expectations["plane_b_matches"]]
    if len(set(expected_a)) != len(expected_a):
        raise TruthIntegrityError("duplicate Plane-A expectations")
    if len(set(expected_b)) != len(expected_b):
        raise TruthIntegrityError("duplicate Plane-B expectations")

    _require_plane_facts("A", expected_a, facts["A"], PLANE_A_FACT_FIELDS)
    _require_plane_facts("B", expected_b, facts["B"], PLANE_B_FACT_FIELDS)

    for payment_id, order_id in expected_a:
        fact = facts["A"][payment_id]
        if fact["order_id"] != order_id:
            raise TruthIntegrityError(
                f"Plane-A fact for {payment_id} contradicts the expected pair"
            )
        for field in ("payment_gross_paise", "order_amount_paise"):
            if not isinstance(fact[field], int) or isinstance(fact[field], bool):
                raise TruthIntegrityError(
                    f"Plane-A fact {payment_id}.{field} must be integer paise"
                )
    for settlement_id, bank_line_id in expected_b:
        fact = facts["B"][settlement_id]
        if fact["bank_line_id"] != bank_line_id:
            raise TruthIntegrityError(
                f"Plane-B fact for {settlement_id} contradicts the expected pair"
            )
        for field in ("batch_amount_paise", "bank_credit_paise"):
            if not isinstance(fact[field], int) or isinstance(fact[field], bool):
                raise TruthIntegrityError(
                    f"Plane-B fact {settlement_id}.{field} must be integer paise"
                )
        members = fact["member_txn_ids"]
        if not isinstance(members, list) or not members:
            raise TruthIntegrityError(
                f"Plane-B fact {settlement_id} must list its settlement members"
            )
        if len(set(members)) != len(members):
            raise TruthIntegrityError(
                f"Plane-B fact {settlement_id} repeats a settlement member"
            )
        if any(not isinstance(member, str) or not member for member in members):
            raise TruthIntegrityError(
                f"Plane-B fact {settlement_id} has a malformed member identifier"
            )

    _require_exception_truth(expectations["exceptions"])


def _require_plane_facts(plane: str, expected: list[tuple[str, ...]],
                         facts: Any, fields: tuple[str, ...]) -> None:
    if not isinstance(facts, dict):
        raise TruthIntegrityError(f"match_facts.{plane} must be an object")
    declared = set(facts)
    wanted = {pair[0] for pair in expected}
    if declared != wanted:
        missing, extra = sorted(wanted - declared), sorted(declared - wanted)
        raise TruthIntegrityError(
            f"match_facts.{plane} does not cover the expected matches exactly "
            f"(missing={missing[:5]} extra={extra[:5]})"
        )
    for key, fact in facts.items():
        if not isinstance(fact, dict):
            raise TruthIntegrityError(f"match_facts.{plane}.{key} must be an object")
        if set(fact) != set(fields):
            raise TruthIntegrityError(
                f"match_facts.{plane}.{key} must declare exactly {list(fields)}"
            )


def _require_exception_truth(expected: Any) -> None:
    if not isinstance(expected, list):
        raise TruthIntegrityError("expectations.exceptions must be a list")
    seen_scopes: set[frozenset[str]] = set()
    for item in expected:
        if not isinstance(item, dict) or not {"ids", "allowed_codes"} <= set(item):
            raise TruthIntegrityError(
                "every expected exception needs ids and allowed_codes"
            )
        ids = item["ids"]
        codes = item["allowed_codes"]
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids):
            raise TruthIntegrityError("expected exception ids must be non-empty strings")
        if not isinstance(codes, list) or not codes or any(not isinstance(x, str) or not x for x in codes):
            raise TruthIntegrityError("expected exception allowed_codes must be non-empty")
        scope = frozenset(ids)
        if len(scope) != len(ids):
            raise TruthIntegrityError(f"expected exception repeats an id: {sorted(ids)}")
        if scope in seen_scopes:
            raise TruthIntegrityError(
                f"duplicate expected exception scope: {sorted(scope)}"
            )
        seen_scopes.add(scope)


def verify_inputs(run_dir: Path, canonical: CanonicalRun) -> dict[str, Any]:
    """Bind the evaluated CSV files to the canonical regenerated dataset."""
    checks: dict[str, bool] = {}
    evaluated: dict[str, str] = {}
    for name, filename in SOURCE_FILES.items():
        path = run_dir / filename
        if not path.is_file():
            raise TruthIntegrityError(f"evaluated run is missing {filename}")
        evaluated[name] = sha256_file(path)
        checks[name] = evaluated[name] == canonical.input_hashes[name]
    failed = sorted(name for name, ok in checks.items() if not ok)
    if failed:
        raise TruthIntegrityError(
            "evaluated source files are not the canonical dataset for this "
            f"run_meta.json (mismatched: {failed})"
        )
    return {
        "checks": checks,
        "evaluated_input_hashes": evaluated,
        "canonical_input_hashes": dict(canonical.input_hashes),
        "all_match": True,
    }


def verify_declared_manifest(run_dir: Path, canonical: CanonicalRun) -> dict[str, Any]:
    """Report -- never silently tolerate -- a tampered answer key in the run."""
    path = run_dir / "manifest.json"
    if not path.is_file():
        raise TruthIntegrityError("manifest.json is missing from the evaluated run")
    try:
        declared = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TruthIntegrityError(f"manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(declared, dict):
        raise TruthIntegrityError("manifest.json must contain a JSON object")

    reference = dict(canonical.manifest)
    reference["input_hashes"] = dict(canonical.input_hashes)
    differing = sorted(
        key for key in set(reference) | set(declared)
        if declared.get(key) != reference.get(key)
    )
    if differing:
        raise TruthIntegrityError(
            "manifest.json does not match independently reconstructed truth "
            f"(differing keys: {differing})"
        )
    return {"matches_canonical": True, "compared_keys": sorted(reference)}


def _project_order(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["order_id"], row["created_at"], int(row["amount_paise"]),
        row["status"], row["channel"], row.get("payment_id") or "",
    )


def _project_txn(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["entity_id"], row["type"].upper(), row.get("order_id") or "",
        int(row["gross_paise"]), int(row["fee_paise"]), int(row["tax_paise"]),
        int(row["net_paise"]), row["captured_at"], row.get("settlement_id") or "",
        row.get("settlement_utr") or "", row.get("settlement_processed_at") or "",
    )


def _project_bank(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["line_id"], row["txn_date"], row["value_date"], row["narration"],
        int(row["credit_paise"]), int(row["debit_paise"]), int(row["balance_paise"]),
        row["ref_no"],
    )


def _project_db_order(row: Any) -> tuple[Any, ...]:
    return (
        row["order_id"], row["created_at"], int(row["amount_paise"]),
        row["status"], row["channel"], row["payment_id"] or "",
    )


def _project_db_txn(row: Any) -> tuple[Any, ...]:
    return (
        row["txn_id"], row["txn_type"], row["order_ref"] or "",
        int(row["gross_paise"]), int(row["fee_paise"]), int(row["tax_paise"]),
        int(row["net_paise"]), row["captured_at"], row["settlement_id"] or "",
        row["settlement_utr"] or "", row["settlement_processed_at"] or "",
    )


def _project_db_bank(row: Any) -> tuple[Any, ...]:
    return (
        row["line_id"], row["txn_date"], row["value_date"], row["narration"],
        int(row["credit_paise"]), int(row["debit_paise"]), int(row["balance_paise"]),
        row["ref_no"],
    )


_PROJECTIONS = {
    "orders": ("raw_orders", _project_order, _project_db_order),
    "gateway_recon": ("raw_txns", _project_txn, _project_db_txn),
    "bank": ("raw_bank", _project_bank, _project_db_bank),
}


def verify_database(conn: Any, canonical: CanonicalRun) -> dict[str, Any]:
    """Bind every stored source record to the canonical row it claims to be.

    Hash checks bind *files*.  This binds the *database the metrics are computed
    from*, so post-run edits to a stored amount cannot survive evaluation.
    """
    declared = {
        "orders": conn.execute("SELECT * FROM runs").fetchone()["orders_sha256"],
        "gateway_recon": conn.execute("SELECT * FROM runs").fetchone()["txns_sha256"],
        "bank": conn.execute("SELECT * FROM runs").fetchone()["bank_sha256"],
    }
    hash_mismatch = sorted(
        name for name, value in declared.items() if value != canonical.input_hashes[name]
    )
    if hash_mismatch:
        raise TruthIntegrityError(
            "the evaluated database was built from different source files "
            f"(mismatched: {hash_mismatch})"
        )

    quarantined: dict[str, set[str]] = {name: set() for name in SOURCE_FILES}
    for row in conn.execute("SELECT source,source_row_id FROM quarantine_rows"):
        quarantined.setdefault(row["source"], set()).add(row["source_row_id"])

    summary: dict[str, Any] = {}
    for name, (table, project_csv, project_db) in _PROJECTIONS.items():
        expected = canonical.rows[name]
        stored: dict[str, tuple[Any, ...]] = {}
        for row in conn.execute(f"SELECT * FROM {table}"):
            source_row_id = row["source_row_id"]
            if source_row_id in stored:
                raise TruthIntegrityError(
                    f"{table} stores {source_row_id} more than once"
                )
            stored[source_row_id] = project_db(row)
        invented = sorted(set(stored) - set(expected))
        if invented:
            raise TruthIntegrityError(
                f"{table} contains rows absent from the canonical dataset: {invented[:5]}"
            )
        accounted = set(stored) | quarantined.get(name, set())
        vanished = sorted(set(expected) - accounted)
        if vanished:
            raise TruthIntegrityError(
                f"canonical {name} rows are neither stored nor quarantined: {vanished[:5]}"
            )
        altered = sorted(
            source_row_id for source_row_id, value in stored.items()
            if value != project_csv(expected[source_row_id])
        )
        if altered:
            raise TruthIntegrityError(
                f"{table} rows differ from the canonical source data: {altered[:5]}"
            )
        summary[name] = {
            "canonical_rows": len(expected),
            "stored_rows": len(stored),
            "quarantined_rows": len(quarantined.get(name, set())),
        }
    return {"all_match": True, "sources": summary}


def establish_truth(run_dir: Path, conn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild truth independently and bind it to the evaluated run.

    Returns the canonical manifest to score against plus an integrity report.
    Any failure raises :class:`TruthIntegrityError`; there is no partial pass.
    """
    meta = read_run_meta(run_dir)
    canonical = reconstruct(meta)
    inputs = verify_inputs(run_dir, canonical)
    manifest_check = verify_declared_manifest(run_dir, canonical)
    database = verify_database(conn, canonical)
    report = {
        "method": "independent_regeneration",
        "status": "PASS",
        "generation_inputs": {
            "seed": canonical.seed,
            "profile": canonical.profile,
            "records": canonical.records,
            "generator_version": canonical.generator_version,
            "config_hashes": config_hashes(),
        },
        "inputs": inputs,
        "run_manifest": manifest_check,
        "database": database,
    }
    return canonical.manifest, report
