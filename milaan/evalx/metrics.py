"""Deterministic functional metrics with explicit numerators and denominators."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any


def _actual_pairs(conn: sqlite3.Connection, plane: str) -> set[tuple[str, str]]:
    rows = conn.execute(
        """SELECT m.match_id, mm.entity_type, mm.entity_id
           FROM matches m JOIN match_members mm ON mm.match_id=m.match_id
           WHERE m.plane=? ORDER BY m.match_id, mm.entity_type, mm.entity_id""", (plane,)
    ).fetchall()
    grouped: dict[int, dict[str, list[str]]] = {}
    for row in rows:
        grouped.setdefault(int(row["match_id"]), {}).setdefault(row["entity_type"], []).append(row["entity_id"])
    pairs: set[tuple[str, str]] = set()
    for members in grouped.values():
        if plane == "A":
            pairs.add((members["TXN"][0], members["ORDER"][0]))
        else:
            for sid in members["BATCH"]:
                pairs.add((sid, members["BANK_LINE"][0]))
    return pairs


def _ratio(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator, "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else 1.0,
    }


def compute_metrics(conn: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM runs").fetchone()
    tier_labels = manifest["tier_labels"]
    planes: dict[str, Any] = {}
    all_expected_entities: set[str] = set()
    actual_bucket_counts: Counter[str] = Counter()

    for plane, key in (("A", "plane_a_matches"), ("B", "plane_b_matches")):
        expected = {tuple(pair) for pair in manifest["expectations"][key]}
        found = _actual_pairs(conn, plane)
        correct, false = found & expected, found - expected
        for pair in found:
            actual_bucket_counts.update(pair)
        for pair in expected:
            all_expected_entities.update(pair)
        tiers: dict[str, dict[str, int | float]] = {}
        for tier in sorted({tier_labels.get(pair[0], "T0") for pair in expected}):
            expected_tier = {pair for pair in expected if tier_labels.get(pair[0], "T0") == tier}
            tiers[tier] = _ratio(len(found & expected_tier), len(expected_tier))
        planes[plane] = {
            "auto_match": _ratio(len(correct), len(expected)),
            "found_count": len(found), "expected_count": len(expected),
            "false_match_count": len(false),
            "false_match_rate": {
                "numerator": len(false), "denominator": len(found),
                "rate": round(len(false) / max(1, len(found)), 6),
            },
            "tier_coverage": tiers,
        }

    actual_exception_rows = conn.execute(
        "SELECT scope_ids,reason_code FROM exceptions ORDER BY id"
    ).fetchall()
    actual_exceptions = [
        {"ids": set(json.loads(row["scope_ids"])), "code": row["reason_code"]}
        for row in actual_exception_rows
    ]
    for item in actual_exceptions:
        actual_bucket_counts.update(item["ids"])
    expected_exceptions = manifest["expectations"]["exceptions"]
    all_expected_entities.update(entity_id for item in expected_exceptions for entity_id in item["ids"])

    def satisfies(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        return set(expected["ids"]).issubset(actual["ids"]) and actual["code"] in expected["allowed_codes"]

    recalled = sum(any(satisfies(expected, actual) for actual in actual_exceptions)
                   for expected in expected_exceptions)
    precise = sum(any(satisfies(expected, actual) for expected in expected_exceptions)
                  for actual in actual_exceptions)
    exactly_one = sum(actual_bucket_counts[entity_id] == 1 for entity_id in all_expected_entities)

    return {
        "schema_version": "1.2.1", "seed": int(manifest["seed"]),
        "profile": manifest["profile"], "generator_version": manifest["generator_version"],
        "input_hashes": {
            "orders": run["orders_sha256"], "gateway_recon": run["txns_sha256"],
            "bank": run["bank_sha256"], "fees": run["fees_config_sha256"],
            "timing": run["timing_config_sha256"],
        },
        "planes": planes,
        "false_match_count": planes["A"]["false_match_count"] + planes["B"]["false_match_count"],
        "exceptions": {
            "recall": _ratio(recalled, len(expected_exceptions)),
            "precision": _ratio(precise, len(actual_exceptions)),
            "actual_count": len(actual_exceptions), "expected_count": len(expected_exceptions),
        },
        "completeness": _ratio(exactly_one, len(all_expected_entities)),
    }
