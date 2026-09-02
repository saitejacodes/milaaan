from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.generator.emit import generate_to_directory


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class AdversarialIntegrityTests(unittest.TestCase):
    def test_rejected_member_taints_batch_and_blocks_partial_credit_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(100, 42, "clean", run)
            manifest = json.loads((run / "manifest.json").read_text())
            txn_fields, txns = read_csv(run / "gateway_recon.csv")
            payment = next(row for row in txns if row["type"].upper() == "PAYMENT")
            settlement_id = payment["settlement_id"]
            bank_id = next(bank_id for sid, bank_id
                           in manifest["expectations"]["plane_b_matches"]
                           if sid == settlement_id)
            dropped_net = int(payment["net_paise"])
            payment["fee_paise"] = str(int(payment["fee_paise"]) + 1)
            write_csv(run / "gateway_recon.csv", txn_fields, txns)

            bank_fields, lines = read_csv(run / "bank.csv")
            target = next(row for row in lines if row["line_id"] == bank_id)
            target["credit_paise"] = str(int(target["credit_paise"]) - dropped_net)
            write_csv(run / "bank.csv", bank_fields, lines)

            database = run / "m.db"
            run_pipeline(run, database, "mock")
            conn = sqlite3.connect(database)
            try:
                matched = conn.execute(
                    """SELECT count(*) FROM matches m JOIN match_members mm
                       ON mm.match_id=m.match_id AND mm.entity_type='BATCH'
                       WHERE mm.entity_id=?""", (settlement_id,),
                ).fetchone()[0]
                reasons = {row[0] for row in conn.execute(
                    "SELECT reason_code FROM exceptions"
                ).fetchall()}
                tainted = conn.execute(
                    "SELECT tainted FROM settlement_batches WHERE settlement_id=?",
                    (settlement_id,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(matched, 0)
            self.assertEqual(tainted, 1)
            self.assertIn("FEE_MODEL_VIOLATION", reasons)
            self.assertIn("TAINTED_SETTLEMENT", reasons)


if __name__ == "__main__":
    unittest.main()
