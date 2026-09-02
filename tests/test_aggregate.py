from __future__ import annotations

import unittest
from datetime import datetime

from milaan.ingest.aggregate import aggregate_batches
from milaan.ingest.normalize import IngestResult
from milaan.models import GatewayTxn, TxnType


def txn(txn_id: str, utr: str | None) -> GatewayTxn:
    return GatewayTxn(
        txn_id=txn_id, txn_type=TxnType.ADJUSTMENT, payment_id=None,
        original_payment_id=None, refund_id=None, order_ref=None, channel=None,
        gross_paise=100, fee_paise=0, tax_paise=0, net_paise=100,
        captured_at=datetime(2026, 8, 1), settlement_id="setl_1",
        settlement_utr=utr, settlement_processed_at=datetime(2026, 8, 3, 17),
        source_row_id=txn_id,
    )


class AggregateTests(unittest.TestCase):
    def test_null_member_utr_does_not_conflict(self) -> None:
        ingested = IngestResult([], [txn("pay_1", "UTR123"), txn("adj_1", None)], [], [], set(), [])
        result = aggregate_batches(ingested)
        self.assertEqual(result.batches[0].settlement_utr, "UTR123")
        self.assertFalse(result.exceptions)

    def test_two_distinct_non_null_utrs_conflict(self) -> None:
        ingested = IngestResult([], [txn("pay_1", "UTR123"), txn("pay_2", "UTR999")], [], [], set(), [])
        result = aggregate_batches(ingested)
        self.assertEqual(result.exceptions[0].reason, "UTR_CONFLICT_IN_BATCH")
        self.assertIn("setl_1", result.blocked_settlement_ids)

    def test_unsupported_member_taints_whole_batch(self) -> None:
        ingested = IngestResult([], [txn("pay_1", "UTR123")], [], [], {"setl_1"}, [])
        result = aggregate_batches(ingested)
        self.assertTrue(result.batches[0].tainted)
        self.assertEqual(result.exceptions[0].reason, "TAINTED_SETTLEMENT")


if __name__ == "__main__":
    unittest.main()
