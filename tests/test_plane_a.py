from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime

from milaan.engine.plane_a import match_plane_a
from milaan.models import Channel, GatewayTxn, Order, OrderStatus, TxnType


def order(oid: str, amount: int, payment_id: str | None = None) -> Order:
    return Order(oid, datetime(2026, 8, 1), amount, OrderStatus.PAID,
                 Channel.UPI, payment_id, oid)


def payment(pid: str, amount: int, order_ref: str | None = None) -> GatewayTxn:
    return GatewayTxn(pid, TxnType.PAYMENT, pid, None, None, order_ref, Channel.UPI,
                      amount, 0, 0, amount, datetime(2026, 8, 1), "setl", "utr",
                      datetime(2026, 8, 3), pid)


class PlaneATests(unittest.TestCase):
    def test_a0_and_a0b_and_a1(self) -> None:
        orders = [order("o0", 100, None), order("o1", 200, "p1"), order("o2", 300, None)]
        txns = [payment("p0", 100, "o0"), payment("p1", 200), payment("p2", 300)]
        result = match_plane_a(orders, txns, 1)
        self.assertEqual([d.tier.value for d in result.decisions], ["A0", "A0B", "A1"])

    def test_mutual_ambiguity_is_never_guessed(self) -> None:
        result = match_plane_a([order("o1", 100), order("o2", 100)],
                               [payment("p1", 100), payment("p2", 100)], 1)
        self.assertFalse(result.decisions)
        self.assertEqual(result.exceptions[0].reason, "AMBIGUOUS_TIE")

    def test_missing_paid_order_is_named(self) -> None:
        result = match_plane_a([order("o1", 100)], [], 1)
        self.assertEqual(result.exceptions[0].reason, "PAID_ORDER_MISSING_FROM_GATEWAY")

    def test_two_identity_claims_are_blocked_not_sorted_away(self) -> None:
        result = match_plane_a(
            [order("o1", 100)],
            [payment("p1", 100, "o1"), payment("p2", 100, "o1")],
            1,
        )
        self.assertFalse(result.decisions)
        self.assertEqual([item.reason for item in result.exceptions], ["IDENTITY_CONFLICT"])
        self.assertEqual(set(result.exceptions[0].scope_ids), {"o1", "p1", "p2"})

    def test_payment_before_order_is_never_accepted_by_direct_id(self) -> None:
        early = replace(payment("p1", 100, "o1"), captured_at=datetime(2026, 7, 1))
        result = match_plane_a([order("o1", 100)], [early], 30)
        self.assertFalse(result.decisions)
        self.assertEqual(result.exceptions[0].reason, "DATE_OUT_OF_WINDOW")

    def test_unmatched_gateway_payment_is_explicit(self) -> None:
        result = match_plane_a([], [payment("p1", 100, "missing-order")], 1)
        self.assertFalse(result.decisions)
        self.assertEqual(result.exceptions[0].reason, "UNMATCHED_GATEWAY_PAYMENT")


if __name__ == "__main__":
    unittest.main()
