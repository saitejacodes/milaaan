"""Pure confidence functions; confidence never changes acceptance."""

from __future__ import annotations

from milaan.engine.rules import (
    A0_CONFIDENCE, A1_CONFIDENCE, B0_CONFIDENCE,
    B2_CORRUPTED_CONFIDENCE, B2_FULL_CONFIDENCE, B2_SUFFIX_CONFIDENCE,
)


def confidence_for(tier: str, gap_bd: int = 0, diff_paise: int = 0,
                   recovery_kind: str | None = None) -> float:
    if tier in {"A0", "A0B"}:
        return A0_CONFIDENCE
    if tier == "A1":
        return A1_CONFIDENCE
    if tier == "B0":
        return B0_CONFIDENCE
    if tier == "B1":
        return round(0.95 - 0.01 * gap_bd - (0.03 if diff_paise else 0), 4)
    if tier == "B2":
        return {
            "FULL": B2_FULL_CONFIDENCE,
            "SUFFIX": B2_SUFFIX_CONFIDENCE,
            "CORRUPTED": B2_CORRUPTED_CONFIDENCE,
        }[recovery_kind or "FULL"]
    raise ValueError(f"unknown tier: {tier}")
