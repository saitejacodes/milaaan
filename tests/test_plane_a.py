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


if __name__ == "__main__":
    unittest.main()
