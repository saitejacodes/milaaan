from __future__ import annotations

import random
import unittest

from milaan.config import load_fees, load_timing
from milaan.generator.inject import apply_injections
from milaan.generator.world import build_world


class InjectionTests(unittest.TestCase):
    def test_targets_are_disjoint_and_expected(self) -> None:
        rng = random.Random(42)
        world = build_world(1200, 42, "mixed", rng, load_fees(), load_timing())
        apply_injections(world, rng)
        ids = [entity_id for item in world.injected for entity_id in item["ids"]]
        self.assertEqual(len(ids), len(set(ids)))
        codes = {code for item in world.expectations["exceptions"] for code in item["allowed_codes"]}
        self.assertTrue({"AMBIGUOUS_TIE", "DUPLICATE_UTR", "DUPLICATE_BANK_LINE",
                         "MISSING_IN_BANK", "UNKNOWN_BANK_CREDIT",
                         "PAID_ORDER_MISSING_FROM_GATEWAY"}.issubset(codes))

    def test_clean_profile_has_no_injections(self) -> None:
        rng = random.Random(1)
        world = build_world(200, 1, "clean", rng, load_fees(), load_timing())
        apply_injections(world, rng)
        self.assertEqual(world.injected, [])
        self.assertEqual(world.expectations["exceptions"], [])


if __name__ == "__main__":
    unittest.main()
