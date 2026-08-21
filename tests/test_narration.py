from __future__ import annotations

import random
import unittest

from milaan.generator.narration import SHAPE_RES, clean_narration, make_utr, mangle_narration


class NarrationTests(unittest.TestCase):
    def test_all_families_match_their_generator_shapes(self) -> None:
        for family in SHAPE_RES:
            rng = random.Random(100 + len(family))
            utr, actual = make_utr(rng, family)
            self.assertEqual(actual, family)
            self.assertRegex(utr, SHAPE_RES[family])

    def test_every_mangling_operator_changes_as_documented(self) -> None:
        operators = (
            "TRUNCATE_SUFFIX", "STRIP_SPACES", "COUNTERPARTY_TYPO",
            "PREFIX_NOISE", "INSERT_SEPARATOR", "CONFUSABLE_SUB", "DROP_UTR",
        )
        for index, operator in enumerate(operators):
            rng = random.Random(index)
            utr, _ = make_utr(rng, "F1")
            clean = "NEFT " + utr + " RAZORPAY SETL"
            damaged, detail = mangle_narration(rng, clean, utr, operator)
            self.assertNotEqual(damaged, clean, operator)
            self.assertEqual(detail["op"], operator)
            if operator == "DROP_UTR":
                self.assertNotIn(utr, damaged)
            if operator == "CONFUSABLE_SUB":
                self.assertIn("pos", detail)


if __name__ == "__main__":
    unittest.main()
