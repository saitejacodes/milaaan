from __future__ import annotations

import unittest
from datetime import date, datetime

from milaan.engine.plane_b import PlaneBState
from milaan.engine.recovery import apply_recovery, collect_recovery_hits
from milaan.models import BankLine, SettlementBatch


def batch(sid: str, utr: str, amount: int = 100) -> SettlementBatch:
    return SettlementBatch(sid, utr, amount, datetime(2026, 8, 3, 17), (sid + ":t",))


def line(lid: str, narration: str, amount: int = 100) -> BankLine:
    return BankLine(lid, date(2026, 8, 3), date(2026, 8, 3), narration,
                    amount, 0, amount, "ref", lid)


class RecoveryTests(unittest.TestCase):
    def test_full_suffix_and_confusable_are_code_derived(self) -> None:
        cases = (
            ("NEFT-KKBKH14156891582-RZP", "FULL"),
            ("NEFT-6891582-RZP", "SUFFIX"),
            ("NEFT-KKBKH1415689I582-RZP", "CORRUPTED"),
        )
        for index, (narration, kind) in enumerate(cases):
            batches = [batch(f"s{index}", "KKBKH14156891582")]
            lines = [line(f"b{index}", narration)]
            hits = collect_recovery_hits(batches, lines, 3)
            self.assertEqual(hits[0].kind, kind)
            state = PlaneBState()
            apply_recovery(state, batches, lines, hits, 3)
            self.assertEqual(state.decisions[0].tier.value, "B2")

    def test_two_substitutions_are_rejected(self) -> None:
        hits = collect_recovery_hits(
            [batch("s1", "KKBKH14156891582")],
            [line("b1", "NEFT-KKBKH14I5689I582-RZP")], 3,
        )
        self.assertEqual(hits, [])

    def test_short_suffix_is_rejected(self) -> None:
        hits = collect_recovery_hits(
            [batch("s1", "KKBKH14156891582")], [line("b1", "NEFT-1582-RZP")], 3,
        )
        self.assertEqual(hits, [])

    def test_suffix_collision_does_not_create_a_match(self) -> None:
        batches = [batch("s1", "AAAA111111ABCDEF"), batch("s2", "BBBB222222ABCDEF")]
        lines = [line("b1", "NEFT-ABCDEF-RZP")]
        hits = collect_recovery_hits(batches, lines, 3)
        # Collision may be represented as no unique suffix evidence; either way no decision is allowed.
        state = PlaneBState()
        apply_recovery(state, batches, lines, hits, 3)
        self.assertFalse(state.decisions)


if __name__ == "__main__":
    unittest.main()
