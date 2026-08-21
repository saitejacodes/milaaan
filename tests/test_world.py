from __future__ import annotations

import random
import unittest

from milaan.config import load_fees, load_timing
from milaan.generator.world import build_world


class WorldTests(unittest.TestCase):
    def test_world_has_three_planes_of_source_data(self) -> None:
        world = build_world(200, 7, "clean", random.Random(7), load_fees(), load_timing())
        self.assertEqual(len(world.orders), 200)
        self.assertGreater(len(world.txns), 180)
        self.assertGreater(len(world.bank), 10)
        self.assertTrue(world.expectations["plane_a_matches"])
        self.assertTrue(world.expectations["plane_b_matches"])

    def test_multi_settlement_day_is_structural(self) -> None:
        world = build_world(1200, 42, "clean", random.Random(42), load_fees(), load_timing())
        days = world.structural["multi_settlement_days"]
        self.assertTrue(days)
        for day in days:
            ids = {t["settlement_id"] for t in world.txns
                   if str(t["settlement_processed_at"]).startswith(day)}
            self.assertGreaterEqual(len(ids), 2)


if __name__ == "__main__":
    unittest.main()
