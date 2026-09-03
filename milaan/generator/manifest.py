"""Ground-truth manifest construction and completeness validation."""

from __future__ import annotations

from typing import Any

from milaan.generator.world import World


GENERATOR_VERSION = "1.3.0"


def _match_facts(world: World) -> dict[str, dict[str, dict[str, Any]]]:
    orders = {str(row["order_id"]): row for row in world.orders}
    txns = {str(row["entity_id"]): row for row in world.txns}
    bank = {str(row["line_id"]): row for row in world.bank}
    plane_a: dict[str, dict[str, Any]] = {}
    for payment_id, order_id in world.expectations["plane_a_matches"]:
        payment, order = txns[payment_id], orders[order_id]
        plane_a[payment_id] = {
            "order_id": order_id,
            "payment_gross_paise": int(payment["gross_paise"]),
            "order_amount_paise": int(order["amount_paise"]),
        }
    plane_b: dict[str, dict[str, Any]] = {}
    for settlement_id, bank_line_id in world.expectations["plane_b_matches"]:
        members = sorted(str(row["entity_id"]) for row in world.txns
                         if row["settlement_id"] == settlement_id)
        batch_amount = sum(int(txns[txn_id]["net_paise"]) for txn_id in members)
        plane_b[settlement_id] = {
            "bank_line_id": bank_line_id,
            "member_txn_ids": members,
            "batch_amount_paise": batch_amount,
            "bank_credit_paise": int(bank[bank_line_id]["credit_paise"]),
        }
    return {"A": plane_a, "B": plane_b}


def build_manifest(world: World) -> dict[str, Any]:
    manifest = {
        "seed": world.seed,
        "profile": world.profile,
        "generator_version": GENERATOR_VERSION,
        "expectations": world.expectations,
        "tier_labels": world.tier_labels,
        "match_facts": _match_facts(world),
        "source_counts": {
            "orders": len(world.orders),
            "gateway_recon": len(world.txns),
            "bank": len(world.bank),
        },
        "structural": world.structural,
        "injected": world.injected,
    }
    validate_manifest(world, manifest)
    return manifest


def validate_manifest(world: World, manifest: dict[str, Any]) -> None:
    exp = manifest["expectations"]
    a_matches = {tuple(pair) for pair in exp["plane_a_matches"]}
    b_matches = {tuple(pair) for pair in exp["plane_b_matches"]}
    exception_ids = {entity_id for item in exp["exceptions"] for entity_id in item["ids"]}

    if len(a_matches) != len(exp["plane_a_matches"]):
        raise AssertionError("duplicate Plane-A expectations")
    if len(b_matches) != len(exp["plane_b_matches"]):
        raise AssertionError("duplicate Plane-B expectations")

    actual_payment_ids = {str(t["entity_id"]) for t in world.txns if t["type"] == "payment"}
    actual_nonfailed_orders = {str(o["order_id"]) for o in world.orders if o["status"] != "FAILED"}
    expected_payment_ids = {left for left, _right in a_matches}
    expected_order_ids = {right for _left, right in a_matches}
    if actual_payment_ids != expected_payment_ids:
        raise AssertionError("Plane-A payment expectations are incomplete")
    if actual_nonfailed_orders != expected_order_ids | (actual_nonfailed_orders & exception_ids):
        raise AssertionError("Plane-A order expectations are incomplete")

    actual_batches = {str(t["settlement_id"]) for t in world.txns if t["settlement_id"]}
    actual_bank = {str(line["line_id"]) for line in world.bank}
    expected_batches = {left for left, _right in b_matches}
    expected_bank = {right for _left, right in b_matches}
    if actual_batches != expected_batches | (actual_batches & exception_ids):
        raise AssertionError("Plane-B batch expectations are incomplete")
    if actual_bank != expected_bank | (actual_bank & exception_ids):
        raise AssertionError("Plane-B bank expectations are incomplete")

    matched_entities = expected_payment_ids | expected_order_ids | expected_batches | expected_bank
    if matched_entities & exception_ids:
        raise AssertionError("an entity appears in both match and exception expectations")

    injected_ids = [entity_id for item in manifest["injected"] for entity_id in item["ids"]]
    if len(injected_ids) != len(set(injected_ids)):
        raise AssertionError("injection targets are not disjoint")
