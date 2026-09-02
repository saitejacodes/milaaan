"""Deterministic, disjoint anomaly injection for mixed and hard profiles."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from milaan.generator.narration import mangle_narration
from milaan.generator.world import World


def _remove_pair(pairs: list[list[str]], left: str, right: str) -> None:
    pairs[:] = [pair for pair in pairs if pair != [left, right]]


def _reserve(world: World, used: set[str], ids: list[str], tier: str,
             detail: dict[str, Any]) -> None:
    overlap = used.intersection(ids)
    if overlap:
        raise AssertionError(f"injection targets overlap: {sorted(overlap)}")
    used.update(ids)
    for entity_id in ids:
        world.tier_labels[entity_id] = tier
    world.injected.append({"tier": tier, "ids": ids, "detail": detail})


def _exception(world: World, ids: list[str], codes: list[str], tier: str) -> None:
    world.expectations["exceptions"].append({
        "ids": ids, "allowed_codes": codes, "tier": tier,
    })


def _batch_amount(world: World, settlement_id: str) -> int:
    return sum(int(t["net_paise"]) for t in world.txns if t["settlement_id"] == settlement_id)


def _batch_processed_at(world: World, settlement_id: str) -> datetime:
    values = {datetime.fromisoformat(str(t["settlement_processed_at"]))
              for t in world.txns if t["settlement_id"] == settlement_id}
    if len(values) != 1:
        raise AssertionError(f"settlement {settlement_id} has inconsistent processing dates")
    return next(iter(values))


def _add_balancing_adjustment(world: World, rng: random.Random, settlement_id: str,
                              delta: int, label: str) -> None:
    members = [t for t in world.txns if t["settlement_id"] == settlement_id]
    exemplar = members[0]
    world.txns.append({
        "source_row_id": "", "entity_id": f"adj_injected_{label}_{rng.randrange(10**9):09d}",
        "type": "adjustment", "payment_id": "", "refund_id": "", "order_id": "",
        "method": "", "gross_paise": delta, "fee_paise": 0, "tax_paise": 0,
        "net_paise": delta, "captured_at": exemplar["captured_at"],
        "settlement_id": settlement_id, "settlement_utr": "",
        "settlement_processed_at": exemplar["settlement_processed_at"],
    })


def _choose_available(lines: list[dict[str, Any]], used: set[str], count: int) -> list[dict[str, Any]]:
    available = [line for line in lines if line["line_id"] not in used]
    if len(available) < count:
        raise ValueError("not enough bank lines for disjoint anomaly injection")
    return available[:count]


def apply_injections(world: World, rng: random.Random) -> World:
    if world.profile == "clean":
        return world

    used: set[str] = set()
    bank_by_id = {str(line["line_id"]): line for line in world.bank}
    expected_by_line = {right: left for left, right in world.expectations["plane_b_matches"]}

    # T5: equal-amount, same-date twin with both UTRs removed. B1 must abstain.
    twin_a, twin_b = _choose_available(world.bank, used, 2)
    sid_a, sid_b = expected_by_line[twin_a["line_id"]], expected_by_line[twin_b["line_id"]]
    target = int(twin_a["credit_paise"])
    delta = target - int(twin_b["credit_paise"])
    if delta:
        _add_balancing_adjustment(world, rng, sid_b, delta, "twin")
    twin_b["credit_paise"] = target
    common_credit_date = max(_batch_processed_at(world, sid_a),
                             _batch_processed_at(world, sid_b)).date().isoformat()
    twin_a["value_date"] = common_credit_date
    twin_a["txn_date"] = common_credit_date
    twin_b["value_date"] = common_credit_date
    twin_b["txn_date"] = common_credit_date
    twin_a["narration"] = "NEFT-NOREF-SETTLEMENT-A"
    twin_b["narration"] = "IMPS-NOREF-SETTLEMENT-B"
    twin_a["ref_no"], twin_b["ref_no"] = "NOREF-A", "NOREF-B"
    twin_ids = [sid_a, sid_b, twin_a["line_id"], twin_b["line_id"]]
    _reserve(world, used, twin_ids, "T5", {"kind": "TWIN_CREDIT_TIE"})
    _remove_pair(world.expectations["plane_b_matches"], sid_a, twin_a["line_id"])
    _remove_pair(world.expectations["plane_b_matches"], sid_b, twin_b["line_id"])
    _exception(world, twin_ids, ["AMBIGUOUS_TIE"], "T5")

    # T5: one valid UTR on two different credits.
    duplicate_utr_source = _choose_available(world.bank, used, 1)[0]
    sid = expected_by_line[duplicate_utr_source["line_id"]]
    duplicate_utr = dict(duplicate_utr_source)
    duplicate_utr["line_id"] = f"bank_dup_utr_{world.seed}"
    duplicate_utr["source_row_id"] = "bank:injected-duplicate-utr"
    duplicate_utr["credit_paise"] = int(duplicate_utr["credit_paise"]) + 777
    duplicate_utr["balance_paise"] = 0
    world.bank.append(duplicate_utr)
    ids = [sid, duplicate_utr_source["line_id"], duplicate_utr["line_id"]]
    _reserve(world, used, ids, "T5", {"kind": "DUPLICATE_UTR"})
    _remove_pair(world.expectations["plane_b_matches"], sid, duplicate_utr_source["line_id"])
    _exception(world, ids, ["DUPLICATE_UTR"], "T5")

    # T5: exact duplicate bank row (new line id/source id only).
    clone_source = _choose_available(world.bank, used, 1)[0]
    clone_sid = expected_by_line[clone_source["line_id"]]
    clone = dict(clone_source)
    clone["line_id"] = f"bank_clone_{world.seed}"
    clone["source_row_id"] = "bank:injected-clone"
    world.bank.append(clone)
    ids = [clone_sid, clone_source["line_id"], clone["line_id"]]
    _reserve(world, used, ids, "T5", {"kind": "DUPLICATE_BANK_LINE"})
    _remove_pair(world.expectations["plane_b_matches"], clone_sid, clone_source["line_id"])
    _exception(world, ids, ["DUPLICATE_BANK_LINE"], "T5")

    # T6: recoverable narration damage remains an expected match.
    for operator in ("TRUNCATE_SUFFIX", "CONFUSABLE_SUB", "INSERT_SEPARATOR"):
        line = _choose_available(world.bank, used, 1)[0]
        sid = expected_by_line[line["line_id"]]
        utr = str(line["ref_no"])
        damaged, detail = mangle_narration(rng, str(line["narration"]), utr, operator)
        line["narration"] = damaged
        ids = [sid, line["line_id"]]
        _reserve(world, used, ids, "T6", detail)

    # T7: a missing bank credit leaves the batch unmatched.
    missing_line = _choose_available(world.bank, used, 1)[0]
    missing_sid = expected_by_line[missing_line["line_id"]]
    ids = [missing_sid, missing_line["line_id"]]
    _reserve(world, used, ids, "T7", {"kind": "DELETE_BANK_CREDIT"})
    world.bank.remove(missing_line)
    _remove_pair(world.expectations["plane_b_matches"], missing_sid, missing_line["line_id"])
    _exception(world, [missing_sid], ["MISSING_IN_BANK"], "T7")

    # T7: an unexplained credit has no candidate batch.
    last = world.bank[-1]
    unknown = {
        "source_row_id": "bank:injected-unknown", "line_id": f"bank_unknown_{world.seed}",
        "txn_date": last["txn_date"], "value_date": last["value_date"],
        "narration": "NEFT-UNRELATED-VENDOR-UNKNOWNREF", "credit_paise": 98_765,
        "debit_paise": 0, "balance_paise": 0, "ref_no": "UNKNOWNREF",
    }
    world.bank.append(unknown)
    _reserve(world, used, [unknown["line_id"]], "T7", {"kind": "UNKNOWN_CREDIT"})
    _exception(world, [unknown["line_id"]], ["UNKNOWN_BANK_CREDIT"], "T7")

    # T7: delete one PAID gateway payment, replacing only its batch contribution
    # with a neutral adjustment so the source-plane anomaly does not corrupt Plane B.
    paid_orders = {o["order_id"]: o for o in world.orders if o["status"] == "PAID"}
    payment = next(t for t in world.txns
                   if t["type"] == "payment" and t["order_id"] in paid_orders
                   and t["entity_id"] not in used)
    order_id, payment_id = str(payment["order_id"]), str(payment["entity_id"])
    replacement = dict(payment)
    replacement.update({
        "entity_id": f"adj_missing_{world.seed}", "type": "adjustment",
        "payment_id": "", "refund_id": "", "order_id": "", "method": "",
        "gross_paise": payment["net_paise"], "fee_paise": 0, "tax_paise": 0,
        "settlement_utr": "",
    })
    world.txns.remove(payment)
    world.txns.append(replacement)
    _remove_pair(world.expectations["plane_a_matches"], payment_id, order_id)
    _reserve(world, used, [order_id, payment_id], "T7", {"kind": "DELETE_GATEWAY_PAYMENT"})
    _exception(world, [order_id], ["PAID_ORDER_MISSING_FROM_GATEWAY"], "T7")

    if world.profile == "hard":
        # The unapproved solver is not smuggled in: make one combined credit and
        # explicitly expect a conservative combined ambiguity exception.
        first, second = _choose_available(world.bank, used, 2)
        first_sid, second_sid = expected_by_line[first["line_id"]], expected_by_line[second["line_id"]]
        combined = dict(first)
        combined["line_id"] = f"bank_combined_{world.seed}"
        combined["source_row_id"] = "bank:injected-combined"
        combined["credit_paise"] = int(first["credit_paise"]) + int(second["credit_paise"])
        combined["narration"] = "NEFT-COMBINED-SETTLEMENT-NOREF"
        combined["ref_no"] = "NOREF"
        common_credit_date = max(_batch_processed_at(world, first_sid),
                                 _batch_processed_at(world, second_sid)).date().isoformat()
        combined["value_date"] = common_credit_date
        combined["txn_date"] = common_credit_date
        world.bank.remove(first)
        world.bank.remove(second)
        world.bank.append(combined)
        ids = [first_sid, second_sid, combined["line_id"]]
        _reserve(world, used, ids, "T7", {"kind": "COMBINED_CREDIT"})
        _remove_pair(world.expectations["plane_b_matches"], first_sid, first["line_id"])
        _remove_pair(world.expectations["plane_b_matches"], second_sid, second["line_id"])
        _exception(world, ids, ["AMBIGUOUS_COMBINED"], "T7")

    # Recalculate presentation balances and source row ids after all edits.
    balance = 2_500_000
    for index, line in enumerate(sorted(world.bank, key=lambda x: (x["value_date"], x["line_id"]))):
        balance += int(line["credit_paise"]) - int(line["debit_paise"])
        line["balance_paise"] = balance
        if not str(line["source_row_id"]).startswith("bank:injected"):
            line["source_row_id"] = f"bank:{index + 2}"
    for index, txn in enumerate(sorted(world.txns, key=lambda row: str(row["entity_id"]))):
        txn["source_row_id"] = f"recon:{index + 2}"

    # This assertion is the generation contract, not merely a test fixture.
    all_injected_ids = [entity_id for item in world.injected for entity_id in item["ids"]]
    if len(all_injected_ids) != len(set(all_injected_ids)):
        raise AssertionError("injected entity targets are not disjoint")
    return world
