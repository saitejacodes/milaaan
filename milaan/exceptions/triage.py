"""Deterministic residual triage with explicit precedence."""

from __future__ import annotations

from itertools import combinations

from milaan.engine.plane_b import PlaneBState, normalize_identifier
from milaan.generator.calendar import forward_business_gap
from milaan.models import BankLine, ExceptionItem, SettlementBatch


def triage_plane_b(batches: list[SettlementBatch], bank: list[BankLine], state: PlaneBState,
                   window_b_bd: int, prior_exceptions: list[ExceptionItem]) -> list[ExceptionItem]:
    exceptions: list[ExceptionItem] = []
    prior_ids = {entity_id for item in prior_exceptions + state.exceptions for entity_id in item.scope_ids}
    remaining_batches = [b for b in batches if b.settlement_id not in (
        state.matched_batch_ids | state.blocked_batch_ids | prior_ids
    ) and not b.tainted]
    remaining_bank = [line for line in bank if line.line_id not in (
        state.matched_bank_ids | state.blocked_bank_ids | prior_ids
    )]

    # Hard profile residue: identify, but never solve, a two-batch combined credit.
    consumed_batches: set[str] = set()
    consumed_bank: set[str] = set()
    for line in remaining_bank:
        candidates = [b for b in remaining_batches
                      if (gap := forward_business_gap(b.processed_at.date(), line.value_date))
                      is not None and gap <= window_b_bd]
        pairs = [(a, b) for a, b in combinations(candidates, 2)
                 if a.amount_paise + b.amount_paise == line.credit_paise]
        if len(pairs) == 1:
            first, second = pairs[0]
            ids = (first.settlement_id, second.settlement_id, line.line_id)
            exceptions.append(ExceptionItem(ids, "AMBIGUOUS_COMBINED", 1.0, {
                "batch_amounts_paise": [first.amount_paise, second.amount_paise],
                "credit_paise": line.credit_paise,
                "note": "solver is intentionally not approved",
            }))
            consumed_batches.update((first.settlement_id, second.settlement_id))
            consumed_bank.add(line.line_id)

    remaining_batches = [b for b in remaining_batches if b.settlement_id not in consumed_batches]
    remaining_bank = [line for line in remaining_bank if line.line_id not in consumed_bank]

    # Specific UTR-based failures before generic missing/unknown.
    consumed_batches.clear(); consumed_bank.clear()
    for batch in remaining_batches:
        if not batch.settlement_utr:
            continue
        needle = normalize_identifier(batch.settlement_utr)
        utr_lines = [line for line in remaining_bank if needle in normalize_identifier(line.narration)]
        in_window = [line for line in utr_lines
                     if (gap := forward_business_gap(batch.processed_at.date(), line.value_date))
                     is not None and gap <= window_b_bd]
        if len(in_window) == 1 and in_window[0].credit_paise != batch.amount_paise:
            line = in_window[0]
            exceptions.append(ExceptionItem(
                (batch.settlement_id, line.line_id), "AMOUNT_MISMATCH_BEYOND_TOL", 1.0,
                {"batch_amount_paise": batch.amount_paise,
                 "credit_paise": line.credit_paise, "utr": batch.settlement_utr},
            ))
            consumed_batches.add(batch.settlement_id); consumed_bank.add(line.line_id)
        elif len(utr_lines) == 1 and not in_window:
            line = utr_lines[0]
            exceptions.append(ExceptionItem(
                (batch.settlement_id, line.line_id), "DATE_OUT_OF_WINDOW", 1.0,
                {"processed_date": batch.processed_at.date().isoformat(),
                 "value_date": line.value_date.isoformat(), "utr": batch.settlement_utr},
            ))
            consumed_batches.add(batch.settlement_id); consumed_bank.add(line.line_id)

    for batch in remaining_batches:
        if batch.settlement_id in consumed_batches:
            continue
        exceptions.append(ExceptionItem(
            (batch.settlement_id,), "MISSING_IN_BANK", 1.0,
            {"settlement_id": batch.settlement_id, "utr": batch.settlement_utr,
             "amount_paise": batch.amount_paise,
             "processed_date": batch.processed_at.date().isoformat()},
        ))
    for line in remaining_bank:
        if line.line_id in consumed_bank:
            continue
        exceptions.append(ExceptionItem(
            (line.line_id,), "UNKNOWN_BANK_CREDIT", 1.0,
            {"bank_line_id": line.line_id, "credit_paise": line.credit_paise,
             "value_date": line.value_date.isoformat(), "narration": line.narration},
        ))
    return exceptions
