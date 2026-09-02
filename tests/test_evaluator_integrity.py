from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from milaan.db import (
    connect, create_fresh, insert_bank, insert_batches, insert_decision,
    insert_exception, insert_run, insert_txns,
)
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.evalx.metrics import compute_metrics
from milaan.generator.emit import generate_to_directory
from milaan.models import (
    BankLine, Decision, ExceptionItem, GatewayTxn, MatchKind, MatchTier,
    Plane, SettlementBatch, TxnType,
)


class EvaluatorIntegrityTests(unittest.TestCase):
    def test_pair_ids_alone_cannot_hide_wrong_amount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.db"
            conn = create_fresh(path)
            hashes = {key: key * 8 for key in ("orders", "txns", "bank", "fees", "timing")}
            insert_run(conn, run_id="1-clean-mock", seed=1, profile="clean",
                       llm_mode="mock", hashes=hashes, git_sha=None)
            txn = GatewayTxn(
                "t1", TxnType.ADJUSTMENT, None, None, None, None, None,
                900, 0, 0, 900, datetime(2026, 8, 1), "s1", "UTR1",
                datetime(2026, 8, 2), "recon:2",
            )
            insert_txns(conn, "1-clean-mock", [txn])
            insert_batches(conn, "1-clean-mock", [
                SettlementBatch("s1", "UTR1", 900, datetime(2026, 8, 2), ("t1",)),
            ])
            insert_bank(conn, "1-clean-mock", [
                BankLine("b1", date(2026, 8, 2), date(2026, 8, 2), "UTR1",
                         900, 0, 900, "UTR1", "bank:2"),
            ])
            insert_decision(conn, "1-clean-mock", Decision(
                Plane.B, MatchKind.BATCH_BANK, ("s1",), "b1", MatchTier.B0,
                0, 0, 1.0, {},
            ))
            manifest = {
                "seed": 1, "profile": "clean", "generator_version": "independent-fixture",
                "tier_labels": {"s1": "T0"},
                "expectations": {"plane_a_matches": [],
                                 "plane_b_matches": [["s1", "b1"]], "exceptions": []},
                "match_facts": {"A": {}, "B": {"s1": {
                    "bank_line_id": "b1", "member_txn_ids": ["t1"],
                    "batch_amount_paise": 1000, "bank_credit_paise": 1000,
                }}},
            }
            metrics = compute_metrics(conn, manifest)
            conn.close()
            self.assertEqual(metrics["planes"]["B"]["expected_match_recall"]["rate"], 0)
            self.assertEqual(metrics["planes"]["B"]["false_match_count"], 1)

    def test_modified_input_cannot_reuse_original_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(50, 9, "clean", run)
            bank_path = run / "bank.csv"
            with bank_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                fields, rows = list(reader.fieldnames or ()), list(reader)
            rows[0]["narration"] += " SAFE-WHITESPACE"
            with bank_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            database = run / "m.db"
            run_pipeline(run, database, "mock")
            with self.assertRaisesRegex(RuntimeError, "manifest input hashes"):
                evaluate_run(run, database, run, "clean")

    def test_unexpected_exception_fails_precision_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            database = run / "m.db"
            generate_to_directory(100, 12, "mixed", run)
            run_pipeline(run, database, "mock")
            conn = connect(database)
            insert_exception(conn, "12-mixed-mock", ExceptionItem(
                ("invented_scope",), "INGEST_REJECT", 1.0, {"source": "test"},
                narrative="Synthetic unexpected exception", guidance="Review", suggested_action="Review",
            ))
            conn.close()
            with self.assertRaisesRegex(RuntimeError, "exception precision"):
                evaluate_run(run, database, run, "mixed")


if __name__ == "__main__":
    unittest.main()
