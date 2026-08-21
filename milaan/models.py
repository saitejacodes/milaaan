"""Domain objects. Money is always signed integer paise."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class Channel(StrEnum):
    UPI = "UPI"
    CARD = "CARD"
    NETBANKING = "NETBANKING"


class OrderStatus(StrEnum):
    PAID = "PAID"
    REFUNDED = "REFUNDED"
    PARTIAL_REFUND = "PARTIAL_REFUND"
    FAILED = "FAILED"


class TxnType(StrEnum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    CHARGEBACK = "CHARGEBACK"
    ADJUSTMENT = "ADJUSTMENT"


class MatchKind(StrEnum):
    ORDER_TXN = "ORDER_TXN"
    BATCH_BANK = "BATCH_BANK"


class Plane(StrEnum):
    A = "A"
    B = "B"


class MatchTier(StrEnum):
    A0 = "A0"
    A0B = "A0B"
    A1 = "A1"
    B0 = "B0"
    B1 = "B1"
    B2 = "B2"


@dataclass(frozen=True)
class Order:
    order_id: str
    created_at: datetime
    amount_paise: int
    status: OrderStatus
    channel: Channel
    payment_id: str | None
    source_row_id: str


@dataclass(frozen=True)
class GatewayTxn:
    txn_id: str
    txn_type: TxnType
    payment_id: str | None
    original_payment_id: str | None
    refund_id: str | None
    order_ref: str | None
    channel: Channel | None
    gross_paise: int
    fee_paise: int
    tax_paise: int
    net_paise: int
    captured_at: datetime
    settlement_id: str | None
    settlement_utr: str | None
    settlement_processed_at: datetime | None
    source_row_id: str


@dataclass(frozen=True)
class SettlementBatch:
    settlement_id: str
    settlement_utr: str | None
    amount_paise: int
    processed_at: datetime
    member_txn_ids: tuple[str, ...]
    tainted: bool = False


@dataclass(frozen=True)
class BankLine:
    line_id: str
    txn_date: date
    value_date: date
    narration: str
    credit_paise: int
    debit_paise: int
    balance_paise: int
    ref_no: str
    source_row_id: str


@dataclass(frozen=True)
class Decision:
    plane: Plane
    kind: MatchKind
    left_ids: tuple[str, ...]
    right_id: str
    tier: MatchTier
    amount_diff_paise: int
    date_gap_bd: int
    confidence: float
    evidence: dict[str, Any]


@dataclass(frozen=True)
class ExceptionItem:
    scope_ids: tuple[str, ...]
    reason: str
    confidence: float
    evidence: dict[str, Any]
    narrative: str = ""
    guidance: str = ""
    suggested_action: str = ""


@dataclass(frozen=True)
class QuarantineRow:
    source: str
    source_row_id: str
    raw: dict[str, str]
    reason: str
    settlement_id: str | None = None
