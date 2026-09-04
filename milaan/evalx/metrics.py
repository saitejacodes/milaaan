"""Independent functional metrics with evidence and conservation checks."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from milaan.evalx.truth import require_complete_truth


def _ratio(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else 1.0,
    }


def _actual_matches(conn: sqlite3.Connection, plane: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT m.match_id,m.amount_diff_paise,m.date_gap_bd,m.evidence,
                  mm.entity_type,mm.entity_id
           FROM matches m JOIN match_members mm ON mm.match_id=m.match_id
           WHERE m.plane=? ORDER BY m.match_id,mm.entity_type,mm.entity_id""",
        (plane,),
    ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        match = grouped.setdefault(int(row["match_id"]), {
            "match_id": int(row["match_id"]),
            "amount_diff_paise": int(row["amount_diff_paise"]),
            "date_gap_bd": int(row["date_gap_bd"]),
            "evidence": json.loads(row["evidence"]),
            "members": defaultdict(list),
        })
        match["members"][row["entity_type"]].append(row["entity_id"])
    actual: list[dict[str, Any]] = []
    for match in grouped.values():
        members = match.pop("members")
        if plane == "A":
            match["pair"] = (members["TXN"][0], members["ORDER"][0])
        else:
            match["pair"] = (members["BATCH"][0], members["BANK_LINE"][0])
        actual.append(match)
    return actual


def _maximum_exception_matching(expected: list[dict[str, Any]],
                                actual: list[dict[str, Any]]) -> tuple[int, set[int]]:
    edges = {
        index: [candidate for candidate, item in enumerate(actual)
                if frozenset(want["ids"]) == item["ids"]
                and item["code"] in want["allowed_codes"]]
        for index, want in enumerate(expected)
    }
    actual_owner: dict[int, int] = {}

    def augment(expected_index: int, seen: set[int]) -> bool:
        for actual_index in edges[expected_index]:
            if actual_index in seen:
                continue
            seen.add(actual_index)
            owner = actual_owner.get(actual_index)
            if owner is None or augment(owner, seen):
                actual_owner[actual_index] = expected_index
                return True
        return False

    matched = sum(augment(index, set()) for index in range(len(expected)))
    return matched, set(actual_owner)


def _source_conservation(
    orders: dict[str, sqlite3.Row],
    txns: dict[str, sqlite3.Row],
    bank: dict[str, sqlite3.Row],
    quarantine: list[sqlite3.Row],
    actual_a: list[dict[str, Any]],
    actual_b: list[dict[str, Any]],
    exception_ids: set[str],
) -> dict[str, Any]:
    matched_orders = {item["pair"][1] for item in actual_a}
    matched_payments = {item["pair"][0] for item in actual_a}
    matched_batches = {item["pair"][0] for item in actual_b}
    matched_bank = {item["pair"][1] for item in actual_b}
    buckets: Counter[str] = Counter()
    unaccounted: list[str] = []

    for order_id, row in orders.items():
        if row["status"] == "FAILED":
            buckets["ignored_failed_order"] += 1
        elif order_id in exception_ids:
            buckets["exception"] += 1
        elif order_id in matched_orders:
            buckets["matched"] += 1
        else:
            unaccounted.append(order_id)

    for txn_id, row in txns.items():
        settlement_id = row["settlement_id"]
        exception_path = txn_id in exception_ids or (
            bool(settlement_id) and settlement_id in exception_ids
        )
        plane_a_done = row["txn_type"] != "PAYMENT" or txn_id in matched_payments
        plane_b_done = bool(settlement_id) and settlement_id in matched_batches
        if exception_path:
            buckets["exception"] += 1
        elif plane_a_done and plane_b_done:
            buckets["matched"] += 1
        else:
            unaccounted.append(txn_id)

    for line_id in bank:
        if line_id in exception_ids:
            buckets["exception"] += 1
        elif line_id in matched_bank:
            buckets["matched"] += 1
        else:
            unaccounted.append(line_id)

    # Quarantine is an explicit terminal state even when malformed source data
    # cannot provide a trustworthy business identifier.
    buckets["quarantined"] += len(quarantine)
    total = len(orders) + len(txns) + len(bank) + len(quarantine)
    accounted = total - len(unaccounted)
    return {
        **_ratio(accounted, total),
        "total_source_records": total,
        "terminal_buckets": dict(sorted(buckets.items())),
        "unaccounted_ids": sorted(unaccounted),
    }


