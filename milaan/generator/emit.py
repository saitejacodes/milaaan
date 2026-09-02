"""Write deterministic CSV and JSON generator artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from milaan.config import load_fees, load_timing
from milaan.generator.inject import apply_injections
from milaan.generator.manifest import GENERATOR_VERSION, build_manifest
from milaan.generator.world import build_world


ORDER_FIELDS = (
    "source_row_id", "order_id", "created_at", "amount_paise", "status", "channel", "payment_id",
)
TXN_FIELDS = (
    "source_row_id", "entity_id", "type", "payment_id", "refund_id", "order_id", "method",
    "gross_paise", "fee_paise", "tax_paise", "net_paise", "captured_at",
    "settlement_id", "settlement_utr", "settlement_processed_at",
)
BANK_FIELDS = (
    "source_row_id", "line_id", "txn_date", "value_date", "narration", "credit_paise",
    "debit_paise", "balance_paise", "ref_no",
)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]], key: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: str(item[key])):
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_to_directory(records: int, seed: int, profile: str, out: Path) -> str:
    rng = random.Random(seed)
    world = build_world(records, seed, profile, rng, load_fees(), load_timing())
    apply_injections(world, rng)
    manifest = build_manifest(world)
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "orders.csv", ORDER_FIELDS, world.orders, "order_id")
    _write_csv(out / "gateway_recon.csv", TXN_FIELDS, world.txns, "entity_id")
    _write_csv(out / "bank.csv", BANK_FIELDS, world.bank, "line_id")
    manifest["input_hashes"] = {
        "orders": _sha256(out / "orders.csv"),
        "gateway_recon": _sha256(out / "gateway_recon.csv"),
        "bank": _sha256(out / "bank.csv"),
    }
    _write_json(out / "manifest.json", manifest)
    _write_json(out / "run_meta.json", {
        "seed": seed, "profile": profile, "records": records,
        "generator_version": GENERATOR_VERSION,
    })
    return (
        f"generated {records} orders, {len(world.txns)} recon rows, "
        f"{len(world.bank)} bank lines; seed={seed} profile={profile}"
    )
