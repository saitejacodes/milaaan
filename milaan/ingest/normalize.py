"""Convert source strings to typed domain objects and quarantine unsafe rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from milaan.config import FeeConfig, fee_for
from milaan.ingest.quarantine import quarantined
from milaan.ingest.readers import read_source_files
from milaan.models import (
    BankLine, Channel, ExceptionItem, GatewayTxn, Order, OrderStatus,
    QuarantineRow, TxnType,
)


@dataclass
class IngestResult:
    orders: list[Order]
    txns: list[GatewayTxn]
    bank: list[BankLine]
    quarantined: list[QuarantineRow]
    tainted_settlement_ids: set[str]
    exceptions: list[ExceptionItem]


def _none(value: str | None) -> str | None:
    return value if value else None


def _int(raw: dict[str, str], name: str) -> int:
    return int(raw[name])


def _normalize_order(raw: dict[str, str]) -> Order:
    return Order(
        order_id=raw["order_id"], created_at=datetime.fromisoformat(raw["created_at"]),
        amount_paise=_int(raw, "amount_paise"), status=OrderStatus(raw["status"]),
        channel=Channel(raw["channel"]), payment_id=_none(raw.get("payment_id")),
        source_row_id=raw["source_row_id"],
    )


def _normalize_txn(raw: dict[str, str], fees: FeeConfig) -> GatewayTxn:
    raw_type = raw["type"].upper()
    txn_type = TxnType(raw_type)
    gross, fee, tax, net = (_int(raw, name) for name in (
        "gross_paise", "fee_paise", "tax_paise", "net_paise"
    ))
    txn_id = raw["entity_id"]
    if txn_type is TxnType.PAYMENT:
        channel = Channel(raw["method"])
        expected_fee, expected_tax = fee_for(gross, channel, fees)
        if min(gross, fee, tax, net) < 0 or (fee, tax) != (expected_fee, expected_tax):
            raise ArithmeticError("FEE_MODEL_VIOLATION")
        if net != gross - fee - tax:
            raise ArithmeticError("FEE_MODEL_VIOLATION")
        payment_id, original_payment_id = txn_id, None
    elif txn_type in {TxnType.REFUND, TxnType.CHARGEBACK}:
        channel = None
        if not (gross < 0 and fee == 0 and tax == 0 and net == gross and raw.get("payment_id")):
            raise ArithmeticError("FEE_MODEL_VIOLATION")
        payment_id, original_payment_id = None, raw["payment_id"]
    else:
        channel = None
        if not (fee == 0 and tax == 0 and net == gross):
            raise ArithmeticError("FEE_MODEL_VIOLATION")
        payment_id = original_payment_id = None
    return GatewayTxn(
        txn_id=txn_id, txn_type=txn_type, payment_id=payment_id,
        original_payment_id=original_payment_id, refund_id=_none(raw.get("refund_id")),
        order_ref=_none(raw.get("order_id")), channel=channel,
        gross_paise=gross, fee_paise=fee, tax_paise=tax, net_paise=net,
        captured_at=datetime.fromisoformat(raw["captured_at"]),
        settlement_id=_none(raw.get("settlement_id")),
        settlement_utr=_none(raw.get("settlement_utr")),
        settlement_processed_at=(datetime.fromisoformat(raw["settlement_processed_at"])
                                 if raw.get("settlement_processed_at") else None),
        source_row_id=raw["source_row_id"],
    )


def _normalize_bank(raw: dict[str, str]) -> BankLine:
    return BankLine(
        line_id=raw["line_id"], txn_date=date.fromisoformat(raw["txn_date"]),
        value_date=date.fromisoformat(raw["value_date"]), narration=raw["narration"],
        credit_paise=_int(raw, "credit_paise"), debit_paise=_int(raw, "debit_paise"),
        balance_paise=_int(raw, "balance_paise"), ref_no=raw["ref_no"],
        source_row_id=raw["source_row_id"],
    )


def normalize_inputs(data_dir: Path, fees: FeeConfig) -> IngestResult:
    order_rows, txn_rows, bank_rows = read_source_files(data_dir)
    orders: list[Order] = []
    txns: list[GatewayTxn] = []
    bank: list[BankLine] = []
    rejected: list[QuarantineRow] = []
    exceptions: list[ExceptionItem] = []
    tainted: set[str] = set()

    for raw in order_rows:
        try:
            orders.append(_normalize_order(raw))
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append(quarantined("orders", raw, f"INGEST_REJECT:{type(exc).__name__}"))
            exceptions.append(ExceptionItem(
                (raw.get("source_row_id", "orders:unknown"),), "INGEST_REJECT", 1.0,
                {"source": "orders", "error": type(exc).__name__},
            ))

    for raw in txn_rows:
        try:
            if raw.get("type", "").upper() not in {item.value for item in TxnType}:
                rejected.append(quarantined("gateway_recon", raw, "unsupported_type"))
                if raw.get("settlement_id"):
                    tainted.add(raw["settlement_id"])
                continue
            txns.append(_normalize_txn(raw, fees))
        except ArithmeticError:
            rejected.append(quarantined("gateway_recon", raw, "FEE_MODEL_VIOLATION"))
            exceptions.append(ExceptionItem(
                (raw.get("entity_id", raw.get("source_row_id", "recon:unknown")),),
                "FEE_MODEL_VIOLATION", 1.0,
                {"source_row_id": raw.get("source_row_id"), "raw": raw},
            ))
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append(quarantined("gateway_recon", raw, f"INGEST_REJECT:{type(exc).__name__}"))
            exceptions.append(ExceptionItem(
                (raw.get("source_row_id", "recon:unknown"),), "INGEST_REJECT", 1.0,
                {"source": "gateway_recon", "error": type(exc).__name__},
            ))

    for raw in bank_rows:
        try:
            bank.append(_normalize_bank(raw))
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append(quarantined("bank", raw, f"INGEST_REJECT:{type(exc).__name__}"))
            exceptions.append(ExceptionItem(
                (raw.get("source_row_id", "bank:unknown"),), "INGEST_REJECT", 1.0,
                {"source": "bank", "error": type(exc).__name__},
            ))

    return IngestResult(orders, txns, bank, rejected, tainted, exceptions)
