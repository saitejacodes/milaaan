"""Bounded, deterministic identifier recovery. No model is involved."""

from __future__ import annotations

from dataclasses import dataclass

from milaan.engine.confidence import confidence_for
from milaan.engine.plane_b import PlaneBState, normalize_identifier
from milaan.engine.rules import CONFUSABLE_PAIRS, SUFFIX_MIN_LENGTH
from milaan.generator.calendar import business_gap, forward_business_gap
from milaan.models import (
    BankLine, Decision, ExceptionItem, MatchKind, MatchTier, Plane, SettlementBatch,
)


@dataclass(frozen=True)
class RecoveryHit:
    settlement_id: str
    bank_line_id: str
    kind: str
    start: int
    end: int
    raw_text: str
    normalized_text: str
    substitution_position: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "settlement_id": self.settlement_id, "bank_line_id": self.bank_line_id,
            "kind": self.kind, "start": self.start, "end": self.end,
            "raw_text": self.raw_text, "normalized_text": self.normalized_text,
            "substitution_position": self.substitution_position,
        }


def _normalize_with_positions(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isspace() or char == "-":
            continue
        chars.append(char.casefold())
        positions.append(index)
    return "".join(chars), positions


def _span(original: str, positions: list[int], start: int, end: int) -> tuple[int, int, str]:
    original_start = positions[start]
    original_end = positions[end - 1] + 1
    return original_start, original_end, original[original_start:original_end]


def _is_confusable(left: str, right: str) -> bool:
    return frozenset((left.casefold(), right.casefold())) in CONFUSABLE_PAIRS


def hits_for_pair(batch: SettlementBatch, line: BankLine,
                  candidate_utrs: list[tuple[str, str]]) -> list[RecoveryHit]:
    if not batch.settlement_utr:
        return []
    utr = normalize_identifier(batch.settlement_utr)
    haystack, positions = _normalize_with_positions(line.narration)
    hits: list[RecoveryHit] = []

    full_at = haystack.find(utr)
    if full_at >= 0:
        start, end, raw = _span(line.narration, positions, full_at, full_at + len(utr))
        return [RecoveryHit(batch.settlement_id, line.line_id, "FULL", start, end, raw, utr)]

    for length in range(len(utr) - 1, SUFFIX_MIN_LENGTH - 1, -1):
        suffix = utr[-length:]
        at = haystack.find(suffix)
        if at < 0:
            continue
        owners = {sid for sid, candidate in candidate_utrs
                  if normalize_identifier(candidate).endswith(suffix)}
        if owners == {batch.settlement_id}:
            start, end, raw = _span(line.narration, positions, at, at + length)
            return [RecoveryHit(batch.settlement_id, line.line_id, "SUFFIX", start, end, raw, suffix)]
        # A colliding suffix is deliberately returned as edges for every owner by
        # their own pair scan; mutual uniqueness will abstain.
        break

    if len(haystack) >= len(utr):
        for at in range(len(haystack) - len(utr) + 1):
            window = haystack[at:at + len(utr)]
            mismatch_positions = [i for i, (a, b) in enumerate(zip(window, utr)) if a != b]
            if len(mismatch_positions) != 1:
                continue
            position = mismatch_positions[0]
            if not _is_confusable(window[position], utr[position]):
                continue
            start, end, raw = _span(line.narration, positions, at, at + len(utr))
            hits.append(RecoveryHit(
                batch.settlement_id, line.line_id, "CORRUPTED", start, end, raw,
                window, substitution_position=position,
            ))
            break
    return hits


def collect_recovery_hits(batches: list[SettlementBatch], bank: list[BankLine],
                          window_b_bd: int) -> list[RecoveryHit]:
    candidates = [(b.settlement_id, b.settlement_utr) for b in batches
                  if b.settlement_utr and not b.tainted]
    typed_candidates = [(sid, str(utr)) for sid, utr in candidates]
    hits: list[RecoveryHit] = []
    for batch in batches:
        if batch.tainted or not batch.settlement_utr:
            continue
        for line in bank:
            if line.credit_paise != batch.amount_paise:
                continue
            gap = forward_business_gap(batch.processed_at.date(), line.value_date)
            if gap is None or gap > window_b_bd:
                continue
            hits.extend(hits_for_pair(batch, line, typed_candidates))
    return hits


def deferred_bank_ids(hits: list[RecoveryHit]) -> set[str]:
    return {hit.bank_line_id for hit in hits}


def apply_recovery(state: PlaneBState, batches: list[SettlementBatch], bank: list[BankLine],
                   hits: list[RecoveryHit], window_b_bd: int) -> None:
    batch_map = {batch.settlement_id: batch for batch in batches}
    bank_map = {line.line_id: line for line in bank}
    usable = [hit for hit in hits
              if hit.settlement_id not in state.matched_batch_ids | state.blocked_batch_ids
              and hit.bank_line_id not in state.matched_bank_ids | state.blocked_bank_ids]
    by_batch: dict[str, set[str]] = {}
    by_bank: dict[str, set[str]] = {}
    hit_map: dict[tuple[str, str], RecoveryHit] = {}
    for hit in usable:
        by_batch.setdefault(hit.settlement_id, set()).add(hit.bank_line_id)
        by_bank.setdefault(hit.bank_line_id, set()).add(hit.settlement_id)
        prior = hit_map.get((hit.settlement_id, hit.bank_line_id))
        rank = {"FULL": 3, "SUFFIX": 2, "CORRUPTED": 1}
        if prior is None or rank[hit.kind] > rank[prior.kind]:
            hit_map[(hit.settlement_id, hit.bank_line_id)] = hit

    for sid in sorted(by_batch):
        line_ids = by_batch[sid]
        if len(line_ids) != 1:
            continue
        line_id = next(iter(line_ids))
        if len(by_bank[line_id]) != 1:
            continue
        batch, line, hit = batch_map[sid], bank_map[line_id], hit_map[(sid, line_id)]
        gap = business_gap(batch.processed_at.date(), line.value_date)
        decision = Decision(
            plane=Plane.B, kind=MatchKind.BATCH_BANK, left_ids=(sid,), right_id=line_id,
            tier=MatchTier.B2, amount_diff_paise=0, date_gap_bd=gap,
            confidence=confidence_for("B2", gap, 0, hit.kind),
            evidence={
                "settlement_id": sid, "bank_line_id": line_id,
                "batch_amount_paise": batch.amount_paise, "credit_paise": line.credit_paise,
                "utr": batch.settlement_utr, "rule": "B2", "recovery": hit.as_dict(),
            },
        )
        state.decisions.append(decision)
        state.matched_batch_ids.add(sid); state.matched_bank_ids.add(line_id)

    ambiguous_edges = [(sid, line_id) for sid, line_ids in by_batch.items()
                       for line_id in line_ids
                       if sid not in state.matched_batch_ids
                       and (len(line_ids) > 1 or len(by_bank[line_id]) > 1)]
    if ambiguous_edges:
        ids = sorted({entity for edge in ambiguous_edges for entity in edge})
        state.exceptions.append(ExceptionItem(tuple(ids), "AMBIGUOUS_TIE", 1.0, {
            "plane": "B", "recovery_hits": [hit_map[edge].as_dict() for edge in sorted(ambiguous_edges)],
        }))
        state.blocked_batch_ids.update(sid for sid, _ in ambiguous_edges)
        state.blocked_bank_ids.update(line_id for _, line_id in ambiguous_edges)
