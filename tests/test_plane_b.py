from __future__ import annotations

import unittest
from datetime import date, datetime

from milaan.engine.plane_b import match_b0_b1
from milaan.models import BankLine, SettlementBatch


def batch(sid: str, utr: str | None, amount: int) -> SettlementBatch:
    return SettlementBatch(sid, utr, amount, datetime(2026, 8, 3, 17), (sid + ":t",))


def line(lid: str, narration: str, amount: int) -> BankLine:
    return BankLine(lid, date(2026, 8, 3), date(2026, 8, 3), narration,
                    amount, 0, amount, "ref", lid)


# The shipped configuration matches amounts exactly on every tier; these unit
# tests use the same policy so they cannot pass under a rule the product does
# not actually ship. See ADR-012.
EXACT = 0


class PlaneBTests(unittest.TestCase):
    def test_b0_is_candidate_driven(self) -> None:
        result = match_b0_b1([batch("s1", "ABC123456789", 100)],
                             [line("b1", "NEFT-ABC123456789-RZP", 100)], 3, EXACT)
        self.assertEqual(result.decisions[0].tier.value, "B0")

    def test_b1_twin_is_ambiguous_never_greedy(self) -> None:
        batches = [batch("s1", None, 100), batch("s2", None, 100)]
        lines = [line("b1", "NOREF-A", 100), line("b2", "NOREF-B", 100)]
        result = match_b0_b1(batches, lines, 3, EXACT)
        self.assertFalse(result.decisions)
        self.assertEqual(result.exceptions[0].reason, "AMBIGUOUS_TIE")

    def test_duplicate_utr_blocks_matching(self) -> None:
        batches = [batch("s1", "ABC123456789", 100)]
        lines = [line("b1", "ABC123456789", 100), line("b2", "ABC123456789", 101)]
        result = match_b0_b1(batches, lines, 3, EXACT)
        self.assertFalse(result.decisions)
        self.assertEqual(result.exceptions[0].reason, "DUPLICATE_UTR")

    def test_one_paise_difference_is_not_matched(self) -> None:
        """Amount equality is exact: there is no band a discrepancy hides in."""
        result = match_b0_b1([batch("s1", "ABC123456789", 100)],
                             [line("b1", "NEFT-ABC123456789-RZP", 101)], 3, EXACT)
        self.assertFalse(result.decisions)

    def test_shipped_timing_configuration_is_exact(self) -> None:
        from milaan.config import load_timing

        self.assertEqual(load_timing().tol_b_paise, EXACT)

    def test_bank_credit_before_settlement_is_rejected(self) -> None:
        early = BankLine("b1", date(2026, 8, 1), date(2026, 8, 1),
                         "NEFT-ABC123456789-RZP", 100, 0, 100, "ref", "b1")
        result = match_b0_b1([batch("s1", "ABC123456789", 100)], [early], 3, EXACT)
        self.assertFalse(result.decisions)


if __name__ == "__main__":
    unittest.main()
