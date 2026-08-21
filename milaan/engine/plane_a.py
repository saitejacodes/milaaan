"""Plane A: merchant orders to gateway PAYMENT transactions."""

from __future__ import annotations

from dataclasses import dataclass

from milaan.engine.confidence import confidence_for
from milaan.generator.calendar import business_gap
from milaan.models import (
    Decision, ExceptionItem, GatewayTxn, MatchKind, MatchTier, Order,
    OrderStatus, Plane, TxnType,
)


@dataclass
class PlaneAResult:
    decisions: list[Decision]
    exceptions: list[ExceptionItem]
    matched_order_ids: set[str]
    matched_txn_ids: set[str]


def _decision(order: Order, txn: GatewayTxn, tier: MatchTier) -> Decision:
    gap = business_gap(order.created_at.date(), txn.captured_at.date())
    return Decision(
        plane=Plane.A, kind=MatchKind.ORDER_TXN, left_ids=(order.order_id,),
        right_id=txn.txn_id, tier=tier,
        amount_diff_paise=txn.gross_paise - order.amount_paise,
        date_gap_bd=gap, confidence=confidence_for(tier.value, gap, 0),
        evidence={"order_id": order.order_id, "txn_id": txn.txn_id,
                  "gross_paise": txn.gross_paise, "order_amount_paise": order.amount_paise,
                  "rule": tier.value},
    )


def match_plane_a(orders: list[Order], txns: list[GatewayTxn], window_a_bd: int) -> PlaneAResult:
    active_orders = {o.order_id: o for o in orders if o.status is not OrderStatus.FAILED}
    payments = {t.txn_id: t for t in txns if t.txn_type is TxnType.PAYMENT}
    decisions: list[Decision] = []
    exceptions: list[ExceptionItem] = []
    matched_orders: set[str] = set()
    matched_txns: set[str] = set()
    blocked_orders: set[str] = set()
    blocked_txns: set[str] = set()

    def accept(order: Order, txn: GatewayTxn, tier: MatchTier) -> None:
        decisions.append(_decision(order, txn, tier))
        matched_orders.add(order.order_id)
        matched_txns.add(txn.txn_id)

    # A0: direct gateway order reference.
    for txn in sorted(payments.values(), key=lambda item: item.txn_id):
        if not txn.order_ref or txn.order_ref not in active_orders:
            continue
        order = active_orders[txn.order_ref]
        if order.order_id in matched_orders:
            continue
        if txn.gross_paise != order.amount_paise:
            ids = (order.order_id, txn.txn_id)
            exceptions.append(ExceptionItem(ids, "AMOUNT_MISMATCH_BEYOND_TOL", 1.0, {
                "order_amount_paise": order.amount_paise, "txn_gross_paise": txn.gross_paise,
                "identity_rule": "order_ref",
            }))
            blocked_orders.add(order.order_id); blocked_txns.add(txn.txn_id)
            continue
        accept(order, txn, MatchTier.A0)

    # A0b: merchant-recorded payment id.
    for order in sorted(active_orders.values(), key=lambda item: item.order_id):
        if order.order_id in matched_orders | blocked_orders or not order.payment_id:
            continue
        txn = payments.get(order.payment_id)
        if not txn or txn.txn_id in matched_txns | blocked_txns:
            continue
        if txn.gross_paise != order.amount_paise:
            ids = (order.order_id, txn.txn_id)
            exceptions.append(ExceptionItem(ids, "AMOUNT_MISMATCH_BEYOND_TOL", 1.0, {
                "order_amount_paise": order.amount_paise, "txn_gross_paise": txn.gross_paise,
                "identity_rule": "payment_id",
            }))
            blocked_orders.add(order.order_id); blocked_txns.add(txn.txn_id)
            continue
        accept(order, txn, MatchTier.A0B)

    # A1: exact amount and mutual uniqueness inside the business-day window.
    remaining_orders = [o for o in active_orders.values()
                        if o.order_id not in matched_orders | blocked_orders]
    remaining_txns = [t for t in payments.values()
                      if t.txn_id not in matched_txns | blocked_txns]
    by_order: dict[str, list[str]] = {}
    by_txn: dict[str, list[str]] = {}
    for order in remaining_orders:
        for txn in remaining_txns:
            if order.amount_paise == txn.gross_paise and business_gap(
                    order.created_at.date(), txn.captured_at.date()) <= window_a_bd:
                by_order.setdefault(order.order_id, []).append(txn.txn_id)
                by_txn.setdefault(txn.txn_id, []).append(order.order_id)
    order_map = {o.order_id: o for o in remaining_orders}
    for order_id in sorted(by_order):
        candidates = by_order[order_id]
        if len(candidates) == 1 and len(by_txn[candidates[0]]) == 1:
            txn_id = candidates[0]
            if order_id not in matched_orders and txn_id not in matched_txns:
                accept(order_map[order_id], payments[txn_id], MatchTier.A1)

    ambiguous_ids: set[str] = set()
    for order_id, candidate_txns in by_order.items():
        if order_id in matched_orders:
            continue
        if len(candidate_txns) > 1 or any(len(by_txn[txn_id]) > 1 for txn_id in candidate_txns):
            ambiguous_ids.add(order_id); ambiguous_ids.update(candidate_txns)
    if ambiguous_ids:
        exceptions.append(ExceptionItem(tuple(sorted(ambiguous_ids)), "AMBIGUOUS_TIE", 1.0, {
            "plane": "A", "candidate_edges": sorted(
                (order_id, txn_id) for order_id, txn_ids in by_order.items() for txn_id in txn_ids
                if order_id in ambiguous_ids
            ),
        }))
        blocked_orders.update(entity for entity in ambiguous_ids if entity in active_orders)
        blocked_txns.update(entity for entity in ambiguous_ids if entity in payments)

    # Missing source-plane records and orphan refunds.
    for order in sorted(active_orders.values(), key=lambda item: item.order_id):
        if order.order_id not in matched_orders | blocked_orders:
            exceptions.append(ExceptionItem(
                (order.order_id,), "PAID_ORDER_MISSING_FROM_GATEWAY", 1.0,
                {"order_id": order.order_id, "amount_paise": order.amount_paise},
            ))
    payment_ids = set(payments)
    for refund in sorted((t for t in txns if t.txn_type in {TxnType.REFUND, TxnType.CHARGEBACK}),
                         key=lambda item: item.txn_id):
        if refund.original_payment_id not in payment_ids:
            exceptions.append(ExceptionItem(
                (refund.txn_id,), "ORPHAN_REFUND", 1.0,
                {"refund_id": refund.txn_id, "missing_payment_id": refund.original_payment_id},
            ))
    return PlaneAResult(decisions, exceptions, matched_orders, matched_txns)
