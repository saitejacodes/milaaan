from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from milaan.config import load_fees
from milaan.generator.emit import generate_to_directory
from milaan.ingest.normalize import normalize_inputs


class NormalizeTests(unittest.TestCase):
    def test_generated_money_stays_integer_and_identity_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(100, 1, "clean", run)
            result = normalize_inputs(run, load_fees())
            payment = next(t for t in result.txns if t.txn_type.value == "PAYMENT")
            self.assertEqual(payment.payment_id, payment.txn_id)
            self.assertIsInstance(payment.net_paise, int)
            self.assertFalse(result.quarantined)

    def test_transfer_row_taints_its_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(100, 2, "clean", run)
            path = run / "gateway_recon.csv"
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle)); fields = handle.seek(0) or list(rows[0])
            transfer = dict(rows[0])
            transfer.update({"source_row_id": "recon:transfer", "entity_id": "trf_test", "type": "transfer"})
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
                writer.writeheader(); writer.writerows(rows + [transfer])
            result = normalize_inputs(run, load_fees())
            self.assertIn(transfer["settlement_id"], result.tainted_settlement_ids)
            self.assertTrue(any(row.source_row_id == "recon:transfer" for row in result.quarantined))


if __name__ == "__main__":
    unittest.main()
