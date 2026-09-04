from datetime import date
import unittest

from milaan.config import fee_for, load_fees, load_timing
from milaan.generator.calendar import (
    add_business_days, business_gap, forward_business_gap, is_business_day,
)
from milaan.models import Channel


class CalendarAndConfigTests(unittest.TestCase):
    def test_weekend_and_holiday_rollover(self) -> None:
        self.assertEqual(add_business_days(date(2026, 1, 23), 1), date(2026, 1, 27))
        self.assertFalse(is_business_day(date(2026, 1, 26)))

    def test_gap_is_order_agnostic(self) -> None:
        a, b = date(2026, 1, 23), date(2026, 1, 27)
        self.assertEqual(business_gap(a, b), 1)
        self.assertEqual(business_gap(b, a), 1)

    def test_forward_gap_rejects_reversed_chronology(self) -> None:
        earlier, later = date(2026, 1, 23), date(2026, 1, 27)
        self.assertEqual(forward_business_gap(earlier, later), 1)
        self.assertIsNone(forward_business_gap(later, earlier))

    def test_fee_math_is_integer_and_half_up(self) -> None:
        fee, tax = fee_for(10_000, Channel.CARD, load_fees())
        self.assertEqual((fee, tax), (200, 36))
        self.assertIsInstance(fee, int)

    def test_timing_toml_is_valid(self) -> None:
        cfg = load_timing()
        self.assertEqual(cfg.settlement_cycle_bd, 2)
        self.assertEqual(cfg.tol_b_paise, 0)  # exact amount equality on every tier


if __name__ == "__main__":
    unittest.main()
