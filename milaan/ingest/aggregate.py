"""Aggregate signed recon members by settlement_id, never by date."""

from __future__ import annotations

from dataclasses import dataclass

from milaan.ingest.normalize import IngestResult
from milaan.models import ExceptionItem, GatewayTxn, SettlementBatch


@dataclass
class AggregateResult:
    batches: list[SettlementBatch]
    exceptions: list[ExceptionItem]
    blocked_settlement_ids: set[str]


def aggregate_batches(ingested: IngestResult) -> AggregateResult:
    grouped: dict[str, list[GatewayTxn]] = {}
    for txn in ingested.txns:
        if txn.settlement_id:
            grouped.setdefault(txn.settlement_id, []).append(txn)

    batches: list[SettlementBatch] = []
    exceptions: list[ExceptionItem] = []
    blocked: set[str] = set()
    all_ids = set(grouped) | set(ingested.tainted_settlement_ids)
    for settlement_id in sorted(all_ids):
        members = grouped.get(settlement_id, [])
        processed_values = {m.settlement_processed_at for m in members if m.settlement_processed_at}
        if settlement_id in ingested.tainted_settlement_ids:
            blocked.add(settlement_id)
            rejected_rows = [q for q in ingested.quarantined if q.settlement_id == settlement_id]
            claimed_rejected_net = 0
            for row in rejected_rows:
                try:
                    claimed_rejected_net += int(row.raw.get("net_paise", "0"))
                except (TypeError, ValueError):
                    pass
            exceptions.append(ExceptionItem(
                (settlement_id,), "TAINTED_SETTLEMENT", 1.0,
                {"settlement_id": settlement_id,
                 "known_member_txn_ids": sorted(m.txn_id for m in members),
                 "known_member_net_paise": sum(m.net_paise for m in members),
                 "quarantined_claimed_net_paise": claimed_rejected_net,
                 "claimed_exposure_paise": sum(m.net_paise for m in members) + claimed_rejected_net,
                 "quarantined_rows": [q.source_row_id for q in rejected_rows],
                 "quarantine_reasons": sorted({q.reason for q in rejected_rows})},
            ))
        non_null = {m.settlement_utr for m in members if m.settlement_utr}
        if len(non_null) > 1:
            blocked.add(settlement_id)
            exceptions.append(ExceptionItem(
                (settlement_id,), "UTR_CONFLICT_IN_BATCH", 1.0,
                {"settlement_id": settlement_id, "distinct_non_null_utrs": sorted(non_null),
                 "member_txn_ids": sorted(m.txn_id for m in members)},
            ))
        if len(processed_values) != 1:
            blocked.add(settlement_id)
            exceptions.append(ExceptionItem(
                (settlement_id,), "INGEST_REJECT", 1.0,
                {"settlement_id": settlement_id,
                 "error": "inconsistent_or_missing_settlement_processed_at"},
            ))
            continue
        batch_utr = next(iter(non_null)) if len(non_null) == 1 else None
        batches.append(SettlementBatch(
            settlement_id=settlement_id, settlement_utr=batch_utr,
            amount_paise=sum(m.net_paise for m in members),
            processed_at=next(iter(processed_values)),
            member_txn_ids=tuple(sorted(m.txn_id for m in members)),
            tainted=settlement_id in ingested.tainted_settlement_ids,
        ))
    return AggregateResult(batches, exceptions, blocked)
