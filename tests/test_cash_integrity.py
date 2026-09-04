"""Cash reporting must not lose or double-count money."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from milaan.db import connect
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.evalx.metrics import compute_metrics
from milaan.generator.emit import generate_to_directory


def _rows(database: Path, table: str) -> list[dict]:
    with connect(database) as conn:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]  # noqa: S608


class CashIntegrityTests(unittest.TestCase):
    def test_settlement_with_every_member_rejected_still_reports_its_money(self) -> None:
        """No batch row survives, so exposure must come from the exception itself."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(120, 31, "clean", run)
            manifest = json.loads((run / "manifest.json").read_text())
            expected = {a for a, _ in manifest["expectations"]["plane_b_matches"]}
            with (run / "gateway_recon.csv").open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                fields, rows = list(reader.fieldnames or ()), list(reader)
            counts = Counter(row["settlement_id"] for row in rows
                             if row["settlement_id"] in expected)
            settlement_id, _count = counts.most_common()[-1]
            claimed = sum(int(row["net_paise"]) for row in rows
                          if row["settlement_id"] == settlement_id)
            self.assertGreater(claimed, 0)

            for row in rows:
                if row["settlement_id"] != settlement_id:
                    continue
                if row["type"] == "payment":
                    row["fee_paise"] = str(int(row["fee_paise"]) + 1)
                else:
                    row["type"] = "bogus_type"
            with (run / "gateway_recon.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)

            run_pipeline(run, run / "m.db", "mock")
            # evaluate_run refuses tampered inputs before it ever reaches the
            # metrics, which is correct; the accounting question is asked of
            # compute_metrics directly, against the truth generated for this run.
            conn = connect(run / "m.db")
            try:
                metrics = compute_metrics(conn, manifest)
            finally:
                conn.close()

            batch_row_survived = any(
                row["settlement_id"] == settlement_id
                for row in _rows(run / "m.db", "settlement_batches")
            )
            self.assertFalse(batch_row_survived,
                             "the fixture must leave no aggregated batch row")
            cash = metrics["cash_position"]
            self.assertGreaterEqual(cash["blocked_settlement_paise"], claimed)
            self.assertEqual(cash["classification"]["settlements"]["rate"], 1.0)
            self.assertEqual(
                cash["classification"]["settlement_buckets"].get("blocked", 0) > 0, True
            )

    def test_every_settlement_and_bank_line_is_classified_exactly_once(self) -> None:
        for records, seed, profile in ((200, 5, "clean"), (600, 6, "mixed"), (600, 7, "hard")):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp) / "run"
                generate_to_directory(records, seed, profile, run)
                run_pipeline(run, run / "m.db", "mock")
                evaluate_run(run, run / "m.db", run, profile)
                metrics = json.loads((run / "functional_metrics.json").read_text())
                cash = metrics["cash_position"]
                classification = cash["classification"]

                self.assertEqual(classification["settlements"]["rate"], 1.0)
                self.assertEqual(classification["bank_lines"]["rate"], 1.0)
                self.assertEqual(classification["unclassified_settlement_ids"], [])
                self.assertEqual(classification["unclassified_bank_line_ids"], [])
                # Buckets partition, so they must sum back to their denominator.
                self.assertEqual(
                    sum(classification["settlement_buckets"].values()),
                    classification["settlements"]["denominator"],
                )
                self.assertEqual(
                    sum(classification["bank_line_buckets"].values()),
                    classification["bank_lines"]["denominator"],
                )

    def test_bank_totals_and_attention_add_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(600, 8, "mixed", run)
            run_pipeline(run, run / "m.db", "mock")
            evaluate_run(run, run / "m.db", run, "mixed")
            cash = json.loads((run / "functional_metrics.json").read_text())["cash_position"]

            self.assertEqual(
                cash["banked_paise"] + cash["unexplained_bank_credit_paise"],
                cash["total_bank_credit_paise"],
            )
            self.assertEqual(
                cash["expected_unbanked_paise"] + cash["blocked_settlement_paise"]
                + cash["unexplained_bank_credit_paise"],
                cash["gross_attention_paise"],
            )
            self.assertEqual(
                cash["matched_settlement_paise"] + cash["expected_unbanked_paise"]
                + cash["blocked_settlement_paise"],
                cash["total_settlement_control_paise"],
            )
            # A matched settlement equals its bank credit exactly, so the delta
            # across every match must be zero paise.
            self.assertEqual(cash["matched_amount_delta_paise"], 0)


if __name__ == "__main__":
    unittest.main()
