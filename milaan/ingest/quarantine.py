"""Quarantine construction keeps rejected source rows available for audit."""

from __future__ import annotations

from milaan.models import QuarantineRow


def quarantined(source: str, raw: dict[str, str], reason: str) -> QuarantineRow:
    return QuarantineRow(
        source=source,
        source_row_id=raw.get("source_row_id") or f"{source}:unknown",
        raw=dict(raw),
        reason=reason,
        settlement_id=raw.get("settlement_id") or None,
    )