def compute_metrics(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM runs").fetchone()
    orders = {row["order_id"]: row for row in conn.execute("SELECT * FROM raw_orders")}
    txns = {row["txn_id"]: row for row in conn.execute("SELECT * FROM raw_txns")}
    bank = {row["line_id"]: row for row in conn.execute("SELECT * FROM raw_bank")}
    batches = {row["settlement_id"]: row
               for row in conn.execute("SELECT * FROM settlement_batches")}
    quarantine = list(conn.execute("SELECT * FROM quarantine_rows ORDER BY id"))
    members_by_batch: dict[str, set[str]] = defaultdict(set)
    for txn_id, row in txns.items():
        if row["settlement_id"]:
            members_by_batch[row["settlement_id"]].add(txn_id)

    actual_by_plane = {plane: _actual_matches(conn, plane) for plane in ("A", "B")}
    # Mandatory truth contract. Missing or partial facts fail closed here rather
    # than degrading to a weaker identifier-only comparison.
    require_complete_truth(manifest)
    tier_labels = manifest["tier_labels"]
    match_facts = manifest["match_facts"]
    planes: dict[str, Any] = {}
    all_expected_entities: set[str] = set()
    actual_bucket_counts: Counter[str] = Counter()

    for plane, key in (("A", "plane_a_matches"), ("B", "plane_b_matches")):
        expected = {tuple(pair) for pair in manifest["expectations"][key]}
        facts = match_facts[plane]
        correct_pairs: set[tuple[str, str]] = set()
        correct_match_ids: set[int] = set()
        for item in actual_by_plane[plane]:
            pair = item["pair"]
            valid = pair in expected
            if valid:
                # Amount, membership and identifier facts are always checked. A
                # pair whose money no longer equals the canonical money is a
                # false match, never a correct one.
                if plane == "A":
                    payment_id, order_id = pair
                    fact = facts.get(payment_id)
                    payment, order = txns.get(payment_id), orders.get(order_id)
                    valid = bool(
                        fact and fact["order_id"] == order_id and payment and order
                        and int(payment["gross_paise"]) == int(fact["payment_gross_paise"])
                        and int(order["amount_paise"]) == int(fact["order_amount_paise"])
                        and int(item["amount_diff_paise"]) == 0
                    )
                else:
                    settlement_id, bank_line_id = pair
                    fact = facts.get(settlement_id)
                    batch, line = batches.get(settlement_id), bank.get(bank_line_id)
                    valid = bool(
                        fact and fact["bank_line_id"] == bank_line_id and batch and line
                        and int(batch["amount_paise"]) == int(fact["batch_amount_paise"])
                        and int(line["credit_paise"]) == int(fact["bank_credit_paise"])
                        and members_by_batch.get(settlement_id, set())
                        == set(fact["member_txn_ids"])
                        and int(item["amount_diff_paise"]) == 0
                    )
            if valid:
                correct_pairs.add(pair)
                correct_match_ids.add(item["match_id"])
            actual_bucket_counts.update(pair)
        for pair in expected:
            all_expected_entities.update(pair)
        tiers: dict[str, dict[str, int | float]] = {}
        for tier in sorted({tier_labels.get(pair[0], "T0") for pair in expected}):
            expected_tier = {pair for pair in expected if tier_labels.get(pair[0], "T0") == tier}
            tiers[tier] = _ratio(len(correct_pairs & expected_tier), len(expected_tier))
        found_count = len(actual_by_plane[plane])
        false_count = found_count - len(correct_match_ids)
        planes[plane] = {
            "auto_match": _ratio(len(correct_pairs), len(expected)),
            "true_match_count": len(correct_match_ids),
            "expected_match_recall": _ratio(len(correct_pairs), len(expected)),
            "match_precision": _ratio(len(correct_match_ids), found_count),
            "found_count": found_count,
            "expected_count": len(expected),
            "false_match_count": false_count,
            "false_match_rate": _ratio(false_count, found_count),
            "tier_coverage": tiers,
        }

    actual_exception_rows = conn.execute(
        "SELECT scope_ids,reason_code,evidence FROM exceptions ORDER BY id"
    ).fetchall()
    actual_exceptions = [
        {"ids": frozenset(json.loads(row["scope_ids"])), "code": row["reason_code"],
         "evidence": json.loads(row["evidence"])}
        for row in actual_exception_rows
    ]
    exception_ids = {entity_id for item in actual_exceptions for entity_id in item["ids"]}
    for item in actual_exceptions:
        actual_bucket_counts.update(item["ids"])
    expected_exceptions = manifest["expectations"]["exceptions"]
    all_expected_entities.update(entity_id for item in expected_exceptions for entity_id in item["ids"])
    recalled, matched_actual = _maximum_exception_matching(expected_exceptions, actual_exceptions)
    exactly_one = sum(actual_bucket_counts[entity_id] == 1 for entity_id in all_expected_entities)

    run_hashes = {
        "orders": run["orders_sha256"],
        "gateway_recon": run["txns_sha256"],
        "bank": run["bank_sha256"],
    }
    manifest_hashes = manifest.get("input_hashes", {})
    hash_checks = {name: manifest_hashes.get(name) == value
                   for name, value in run_hashes.items()}
    input_integrity = {
        "available": bool(manifest_hashes),
        "all_match": bool(manifest_hashes) and all(hash_checks.values()),
        "checks": hash_checks,
    }

    matched_a_orders = len({item["pair"][1] for item in actual_by_plane["A"]})
    matched_a_payments = len({item["pair"][0] for item in actual_by_plane["A"]})
    eligible_orders = sum(row["status"] != "FAILED" for row in orders.values())
    payment_count = sum(row["txn_type"] == "PAYMENT" for row in txns.values())
    all_settlement_ids = set(batches) | {
        row["settlement_id"] for row in quarantine if row["settlement_id"]
    }
    matched_b_batches = {item["pair"][0] for item in actual_by_plane["B"]}
    matched_b_bank = {item["pair"][1] for item in actual_by_plane["B"]}
    workload_coverage = {
        "plane_a_orders": _ratio(matched_a_orders, eligible_orders),
        "plane_a_payments": _ratio(matched_a_payments, payment_count),
        "plane_b_settlement_batches": _ratio(len(matched_b_batches), len(all_settlement_ids)),
        "plane_b_bank_lines": _ratio(len(matched_b_bank), len(bank)),
    }

    source_net = sum(int(row["net_paise"]) for row in txns.values() if row["settlement_id"])
    batch_total = sum(int(row["amount_paise"]) for row in batches.values())
    amount_conservation = {
        "source_net_paise": source_net,
        "batch_total_paise": batch_total,
        "delta_paise": batch_total - source_net,
        "balanced": batch_total == source_net,
    }

    exception_codes_by_id: dict[str, set[str]] = defaultdict(set)
    exception_evidence_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in actual_exceptions:
        for entity_id in item["ids"]:
            exception_codes_by_id[entity_id].add(item["code"])
            exception_evidence_by_id[entity_id].append(item["evidence"])
    def _settlement_exposure(settlement_id: str) -> int:
        """What this settlement claims, even when no batch row survived ingestion.

        A settlement whose members were *all* rejected has no aggregated batch
        row at all. Reading exposure only from settlement_batches would make that
        money silently disappear from the cash report, so the engine's own
        claimed exposure -- recorded on the exception -- is used instead.
        """
        for evidence in exception_evidence_by_id.get(settlement_id, ()):
            if "claimed_exposure_paise" in evidence:
                return int(evidence["claimed_exposure_paise"])
        row = batches.get(settlement_id)
        return int(row["amount_paise"]) if row is not None else 0

    matched_settlement_paise = sum(int(batches[sid]["amount_paise"])
                                   for sid in matched_b_batches if sid in batches)
    banked_paise = sum(int(bank[line_id]["credit_paise"])
                       for line_id in matched_b_bank if line_id in bank)

    # Every settlement lands in exactly one cash bucket. The classification is
    # built as a mapping rather than three running totals so that double
    # counting and omission are both detectable, not merely unlikely.
    settlement_bucket: dict[str, str] = {}
    expected_unbanked_paise = 0
    blocked_paise = 0
    for settlement_id in sorted(all_settlement_ids):
        if settlement_id in matched_b_batches:
            settlement_bucket[settlement_id] = "banked"
            continue
        exposure = _settlement_exposure(settlement_id)
        if exception_codes_by_id.get(settlement_id) == {"MISSING_IN_BANK"}:
            settlement_bucket[settlement_id] = "expected_unbanked"
            expected_unbanked_paise += exposure
        else:
            settlement_bucket[settlement_id] = "blocked"
            blocked_paise += exposure

    bank_bucket = {line_id: ("banked" if line_id in matched_b_bank else "unexplained")
                   for line_id in bank}
    unexplained_bank_paise = sum(int(bank[line_id]["credit_paise"])
                                 for line_id, bucket in bank_bucket.items()
                                 if bucket == "unexplained")

    cash_position = {
        "banked_paise": banked_paise,
        "matched_settlement_paise": matched_settlement_paise,
        "matched_amount_delta_paise": banked_paise - matched_settlement_paise,
        "expected_unbanked_paise": expected_unbanked_paise,
        "blocked_settlement_paise": blocked_paise,
        "unexplained_bank_credit_paise": unexplained_bank_paise,
        "gross_attention_paise": expected_unbanked_paise + blocked_paise + unexplained_bank_paise,
        "total_bank_credit_paise": sum(int(row["credit_paise"]) for row in bank.values()),
        "total_settlement_control_paise": matched_settlement_paise
                                            + expected_unbanked_paise + blocked_paise,
        "classification": {
            "settlements": _ratio(len(settlement_bucket), len(all_settlement_ids)),
            "bank_lines": _ratio(len(bank_bucket), len(bank)),
            "settlement_buckets": dict(sorted(
                Counter(settlement_bucket.values()).items()
            )),
            "bank_line_buckets": dict(sorted(Counter(bank_bucket.values()).items())),
            "unclassified_settlement_ids": sorted(all_settlement_ids - set(settlement_bucket)),
            "unclassified_bank_line_ids": sorted(set(bank) - set(bank_bucket)),
        },
    }

    return {
        "schema_version": "2.0.0",
        "seed": int(manifest["seed"]),
        "profile": manifest["profile"],
        "generator_version": manifest["generator_version"],
        "input_hashes": {
            **run_hashes,
            "fees": run["fees_config_sha256"],
            "timing": run["timing_config_sha256"],
        },
        "input_integrity": input_integrity,
        "planes": planes,
        "workload_coverage": workload_coverage,
        "false_match_count": planes["A"]["false_match_count"]
                             + planes["B"]["false_match_count"],
        "exceptions": {
            "recall": _ratio(recalled, len(expected_exceptions)),
            "precision": _ratio(len(matched_actual), len(actual_exceptions)),
            "actual_count": len(actual_exceptions),
            "expected_count": len(expected_exceptions),
            "correct_count": len(matched_actual),
            "false_count": len(actual_exceptions) - len(matched_actual),
            "missed_count": len(expected_exceptions) - recalled,
        },
        "benchmark_entity_completeness": _ratio(exactly_one, len(all_expected_entities)),
        "completeness": _ratio(exactly_one, len(all_expected_entities)),
        "source_record_conservation": _source_conservation(
            orders, txns, bank, quarantine, actual_by_plane["A"], actual_by_plane["B"],
            exception_ids,
        ),
        "amount_conservation": amount_conservation,
        "cash_position": cash_position,
    }
