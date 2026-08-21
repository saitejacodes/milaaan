"""Plane B deterministic exact-identifier and amount/date matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from milaan.engine.confidence import confidence_for
from milaan.generator.calendar import business_gap
from milaan.models import (
    BankLine, Decision, ExceptionItem, MatchKind, MatchTier, Plane, SettlementBatch,
)


def normalize_identifier(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).casefold()


@dataclass
class PlaneBState:
    decisions: list[Decision] = field(default_factory=list)
    exceptions: list[ExceptionItem] = field(default_factory=list)
    matched_batch_ids: set[str] = field(default_factory=set)
    matched_bank_ids: set[str] = field(default_factory=set)
    blocked_batch_ids: set[str] = field(default_factory=set)
    blocked_bank_ids: set[str] = field(default_factory=set)


def _in_window(batch: SettlementBatch, line: BankLine, window_b_bd: int) -> bool:
    return business_gap(batch.processed_at.date(), line.value_date) <= window_b_bd


def _decision(batch: SettlementBatch, line: BankLine, tier: MatchTier) -> Decision:
    gap = business_gap(batch.processed_at.date(), line.value_date)
    diff = line.credit_paise - batch.amount_paise
    return Decision(
        plane=Plane.B, kind=MatchKind.BATCH_BANK, left_ids=(batch.settlement_id,),
        right_id=line.line_id, tier=tier, amount_diff_paise=diff, date_gap_bd=gap,
        confidence=confidence_for(tier.value, gap, abs(diff)),
        evidence={"settlement_id": batch.settlement_id, "bank_line_id": line.line_id,
                  "batch_amount_paise": batch.amount_paise,
                  "credit_paise": line.credit_paise, "utr": batch.settlement_utr,
                  "rule": tier.value},
    )


def _accept(state: PlaneBState, batch: SettlementBatch, line: BankLine,
            tier: MatchTier) -> None:
    state.decisions.append(_decision(batch, line, tier))
    state.matched_batch_ids.add(batch.settlement_id)
    state.matched_bank_ids.add(line.line_id)


def _detect_duplicates(batches: list[SettlementBatch], bank: list[BankLine],
                       window_b_bd: int, state: PlaneBState) -> None:
    # Exact bank-line clones are detected before duplicate UTRs (exception precedence).
    fingerprints: dict[tuple[object, ...], list[BankLine]] = {}
    for line in bank:
        fingerprint = (
            line.txn_date, line.value_date, line.narration, line.credit_paise,
            line.debit_paise, line.ref_no,
        )
        fingerprints.setdefault(fingerprint, []).append(line)
    for lines in fingerprints.values():
        if len(lines) < 2:
            continue
        related = [b for b in batches if b.settlement_utr and
                   normalize_identifier(b.settlement_utr) in normalize_identifier(lines[0].narration)
                   and b.amount_paise == lines[0].credit_paise and _in_window(b, lines[0], window_b_bd)]
        ids = sorted({line.line_id for line in lines} | {b.settlement_id for b in related})
        state.exceptions.append(ExceptionItem(tuple(ids), "DUPLICATE_BANK_LINE", 1.0, {
            "bank_line_ids": sorted(line.line_id for line in lines),
            "settlement_ids": sorted(b.settlement_id for b in related),
        }))
        state.blocked_bank_ids.update(line.line_id for line in lines)
        state.blocked_batch_ids.update(b.settlement_id for b in related)

    for batch in sorted(batches, key=lambda item: item.settlement_id):
        if not batch.settlement_utr or batch.settlement_id in state.blocked_batch_ids:
            continue
        needle = normalize_identifier(batch.settlement_utr)
        hits = [line for line in bank if line.line_id not in state.blocked_bank_ids
                and needle in normalize_identifier(line.narration)
                and _in_window(batch, line, window_b_bd)]
        if len(hits) > 1:
            ids = [batch.settlement_id] + sorted(line.line_id for line in hits)
            state.exceptions.append(ExceptionItem(tuple(ids), "DUPLICATE_UTR", 1.0, {
                "settlement_id": batch.settlement_id, "utr": batch.settlement_utr,
                "bank_line_ids": sorted(line.line_id for line in hits),
            }))
            state.blocked_batch_ids.add(batch.settlement_id)
            state.blocked_bank_ids.update(line.line_id for line in hits)


def match_b0_b1(batches: list[SettlementBatch], bank: list[BankLine],
                window_b_bd: int, tol_b_paise: int,
                initially_blocked_batches: set[str] | None = None,
                deferred_bank_ids: set[str] | None = None) -> PlaneBState:
    state = PlaneBState(blocked_batch_ids=set(initially_blocked_batches or ()))
    deferred = set(deferred_bank_ids or ())
    active_batches = [b for b in batches if not b.tainted and b.settlement_id not in state.blocked_batch_ids]
    _detect_duplicates(active_batches, bank, window_b_bd, state)

    # B0 exact known UTR and exact amount.
    edges_by_batch: dict[str, list[str]] = {}
    edges_by_bank: dict[str, list[str]] = {}
    batch_map = {b.settlement_id: b for b in active_batches}
    bank_map = {line.line_id: line for line in bank}
    for batch in active_batches:
        if not batch.settlement_utr or batch.settlement_id in state.blocked_batch_ids:
            continue
        needle = normalize_identifier(batch.settlement_utr)
        for line in bank:
            if line.line_id in state.blocked_bank_ids:
                continue
            if (needle in normalize_identifier(line.narration)
                    and line.credit_paise == batch.amount_paise
                    and _in_window(batch, line, window_b_bd)):
                edges_by_batch.setdefault(batch.settlement_id, []).append(line.line_id)
                edges_by_bank.setdefault(line.line_id, []).append(batch.settlement_id)
    for sid in sorted(edges_by_batch):
        ids = edges_by_batch[sid]
        if len(ids) == 1 and len(edges_by_bank[ids[0]]) == 1:
            _accept(state, batch_map[sid], bank_map[ids[0]], MatchTier.B0)

    # B1 mutual uniqueness on amount/date. Credits with any recoverable identifier
    # evidence are deliberately deferred to B2 so B1 cannot steal that tier.
    remaining_batches = [b for b in active_batches
                         if b.settlement_id not in state.matched_batch_ids | state.blocked_batch_ids]
    remaining_bank = [line for line in bank
                      if line.line_id not in state.matched_bank_ids | state.blocked_bank_ids | deferred]
    by_batch: dict[str, list[str]] = {}
    by_bank: dict[str, list[str]] = {}
    for batch in remaining_batches:
        for line in remaining_bank:
            if abs(line.credit_paise - batch.amount_paise) <= tol_b_paise and _in_window(
                    batch, line, window_b_bd):
                by_batch.setdefault(batch.settlement_id, []).append(line.line_id)
                by_bank.setdefault(line.line_id, []).append(batch.settlement_id)
    for sid in sorted(by_batch):
        candidates = by_batch[sid]
        if len(candidates) == 1 and len(by_bank[candidates[0]]) == 1:
            _accept(state, batch_map[sid], bank_map[candidates[0]], MatchTier.B1)

    ambiguous_ids: set[str] = set()
    ambiguous_edges: list[tuple[str, str]] = []
    for sid, line_ids in by_batch.items():
        if sid in state.matched_batch_ids:
            continue
        if len(line_ids) > 1 or any(len(by_bank[line_id]) > 1 for line_id in line_ids):
            ambiguous_ids.add(sid); ambiguous_ids.update(line_ids)
            ambiguous_edges.extend((sid, line_id) for line_id in line_ids)
    if ambiguous_ids:
        state.exceptions.append(ExceptionItem(tuple(sorted(ambiguous_ids)), "AMBIGUOUS_TIE", 1.0, {
            "plane": "B", "candidate_edges": sorted(ambiguous_edges),
        }))
        state.blocked_batch_ids.update(entity for entity in ambiguous_ids if entity in batch_map)
        state.blocked_bank_ids.update(entity for entity in ambiguous_ids if entity in bank_map)
    return state
