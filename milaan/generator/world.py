"""Construct the deterministic, internally consistent synthetic finance world."""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from milaan.config import FeeConfig, TimingConfig, fee_for
from milaan.generator.calendar import add_business_days
from milaan.generator.narration import clean_narration, make_utr
from milaan.models import Channel, OrderStatus


@dataclass
class World:
    seed: int
    profile: str
    records: int
    orders: list[dict[str, Any]] = field(default_factory=list)
    txns: list[dict[str, Any]] = field(default_factory=list)
    bank: list[dict[str, Any]] = field(default_factory=list)
    expectations: dict[str, list[Any]] = field(default_factory=lambda: {
        "plane_a_matches": [], "plane_b_matches": [], "exceptions": []
    })
    tier_labels: dict[str, str] = field(default_factory=dict)
    structural: dict[str, Any] = field(default_factory=lambda: {"multi_settlement_days": []})
    injected: list[dict[str, Any]] = field(default_factory=list)


def _token(rng: random.Random, prefix: str, length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return prefix + "".join(rng.choice(alphabet) for _ in range(length))


def _weighted_channel(rng: random.Random) -> Channel:
    roll = rng.randrange(100)
    return Channel.UPI if roll < 55 else Channel.CARD if roll < 85 else Channel.NETBANKING


def _status(rng: random.Random) -> OrderStatus:
    roll = rng.random()
    if roll < 0.04:
        return OrderStatus.FAILED
    if roll < 0.07:
        return OrderStatus.REFUNDED
    if roll < 0.10:
        return OrderStatus.PARTIAL_REFUND
    return OrderStatus.PAID


def _amount(rng: random.Random) -> int:
    minimum, maximum = 15_000, 2_500_000
    return minimum + int((rng.random() ** 2) * (maximum - minimum))


def _sum_net(rows: list[dict[str, Any]]) -> int:
    return sum(int(row["net_paise"]) for row in rows)


def build_world(records: int, seed: int, profile: str, rng: random.Random,
                fees: FeeConfig, timing: TimingConfig) -> World:
    if records < 50:
        raise ValueError("records must be at least 50")
    world = World(seed=seed, profile=profile, records=records)
    start = datetime(2026, 7, 16, 9, 0, 0)

    for index in range(records):
        created = start + timedelta(
            days=rng.randrange(30), hours=rng.randrange(9), minutes=rng.randrange(60)
        )
        channel = _weighted_channel(rng)
        status = _status(rng)
        amount = _amount(rng)
        order_id = f"order_{index:06d}"
        payment_id = _token(rng, "pay_") if status is not OrderStatus.FAILED else None
        order_payment_id = payment_id if payment_id and rng.random() < 0.90 else None
        order = {
            "source_row_id": f"orders:{index + 2}", "order_id": order_id,
            "created_at": created.isoformat(), "amount_paise": amount,
            "status": status.value, "channel": channel.value,
            "payment_id": order_payment_id or "",
        }
        world.orders.append(order)
        if status is OrderStatus.FAILED:
            world.tier_labels[order_id] = "T0"
            continue

        assert payment_id is not None
        captured = created + timedelta(minutes=rng.randint(2, 240))
        fee, tax = fee_for(amount, channel, fees)
        txn = {
            "source_row_id": "", "entity_id": payment_id, "type": "payment",
            "payment_id": "", "refund_id": "",
            "order_id": order_id if rng.random() < 0.90 else "",
            "method": channel.value, "gross_paise": amount,
            "fee_paise": fee, "tax_paise": tax, "net_paise": amount - fee - tax,
            "captured_at": captured.isoformat(), "settlement_id": "",
            "settlement_utr": "", "settlement_processed_at": "",
        }
        world.txns.append(txn)
        world.expectations["plane_a_matches"].append([payment_id, order_id])
        plane_a_tier = "T1" if fee else "T0"
        world.tier_labels[order_id] = plane_a_tier
        world.tier_labels[payment_id] = plane_a_tier

        if status in {OrderStatus.REFUNDED, OrderStatus.PARTIAL_REFUND}:
            refund_amount = amount if status is OrderStatus.REFUNDED else max(100, amount * rng.randint(25, 75) // 100)
            refund_id = _token(rng, "rfnd_")
            refund_captured = captured + timedelta(days=rng.randint(1, 4), minutes=rng.randrange(180))
            world.txns.append({
                "source_row_id": "", "entity_id": refund_id, "type": "refund",
                "payment_id": payment_id, "refund_id": refund_id, "order_id": order_id,
                "method": "", "gross_paise": -refund_amount, "fee_paise": 0,
                "tax_paise": 0, "net_paise": -refund_amount,
                "captured_at": refund_captured.isoformat(), "settlement_id": "",
                "settlement_utr": "", "settlement_processed_at": "",
            })
            world.tier_labels[refund_id] = "T4"
            world.tier_labels[order_id] = "T4"
            world.tier_labels[payment_id] = "T4"

    # Small adjustments exercise signed batch arithmetic and null member UTR handling.
    for index in range(max(1, records // 250)):
        captured = start + timedelta(days=rng.randrange(30), hours=rng.randrange(8))
        amount = rng.choice((-1, 1)) * rng.randint(100, 5_000)
        world.txns.append({
            "source_row_id": "", "entity_id": _token(rng, "adj_"), "type": "adjustment",
            "payment_id": "", "refund_id": "", "order_id": "", "method": "",
            "gross_paise": amount, "fee_paise": 0, "tax_paise": 0,
            "net_paise": amount, "captured_at": captured.isoformat(),
            "settlement_id": "", "settlement_utr": "", "settlement_processed_at": "",
        })

    by_processed: dict[str, list[dict[str, Any]]] = {}
    for txn in world.txns:
        captured = datetime.fromisoformat(str(txn["captured_at"]))
        processed = add_business_days(captured.date(), timing.settlement_cycle_bd)
        by_processed.setdefault(processed.isoformat(), []).append(txn)

    batch_rows: list[tuple[str, str, list[dict[str, Any]], str]] = []
    for day_index, (processed_day, rows) in enumerate(sorted(by_processed.items())):
        groups = [rows]
        if day_index % 10 == 4 and len(rows) >= 4:
            first, second = rows[::2], rows[1::2]
            if _sum_net(first) == _sum_net(second):
                second.append(first.pop())
            groups = [first, second]
            world.structural["multi_settlement_days"].append(processed_day)
        for group in groups:
            settlement_id = _token(rng, "setl_")
            utr, _family = make_utr(rng)
            processed_at = f"{processed_day}T17:00:00"
            for txn in group:
                txn["settlement_id"] = settlement_id
                # Adjustment rows deliberately carry null UTR like the official recon sample.
                txn["settlement_utr"] = "" if txn["type"] == "adjustment" else utr
                txn["settlement_processed_at"] = processed_at
            batch_rows.append((settlement_id, utr, group, processed_at))

    balance = 2_500_000
    for index, (settlement_id, utr, members, processed_at) in enumerate(batch_rows):
        amount = _sum_net(members)
        if amount <= 0:
            # Synthetic account has positive volume; an adjustment makes this explicit if a rare draw does not.
            adjustment = {
                "source_row_id": "", "entity_id": _token(rng, "adj_"), "type": "adjustment",
                "payment_id": "", "refund_id": "", "order_id": "", "method": "",
                "gross_paise": abs(amount) + 10_000, "fee_paise": 0, "tax_paise": 0,
                "net_paise": abs(amount) + 10_000,
                "captured_at": (datetime.fromisoformat(processed_at) - timedelta(days=2)).isoformat(),
                "settlement_id": settlement_id, "settlement_utr": "",
                "settlement_processed_at": processed_at,
            }
            world.txns.append(adjustment)
            members.append(adjustment)
            amount = _sum_net(members)
        processed_date = datetime.fromisoformat(processed_at).date()
        lag = rng.choice(timing.bank_lag_bd_choices)
        value_date = add_business_days(processed_date, lag)
        line_id = f"bank_{index:05d}"
        balance += amount
        world.bank.append({
            "source_row_id": f"bank:{index + 2}", "line_id": line_id,
            "txn_date": value_date.isoformat(), "value_date": value_date.isoformat(),
            "narration": clean_narration(rng, utr), "credit_paise": amount,
            "debit_paise": 0, "balance_paise": balance, "ref_no": utr,
        })
        world.expectations["plane_b_matches"].append([settlement_id, line_id])
        batch_tier = "T3" if len(members) >= 5 else "T2" if lag else "T0"
        world.tier_labels[settlement_id] = batch_tier
        world.tier_labels[line_id] = batch_tier

    for index, txn in enumerate(sorted(world.txns, key=lambda row: str(row["entity_id"]))):
        txn["source_row_id"] = f"recon:{index + 2}"
    return world
