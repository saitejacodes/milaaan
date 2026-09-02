"""Plane A: merchant orders to gateway PAYMENT transactions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from milaan.engine.confidence import confidence_for
from milaan.generator.calendar import business_gap, forward_business_gap
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

    def block_direct_pair(order: Order, txn: GatewayTxn, identity_rule: str) -> bool:
        if txn.gross_paise != order.amount_paise:
            exceptions.append(ExceptionItem(
                (order.order_id, txn.txn_id), "AMOUNT_MISMATCH_BEYOND_TOL", 1.0,
                {"order_amount_paise": order.amount_paise,
                 "txn_gross_paise": txn.gross_paise,
                 "identity_rule": identity_rule},
            ))
            blocked_orders.add(order.order_id)
            blocked_txns.add(txn.txn_id)
            return True
        gap = forward_business_gap(order.created_at.date(), txn.captured_at.date())
        if gap is None or gap > window_a_bd:
            exceptions.append(ExceptionItem(
                (order.order_id, txn.txn_id), "DATE_OUT_OF_WINDOW", 1.0,
                {"order_created_date": order.created_at.date().isoformat(),
                 "payment_captured_date": txn.captured_at.date().isoformat(),
                 "forward_gap_bd": gap, "window_bd": window_a_bd,
                 "identity_rule": identity_rule},
            ))
            blocked_orders.add(order.order_id)
            blocked_txns.add(txn.txn_id)
            return True
        return False

    # Build the full identity graph before accepting a direct match.  Any order
    # claiming multiple payments, or payment claimed by multiple orders, is one
    # connected conflict and must be resolved at source rather than by sort order.
    identity_edges: set[tuple[str, str]] = set()
    for txn in payments.values():
        if txn.order_ref in active_orders:
            identity_edges.add((str(txn.order_ref), txn.txn_id))
    for order in active_orders.values():
        if order.payment_id in payments:
            identity_edges.add((order.order_id, str(order.payment_id)))

    by_order: dict[str, set[str]] = {}
    by_txn: dict[str, set[str]] = {}
    for order_id, txn_id in identity_edges:
        by_order.setdefault(order_id, set()).add(txn_id)
        by_txn.setdefault(txn_id, set()).add(order_id)
    conflict_orders = {order_id for order_id, ids in by_order.items() if len(ids) > 1}
    conflict_txns = {txn_id for txn_id, ids in by_txn.items() if len(ids) > 1}
    unseen: set[tuple[str, str]] = ({("ORDER", value) for value in conflict_orders}
                                    | {("TXN", value) for value in conflict_txns})
    while unseen:
        seed = min(unseen)
        stack = [seed]
        component_orders: set[str] = set()
        component_txns: set[str] = set()
        while stack:
            kind, entity_id = stack.pop()
            node = (kind, entity_id)
            unseen.discard(node)
            if kind == "ORDER":
                if entity_id in component_orders:
                    continue
                component_orders.add(entity_id)
                for txn_id in by_order.get(entity_id, ()):
                    if txn_id not in component_txns:
                        stack.append(("TXN", txn_id))
            else:
                if entity_id in component_txns:
                    continue
                component_txns.add(entity_id)
                for order_id in by_txn.get(entity_id, ()):
                    if order_id not in component_orders:
                        stack.append(("ORDER", order_id))
        blocked_orders.update(component_orders)
        blocked_txns.update(component_txns)
        exceptions.append(ExceptionItem(
            tuple(sorted(component_orders | component_txns)), "IDENTITY_CONFLICT", 1.0,
            {"order_ids": sorted(component_orders), "payment_ids": sorted(component_txns),
             "identity_edges": sorted((order_id, txn_id) for order_id, txn_id in identity_edges
                                      if order_id in component_orders or txn_id in component_txns)},
        ))

    # A0: direct gateway order reference.
    for txn in sorted(payments.values(), key=lambda item: item.txn_id):
        if not txn.order_ref or txn.order_ref not in active_orders:
            continue
        order = active_orders[txn.order_ref]
        if (order.order_id in matched_orders or order.order_id in blocked_orders
                or txn.txn_id in blocked_txns):
            continue
        if block_direct_pair(order, txn, "order_ref"):
            continue
        accept(order, txn, MatchTier.A0)

    # A0b: merchant-recorded payment id.
    for order in sorted(active_orders.values(), key=lambda item: item.order_id):
        if (order.order_id in matched_orders or order.order_id in blocked_orders
                or not order.payment_id):
            continue
        txn = payments.get(order.payment_id)
        if not txn or txn.txn_id in matched_txns or txn.txn_id in blocked_txns:
            continue
        if block_direct_pair(order, txn, "payment_id"):
            continue
        accept(order, txn, MatchTier.A0B)

    # A1: exact amount and mutual uniqueness inside the business-day window.
    remaining_orders = [o for o in active_orders.values()
                        if o.order_id not in matched_orders and o.order_id not in blocked_orders]
    remaining_txns = [t for t in payments.values()
                      if t.txn_id not in matched_txns and t.txn_id not in blocked_txns]
    by_order: dict[str, list[str]] = {}
    by_txn: dict[str, list[str]] = {}
    txns_by_amount: dict[int, list[GatewayTxn]] = defaultdict(list)
    for txn in remaining_txns:
        txns_by_amount[txn.gross_paise].append(txn)
    for order in remaining_orders:
        for txn in txns_by_amount.get(order.amount_paise, ()):
            gap = forward_business_gap(order.created_at.date(), txn.captured_at.date())
            if gap is not None and gap <= window_a_bd:
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
        if order.order_id not in matched_orders and order.order_id not in blocked_orders:
            exceptions.append(ExceptionItem(
                (order.order_id,), "PAID_ORDER_MISSING_FROM_GATEWAY", 1.0,
                {"order_id": order.order_id, "amount_paise": order.amount_paise},
            ))
    for txn in sorted(payments.values(), key=lambda item: item.txn_id):
        if txn.txn_id not in matched_txns and txn.txn_id not in blocked_txns:
            exceptions.append(ExceptionItem(
                (txn.txn_id,), "UNMATCHED_GATEWAY_PAYMENT", 1.0,
                {"payment_id": txn.txn_id, "order_ref": txn.order_ref,
                 "gross_paise": txn.gross_paise,
                 "captured_at": txn.captured_at.isoformat()},
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
