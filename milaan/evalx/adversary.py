"""Hostile-input attack battery for the reconciliation engine.

One registry drives both ``pytest tests/test_adversarial_finance.py`` and
``make adversarial``, so the printed scorecard and the test suite can never
disagree about what was actually proven.

Every finance attack corrupts a freshly generated dataset, reconciles it, and
then asserts two things: the specific expected control fired, and the universal
safety invariant still holds -- no accepted match may join records whose money
or chronology disagrees, and no tainted settlement may be matched at all.

The universal invariant is the important half. A test that only checks "the
right reason code appeared" would pass even if the engine had also silently
posted an unsafe match somewhere else in the same run.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

from milaan.config import load_timing
from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.calendar import forward_business_gap
from milaan.generator.emit import generate_to_directory


BASE_RECORDS = 120
BASE_SEED = 8801


class AttackFailed(AssertionError):
    """A control did not behave safely under attack."""


@dataclass(frozen=True)
class Attack:
    label: str
    category: str
    run: Callable[["Bench"], None]


@dataclass
class AttackOutcome:
    label: str
    category: str
    passed: bool
    detail: str = ""


REGISTRY: list[Attack] = []


def attack(label: str, category: str = "finance_safety") -> Callable[[Callable], Callable]:
    def decorate(function: Callable[["Bench"], None]) -> Callable[["Bench"], None]:
        REGISTRY.append(Attack(label, category, function))
        return function
    return decorate


# ---------------------------------------------------------------- workbench


@dataclass
class Probe:
    """Read-only view of what the engine decided about a corrupted run."""

    database: Path
    rows: dict[str, list[sqlite3.Row]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        try:
            for name, query in (
                ("orders", "SELECT * FROM raw_orders"),
                ("txns", "SELECT * FROM raw_txns"),
                ("bank", "SELECT * FROM raw_bank"),
                ("batches", "SELECT * FROM settlement_batches"),
                ("matches", "SELECT * FROM matches"),
                ("members", "SELECT * FROM match_members"),
                ("exceptions", "SELECT * FROM exceptions"),
                ("quarantine", "SELECT * FROM quarantine_rows"),
            ):
                self.rows[name] = conn.execute(query).fetchall()
        finally:
            conn.close()

    # -- lookups ---------------------------------------------------------

    def codes(self) -> set[str]:
        return {row["reason_code"] for row in self.rows["exceptions"]}

    def scopes_for(self, code: str) -> list[set[str]]:
        return [set(json.loads(row["scope_ids"])) for row in self.rows["exceptions"]
                if row["reason_code"] == code]

    def codes_for_entity(self, entity_id: str) -> set[str]:
        return {row["reason_code"] for row in self.rows["exceptions"]
                if entity_id in json.loads(row["scope_ids"])}

    def matched(self, entity_type: str) -> set[str]:
        return {row["entity_id"] for row in self.rows["members"]
                if row["entity_type"] == entity_type}

    def batch(self, settlement_id: str) -> sqlite3.Row | None:
        return next((row for row in self.rows["batches"]
                     if row["settlement_id"] == settlement_id), None)

    def quarantine_reasons(self) -> set[str]:
        return {row["reason"].split(":")[0] for row in self.rows["quarantine"]}

    def pairs(self, plane: str) -> list[tuple[str, str]]:
        by_match: dict[int, dict[str, str]] = {}
        for row in self.rows["members"]:
            if row["plane"] == plane:
                by_match.setdefault(int(row["match_id"]), {})[row["entity_type"]] = row["entity_id"]
        if plane == "A":
            return [(m["TXN"], m["ORDER"]) for m in by_match.values()]
        return [(m["BATCH"], m["BANK_LINE"]) for m in by_match.values()]

    # -- the universal invariant -----------------------------------------

    def assert_no_unsafe_match(self) -> None:
        """No accepted match may join records that do not actually agree."""
        timing = load_timing()
        orders = {row["order_id"]: row for row in self.rows["orders"]}
        txns = {row["txn_id"]: row for row in self.rows["txns"]}
        bank = {row["line_id"]: row for row in self.rows["bank"]}
        batches = {row["settlement_id"]: row for row in self.rows["batches"]}

        for payment_id, order_id in self.pairs("A"):
            payment, order = txns.get(payment_id), orders.get(order_id)
            if payment is None or order is None:
                raise AttackFailed(f"Plane-A match {payment_id}->{order_id} cites a missing record")
            if int(payment["gross_paise"]) != int(order["amount_paise"]):
                raise AttackFailed(
                    f"unsafe Plane-A match {payment_id}->{order_id}: "
                    f"{payment['gross_paise']} != {order['amount_paise']}"
                )
            gap = forward_business_gap(
                datetime.fromisoformat(order["created_at"]).date(),
                datetime.fromisoformat(payment["captured_at"]).date(),
            )
            if gap is None or gap > timing.window_a_bd:
                raise AttackFailed(
                    f"unsafe Plane-A match {payment_id}->{order_id}: forward gap {gap}"
                )

        for settlement_id, line_id in self.pairs("B"):
            batch, line = batches.get(settlement_id), bank.get(line_id)
            if batch is None or line is None:
                raise AttackFailed(f"Plane-B match {settlement_id}->{line_id} cites a missing record")
            if int(line["credit_paise"]) != int(batch["amount_paise"]):
                raise AttackFailed(
                    f"unsafe Plane-B match {settlement_id}->{line_id}: "
                    f"credit {line['credit_paise']} != batch {batch['amount_paise']}"
                )
            if int(batch["tainted"]):
                raise AttackFailed(f"tainted settlement {settlement_id} was matched")
            members = [row for row in self.rows["txns"] if row["settlement_id"] == settlement_id]
            member_total = sum(int(row["net_paise"]) for row in members)
            if member_total != int(batch["amount_paise"]):
                raise AttackFailed(
                    f"batch {settlement_id} amount {batch['amount_paise']} != "
                    f"signed member sum {member_total}"
                )
            gap = forward_business_gap(
                datetime.fromisoformat(batch["processed_at"]).date(),
                datetime.fromisoformat(line["value_date"]).date(),
            )
            if gap is None or gap > timing.window_b_bd:
                raise AttackFailed(
                    f"unsafe Plane-B match {settlement_id}->{line_id}: forward gap {gap}"
                )

        for entity_type in ("ORDER", "TXN", "BATCH", "BANK_LINE"):
            used = [row["entity_id"] for row in self.rows["members"]
                    if row["entity_type"] == entity_type]
            if len(used) != len(set(used)):
                raise AttackFailed(f"{entity_type} was consumed by more than one match")


class Bench:
    """A disposable corrupted-run workbench."""

    def __init__(self, directory: Path, records: int = BASE_RECORDS,
                 seed: int = BASE_SEED, profile: str = "clean") -> None:
        self.dir = directory
        self.records, self.seed, self.profile = records, seed, profile
        generate_to_directory(records, seed, profile, directory)
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))

    # -- csv access -------------------------------------------------------

    def read(self, name: str) -> tuple[list[str], list[dict[str, str]]]:
        with (self.dir / name).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return list(reader.fieldnames or ()), list(reader)

    def write(self, name: str, fields: list[str], rows: list[dict[str, str]]) -> None:
        with (self.dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def edit(self, name: str, mutate: Callable[[list[dict[str, str]]], None]) -> None:
        fields, rows = self.read(name)
        mutate(rows)
        self.write(name, fields, rows)

    # -- convenience selectors --------------------------------------------

    def expected_b(self) -> dict[str, str]:
        return {a: b for a, b in self.manifest["expectations"]["plane_b_matches"]}

    def a_payment(self) -> tuple[str, str]:
        return tuple(self.manifest["expectations"]["plane_a_matches"][0])  # type: ignore[return-value]

    def settlement_with(self, txn_type: str) -> tuple[str, dict[str, str]]:
        _fields, rows = self.read("gateway_recon.csv")
        for settlement_id in self.expected_b():
            member = next((row for row in rows if row["settlement_id"] == settlement_id
                           and row["type"] == txn_type), None)
            if member is not None:
                return settlement_id, member
        raise AttackFailed(f"no expected settlement contains a {txn_type} member")

    # -- execution ---------------------------------------------------------

    def reconcile(self) -> Probe:
        run_pipeline(self.dir, self.dir / "m.db", "mock")
        return Probe(self.dir / "m.db")

    def evaluate(self, gate: str | None = None) -> str:
        return evaluate_run(self.dir, self.dir / "m.db", self.dir, gate or self.profile)

    def expect_evaluation_failure(self) -> str:
        try:
            self.evaluate()
        except RuntimeError as exc:
            if (self.dir / "functional_metrics.json").exists():
                raise AttackFailed("a rejected run still published functional_metrics.json")
            return str(exc)
        raise AttackFailed("evaluation accepted a tampered run")


def require(condition: object, message: str) -> None:
    if not condition:
        raise AttackFailed(message)


# ------------------------------------------------------- duplicate identity


@attack("Duplicate order id")
def duplicate_order_id(bench: Bench) -> None:
    fields, rows = bench.read("orders.csv")
    clone = dict(rows[0])
    clone["source_row_id"] = "orders:cloned"
    bench.write("orders.csv", fields, rows + [clone])
    probe = bench.reconcile()
    require("DUPLICATE_SOURCE_ID" in probe.codes(), "duplicate order id was not reported")
    require("DUPLICATE_SOURCE_ID" in probe.quarantine_reasons(), "duplicate order was not quarantined")
    probe.assert_no_unsafe_match()


@attack("Duplicate gateway txn id")
def duplicate_txn_id(bench: Bench) -> None:
    fields, rows = bench.read("gateway_recon.csv")
    clone = dict(next(row for row in rows if row["settlement_id"]))
    clone["source_row_id"] = "recon:cloned"
    bench.write("gateway_recon.csv", fields, rows + [clone])
    probe = bench.reconcile()
    require("DUPLICATE_SOURCE_ID" in probe.codes(), "duplicate txn id was not reported")
    require("TAINTED_SETTLEMENT" in probe.codes(), "the duplicate did not taint its settlement")
    probe.assert_no_unsafe_match()


@attack("Duplicate payment id")
def duplicate_payment_id(bench: Bench) -> None:
    fields, rows = bench.read("gateway_recon.csv")
    payment = next(row for row in rows if row["type"] == "payment")
    clone = dict(payment)
    clone["source_row_id"] = "recon:cloned-payment"
    bench.write("gateway_recon.csv", fields, rows + [clone])
    probe = bench.reconcile()
    require("DUPLICATE_SOURCE_ID" in probe.codes(), "duplicate payment id was not reported")
    matched = [pair for pair in probe.pairs("A") if pair[0] == payment["entity_id"]]
    require(len(matched) <= 1, "a duplicated payment was matched more than once")
    probe.assert_no_unsafe_match()


@attack("One order claimed by two payments")
def order_with_two_payments(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    fields, rows = bench.read("gateway_recon.csv")
    original = next(row for row in rows if row["entity_id"] == payment_id)
    rival = dict(original)
    rival.update({"entity_id": "pay_rival000000001", "source_row_id": "recon:rival",
                  "settlement_id": "", "settlement_utr": "", "settlement_processed_at": ""})
    bench.write("gateway_recon.csv", fields, rows + [rival])
    probe = bench.reconcile()
    require("IDENTITY_CONFLICT" in probe.codes_for_entity(order_id),
            "two payments claiming one order did not raise IDENTITY_CONFLICT")
    require(order_id not in probe.matched("ORDER"), "a contested order was still matched")
    probe.assert_no_unsafe_match()


@attack("One payment claimed by two orders")
def payment_with_two_orders(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    fields, rows = bench.read("orders.csv")
    original = next(row for row in rows if row["order_id"] == order_id)
    rival = dict(original)
    rival.update({"order_id": "order_rival", "source_row_id": "orders:rival",
                  "payment_id": payment_id})
    bench.write("orders.csv", fields, rows + [rival])
    probe = bench.reconcile()
    require("IDENTITY_CONFLICT" in probe.codes_for_entity(payment_id),
            "two orders claiming one payment did not raise IDENTITY_CONFLICT")
    require(payment_id not in probe.matched("TXN"), "a contested payment was still matched")
    probe.assert_no_unsafe_match()


@attack("Conflicting merchant payment reference")
def conflicting_payment_reference(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    _fields, txns = bench.read("gateway_recon.csv")
    other = next(row["entity_id"] for row in txns
                 if row["type"] == "payment" and row["entity_id"] != payment_id)
    bench.edit("orders.csv", lambda rows: [
        row.update({"payment_id": other}) for row in rows if row["order_id"] == order_id
    ])
    probe = bench.reconcile()
    require("IDENTITY_CONFLICT" in probe.codes(),
            "a contradictory merchant payment reference was accepted")
    probe.assert_no_unsafe_match()


# ------------------------------------------------------------- chronology


@attack("Payment captured before its order")
def payment_before_order(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    _fields, orders = bench.read("orders.csv")
    created = datetime.fromisoformat(
        next(row["created_at"] for row in orders if row["order_id"] == order_id)
    )
    earlier = (created - timedelta(days=9)).isoformat()
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"captured_at": earlier}) for row in rows if row["entity_id"] == payment_id
    ])
    probe = bench.reconcile()
    require("DATE_OUT_OF_WINDOW" in probe.codes_for_entity(order_id),
            "a payment captured before its order was not rejected")
    probe.assert_no_unsafe_match()


@attack("Payment beyond the forward window")
def payment_outside_window(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    _fields, orders = bench.read("orders.csv")
    created = datetime.fromisoformat(
        next(row["created_at"] for row in orders if row["order_id"] == order_id)
    )
    late = (created + timedelta(days=45)).isoformat()
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"captured_at": late}) for row in rows if row["entity_id"] == payment_id
    ])
    probe = bench.reconcile()
    require("DATE_OUT_OF_WINDOW" in probe.codes_for_entity(order_id),
            "a payment outside the forward window was accepted")
    probe.assert_no_unsafe_match()


@attack("Bank credit dated before settlement processing")
def bank_before_settlement(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))
    _fields, txns = bench.read("gateway_recon.csv")
    processed = datetime.fromisoformat(next(
        row["settlement_processed_at"] for row in txns if row["settlement_id"] == settlement_id
    ))
    earlier = (processed - timedelta(days=6)).date().isoformat()
    bench.edit("bank.csv", lambda rows: [
        row.update({"value_date": earlier, "txn_date": earlier})
        for row in rows if row["line_id"] == line_id
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a bank credit dated before its settlement was accepted")
    probe.assert_no_unsafe_match()


@attack("Bank credit beyond the settlement window")
def bank_beyond_window(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))
    _fields, txns = bench.read("gateway_recon.csv")
    processed = datetime.fromisoformat(next(
        row["settlement_processed_at"] for row in txns if row["settlement_id"] == settlement_id
    ))
    later = (processed + timedelta(days=40)).date().isoformat()
    bench.edit("bank.csv", lambda rows: [
        row.update({"value_date": later, "txn_date": later})
        for row in rows if row["line_id"] == line_id
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a bank credit far outside the window was accepted")
    require(probe.codes_for_entity(settlement_id), "no exception explained the unmatched batch")
    probe.assert_no_unsafe_match()


# ------------------------------------------------------ malformed source rows


def _corrupt_payment_field(bench: Bench, field_name: str, value: str) -> Probe:
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({field_name: value}) for row in rows
        if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    probe.assert_no_unsafe_match()
    require(settlement_id not in probe.matched("BATCH"),
            f"a settlement containing a rejected member ({field_name}={value!r}) was matched")
    return probe


@attack("Unsupported transaction type")
def unsupported_txn_type(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "type", "crypto_airdrop")
    require("UNSUPPORTED_TXN_TYPE" in probe.codes(), "an unsupported type was not reported")
    require("TAINTED_SETTLEMENT" in probe.codes(), "an unsupported member did not taint its batch")


@attack("Non-numeric gross amount")
def alphabetic_amount(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "gross_paise", "ONE THOUSAND")
    require("INGEST_REJECT" in probe.codes(), "an alphabetic amount was not rejected")


@attack("Empty gross amount")
def empty_amount(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "gross_paise", "")
    require("INGEST_REJECT" in probe.codes(), "an empty amount was not rejected")


@attack("Decimal amount where paise are required")
def decimal_amount(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "gross_paise", "1234.56")
    require("INGEST_REJECT" in probe.codes(), "a decimal amount string was not rejected")


@attack("Negative payment amount")
def negative_payment(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "gross_paise", "-500000")
    require("FEE_MODEL_VIOLATION" in probe.codes(), "a negative payment gross was accepted")


@attack("Absurdly large integer amount")
def enormous_amount(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "gross_paise", "9" * 40)
    require(probe.codes(), "an absurd amount produced no control at all")


@attack("Invalid fee")
def invalid_fee(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "fee_paise", "1")
    require("FEE_MODEL_VIOLATION" in probe.codes(), "an invalid fee was accepted")


@attack("Invalid tax")
def invalid_tax(bench: Bench) -> None:
    """Shift GST while keeping net = gross - fee - tax, to isolate the GST rule."""
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"tax_paise": str(int(row["tax_paise"]) + 7),
                    "net_paise": str(int(row["net_paise"]) - 7)})
        for row in rows if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require("FEE_MODEL_VIOLATION" in probe.codes(), "an invalid GST amount was accepted")
    require(settlement_id not in probe.matched("BATCH"), "the affected settlement was matched")
    probe.assert_no_unsafe_match()


@attack("net != gross - fee - tax")
def broken_net_arithmetic(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"net_paise": str(int(row["net_paise"]) + 1)}) for row in rows
        if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require("FEE_MODEL_VIOLATION" in probe.codes(), "broken net arithmetic was accepted")
    require(settlement_id not in probe.matched("BATCH"), "the affected settlement was still matched")
    probe.assert_no_unsafe_match()


@attack("Invalid refund arithmetic")
def invalid_refund(bench: Bench) -> None:
    bench_profile_rows = bench.read("gateway_recon.csv")
    fields, rows = bench_profile_rows
    refund = next((row for row in rows if row["type"] == "refund"), None)
    if refund is None:
        payment = next(row for row in rows if row["type"] == "payment")
        refund = dict(payment)
        refund.update({"entity_id": "rfnd_broken00001", "type": "refund",
                       "source_row_id": "recon:broken-refund", "payment_id": payment["entity_id"],
                       "refund_id": "rfnd_broken00001", "fee_paise": "0", "tax_paise": "0"})
        rows.append(refund)
    refund["gross_paise"] = "5000"   # a refund must be negative
    refund["net_paise"] = "5000"
    bench.write("gateway_recon.csv", fields, rows)
    probe = bench.reconcile()
    require("FEE_MODEL_VIOLATION" in probe.codes(), "a positive refund was accepted")
    probe.assert_no_unsafe_match()


@attack("Malformed bank date")
def malformed_bank_date(bench: Bench) -> None:
    _settlement_id, line_id = next(iter(bench.expected_b().items()))
    bench.edit("bank.csv", lambda rows: [
        row.update({"value_date": "31/08/2026"}) for row in rows if row["line_id"] == line_id
    ])
    probe = bench.reconcile()
    require("INGEST_REJECT" in probe.codes(), "a malformed bank date was accepted")
    require(line_id not in probe.matched("BANK_LINE"), "a malformed bank line was matched")
    probe.assert_no_unsafe_match()


@attack("Malformed settlement processing date")
def malformed_settlement_date(bench: Bench) -> None:
    probe = _corrupt_payment_field(bench, "settlement_processed_at", "not-a-date")
    require("INGEST_REJECT" in probe.codes(), "a malformed settlement date was accepted")


# ------------------------------------------------------------- settlements


@attack("Rejected member still carrying a settlement id")
def rejected_member_taints_batch(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"fee_paise": str(int(row["fee_paise"]) + 3)}) for row in rows
        if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require("TAINTED_SETTLEMENT" in probe.codes_for_entity(settlement_id),
            "a rejected member did not taint its settlement")
    batch = probe.batch(settlement_id)
    require(batch is not None and int(batch["tainted"]) == 1, "the batch was not marked tainted")
    probe.assert_no_unsafe_match()


@attack("Tainted batch whose survivors exactly equal the credit")
def tainted_batch_tempting_partial(bench: Bench) -> None:
    """The flagship: make abstaining cost a perfect-looking match."""
    settlement_id, member = bench.settlement_with("payment")
    dropped_net = int(member["net_paise"])
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"fee_paise": str(int(row["fee_paise"]) + 1)}) for row in rows
        if row["entity_id"] == member["entity_id"]
    ])
    line_id = bench.expected_b()[settlement_id]
    bench.edit("bank.csv", lambda rows: [
        row.update({"credit_paise": str(int(row["credit_paise"]) - dropped_net)})
        for row in rows if row["line_id"] == line_id
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "the engine matched a tainted settlement to its tempting partial credit")
    require("TAINTED_SETTLEMENT" in probe.codes_for_entity(settlement_id),
            "the tainted settlement was not reported")
    probe.assert_no_unsafe_match()


@attack("Settlement member deleted")
def missing_settlement_member(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    fields, rows = bench.read("gateway_recon.csv")
    bench.write("gateway_recon.csv", fields,
                [row for row in rows if row["entity_id"] != member["entity_id"]])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a settlement missing a member was matched to its original credit")
    probe.assert_no_unsafe_match()


@attack("Extra settlement member injected")
def extra_settlement_member(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    fields, rows = bench.read("gateway_recon.csv")
    intruder = dict(member)
    intruder.update({
        "entity_id": "adj_intruder00001", "type": "adjustment", "source_row_id": "recon:intruder",
        "payment_id": "", "refund_id": "", "order_id": "", "method": "",
        "gross_paise": "77777", "fee_paise": "0", "tax_paise": "0", "net_paise": "77777",
        "settlement_utr": "",
    })
    bench.write("gateway_recon.csv", fields, rows + [intruder])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a settlement with an injected member still matched its original credit")
    probe.assert_no_unsafe_match()


@attack("Settlement amount silently altered")
def incorrect_settlement_amount(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("adjustment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"gross_paise": str(int(row["gross_paise"]) - 25_000),
                    "net_paise": str(int(row["net_paise"]) - 25_000)})
        for row in rows if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a settlement whose total changed still matched the old credit")
    probe.assert_no_unsafe_match()


@attack("Bank credit amount silently altered")
def incorrect_bank_credit(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))
    bench.edit("bank.csv", lambda rows: [
        row.update({"credit_paise": str(int(row["credit_paise"]) - 1)})
        for row in rows if row["line_id"] == line_id
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a one-paise credit discrepancy was tolerated")
    require("AMOUNT_MISMATCH_BEYOND_TOL" in probe.codes_for_entity(settlement_id),
            "the amount discrepancy was not reported as such")
    probe.assert_no_unsafe_match()


@attack("Settlement id removed from a member")
def missing_settlement_id(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"settlement_id": "", "settlement_utr": "", "settlement_processed_at": ""})
        for row in rows if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a batch that lost a member still matched its original credit")
    probe.assert_no_unsafe_match()


@attack("Missing settlement processing timestamp")
def missing_processed_at(bench: Bench) -> None:
    settlement_id = next(iter(bench.expected_b()))
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"settlement_processed_at": ""}) for row in rows
        if row["settlement_id"] == settlement_id
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a settlement with no processing date was matched")
    require("INGEST_REJECT" in probe.codes_for_entity(settlement_id),
            "the undated settlement was not reported")
    probe.assert_no_unsafe_match()


@attack("Inconsistent settlement processing timestamps")
def inconsistent_processed_at(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"settlement_processed_at": "2026-09-30T17:00:00"})
        for row in rows if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require(settlement_id not in probe.matched("BATCH"),
            "a settlement with contradictory processing dates was matched")
    require("INGEST_REJECT" in probe.codes_for_entity(settlement_id),
            "the contradictory dates were not reported")
    probe.assert_no_unsafe_match()


@attack("Conflicting UTRs inside one batch")
def utr_conflict(bench: Bench) -> None:
    settlement_id, member = bench.settlement_with("payment")
    bench.edit("gateway_recon.csv", lambda rows: [
        row.update({"settlement_utr": "UTRCONFLICT99"}) for row in rows
        if row["entity_id"] == member["entity_id"]
    ])
    probe = bench.reconcile()
    require("UTR_CONFLICT_IN_BATCH" in probe.codes_for_entity(settlement_id),
            "conflicting member UTRs inside a batch were accepted")
    require(settlement_id not in probe.matched("BATCH"), "the conflicted batch was matched")
    probe.assert_no_unsafe_match()


# ------------------------------------------------------------ bank anomalies


@attack("Exact duplicate bank line")
def duplicate_bank_line(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))
    fields, rows = bench.read("bank.csv")
    clone = dict(next(row for row in rows if row["line_id"] == line_id))
    clone.update({"line_id": "bank_clone_probe", "source_row_id": "bank:clone-probe"})
    bench.write("bank.csv", fields, rows + [clone])
    probe = bench.reconcile()
    require("DUPLICATE_BANK_LINE" in probe.codes(), "an exact duplicate bank line was accepted")
    require(settlement_id not in probe.matched("BATCH"),
            "a settlement matched despite two identical candidate credits")
    probe.assert_no_unsafe_match()


@attack("One UTR on two different credits")
def duplicate_utr(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))
    fields, rows = bench.read("bank.csv")
    clone = dict(next(row for row in rows if row["line_id"] == line_id))
    clone.update({"line_id": "bank_dupe_utr_probe", "source_row_id": "bank:dupe-utr-probe",
                  "credit_paise": str(int(clone["credit_paise"]) + 313)})
    bench.write("bank.csv", fields, rows + [clone])
    probe = bench.reconcile()
    require({"DUPLICATE_UTR", "DUPLICATE_BANK_LINE"} & probe.codes(),
            "one UTR appearing on two credits was accepted")
    require(settlement_id not in probe.matched("BATCH"), "the ambiguous settlement was matched")
    probe.assert_no_unsafe_match()


@attack("Equal amount and date ambiguity")
def amount_date_ambiguity(bench: Bench) -> None:
    expected = bench.expected_b()
    (first_sid, first_line), (second_sid, second_line) = list(expected.items())[:2]
    _fields, bank_rows = bench.read("bank.csv")
    first = next(row for row in bank_rows if row["line_id"] == first_line)
    target_amount, target_date = first["credit_paise"], first["value_date"]

    # Strip both identifiers so no tier above B1 can resolve the pair, and make
    # the two credits indistinguishable on amount and date.
    def blank(rows: list[dict[str, str]]) -> None:
        for row in rows:
            if row["line_id"] == first_line:
                row.update({"narration": "NEFT-NOREF-A", "ref_no": "NOREF-A"})
            if row["line_id"] == second_line:
                row.update({"narration": "NEFT-NOREF-B", "ref_no": "NOREF-B",
                            "credit_paise": target_amount, "value_date": target_date,
                            "txn_date": target_date})
    bench.edit("bank.csv", blank)

    def rebalance(rows: list[dict[str, str]]) -> None:
        for row in rows:
            if row["settlement_id"] == second_sid:
                row["settlement_utr"] = ""
            if row["settlement_id"] == first_sid:
                row["settlement_utr"] = ""
    bench.edit("gateway_recon.csv", rebalance)
    probe = bench.reconcile()
    require(first_sid not in probe.matched("BATCH") or second_sid not in probe.matched("BATCH"),
            "both sides of an indistinguishable pair were matched")
    probe.assert_no_unsafe_match()


@attack("Unexplained bank credit")
def unknown_bank_credit(bench: Bench) -> None:
    fields, rows = bench.read("bank.csv")
    stranger = dict(rows[-1])
    stranger.update({"line_id": "bank_stranger_probe", "source_row_id": "bank:stranger",
                     "narration": "NEFT-UNRELATED-PARTY", "ref_no": "STRANGER1",
                     "credit_paise": "424242"})
    bench.write("bank.csv", fields, rows + [stranger])
    probe = bench.reconcile()
    require("UNKNOWN_BANK_CREDIT" in probe.codes_for_entity("bank_stranger_probe"),
            "an unexplained credit was not reported")
    require("bank_stranger_probe" not in probe.matched("BANK_LINE"),
            "an unexplained credit was matched to something")
    probe.assert_no_unsafe_match()


@attack("Missing bank credit")
def missing_bank_credit(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))
    fields, rows = bench.read("bank.csv")
    bench.write("bank.csv", fields, [row for row in rows if row["line_id"] != line_id])
    probe = bench.reconcile()
    require("MISSING_IN_BANK" in probe.codes_for_entity(settlement_id),
            "a settlement with no bank credit was not reported as missing")
    probe.assert_no_unsafe_match()


# ------------------------------------------------------------- source plane


@attack("Residual unmatched gateway payment")
def residual_gateway_payment(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    fields, rows = bench.read("orders.csv")
    bench.write("orders.csv", fields, [row for row in rows if row["order_id"] != order_id])
    probe = bench.reconcile()
    require("UNMATCHED_GATEWAY_PAYMENT" in probe.codes_for_entity(payment_id),
            "a payment with no order was not reported")
    require(payment_id not in probe.matched("TXN"), "an orphaned payment was matched anyway")
    probe.assert_no_unsafe_match()


@attack("Paid order missing from the gateway")
def paid_order_missing(bench: Bench) -> None:
    payment_id, order_id = bench.a_payment()
    fields, rows = bench.read("gateway_recon.csv")
    bench.write("gateway_recon.csv", fields,
                [row for row in rows if row["entity_id"] != payment_id])
    probe = bench.reconcile()
    require("PAID_ORDER_MISSING_FROM_GATEWAY" in probe.codes_for_entity(order_id),
            "a paid order absent from the gateway was not reported")
    probe.assert_no_unsafe_match()


@attack("Orphan refund")
def orphan_refund(bench: Bench) -> None:
    fields, rows = bench.read("gateway_recon.csv")
    payment = next(row for row in rows if row["type"] == "payment")
    orphan = dict(payment)
    orphan.update({"entity_id": "rfnd_orphan00001", "type": "refund",
                   "source_row_id": "recon:orphan-refund", "payment_id": "pay_doesnotexist",
                   "refund_id": "rfnd_orphan00001", "order_id": "",
                   "gross_paise": "-1500", "fee_paise": "0", "tax_paise": "0",
                   "net_paise": "-1500", "method": ""})
    bench.write("gateway_recon.csv", fields, rows + [orphan])
    probe = bench.reconcile()
    require("ORPHAN_REFUND" in probe.codes_for_entity("rfnd_orphan00001"),
            "a refund with no original payment was not reported")
    probe.assert_no_unsafe_match()


@attack("Orphan chargeback")
def orphan_chargeback(bench: Bench) -> None:
    fields, rows = bench.read("gateway_recon.csv")
    payment = next(row for row in rows if row["type"] == "payment")
    orphan = dict(payment)
    orphan.update({"entity_id": "rfnd_chargeback01", "type": "chargeback",
                   "source_row_id": "recon:orphan-chargeback", "payment_id": "pay_doesnotexist",
                   "refund_id": "", "order_id": "", "gross_paise": "-9900",
                   "fee_paise": "0", "tax_paise": "0", "net_paise": "-9900", "method": ""})
    bench.write("gateway_recon.csv", fields, rows + [orphan])
    probe = bench.reconcile()
    require("ORPHAN_REFUND" in probe.codes_for_entity("rfnd_chargeback01"),
            "a chargeback with no original payment was not reported")
    probe.assert_no_unsafe_match()


# ------------------------------------------------- database-enforced controls


@attack("Bank credit reused by a second match", "finance_safety")
def bank_credit_reuse(bench: Bench) -> None:
    probe = bench.reconcile()
    settlement_id, line_id = probe.pairs("B")[0]
    conn = sqlite3.connect(bench.dir / "m.db")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES(?, 'B','BATCH_BANK','B0',?,0,0,1.0,'{}','probe')""", (run_id, line_id))
        second = cursor.lastrowid
        try:
            conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)",
                         (second, run_id, "B", "BANK_LINE", line_id))
        except sqlite3.IntegrityError:
            return
        raise AttackFailed("the database allowed one bank credit into two matches")
    finally:
        conn.rollback()
        conn.close()


@attack("Settlement reused by a second match", "finance_safety")
def settlement_reuse(bench: Bench) -> None:
    probe = bench.reconcile()
    settlement_id, _line_id = probe.pairs("B")[0]
    conn = sqlite3.connect(bench.dir / "m.db")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES(?, 'B','BATCH_BANK','B0','bank_probe',0,0,1.0,'{}','probe')""", (run_id,))
        second = cursor.lastrowid
        try:
            conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)",
                         (second, run_id, "B", "BATCH", settlement_id))
        except sqlite3.IntegrityError:
            return
        raise AttackFailed("the database allowed one settlement into two matches")
    finally:
        conn.rollback()
        conn.close()


@attack("Order reused by a second match", "finance_safety")
def order_reuse(bench: Bench) -> None:
    probe = bench.reconcile()
    _payment_id, order_id = probe.pairs("A")[0]
    conn = sqlite3.connect(bench.dir / "m.db")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES(?, 'A','ORDER_TXN','A0','pay_probe',0,0,1.0,'{}','probe')""", (run_id,))
        second = cursor.lastrowid
        try:
            conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)",
                         (second, run_id, "A", "ORDER", order_id))
        except sqlite3.IntegrityError:
            return
        raise AttackFailed("the database allowed one order into two matches")
    finally:
        conn.rollback()
        conn.close()


# ------------------------------------------------------ evaluator integrity


@attack("Source file modified after generation", "evaluator_integrity")
def tampered_input(bench: Bench) -> None:
    bench.reconcile()
    bench.edit("bank.csv", lambda rows: rows[0].update(
        {"credit_paise": str(int(rows[0]["credit_paise"]) + 1)}
    ))
    message = bench.expect_evaluation_failure()
    require("canonical dataset" in message, f"unexpected rejection reason: {message}")


@attack("Ground truth modified", "evaluator_integrity")
def tampered_truth(bench: Bench) -> None:
    bench.reconcile()
    manifest = json.loads((bench.dir / "manifest.json").read_text(encoding="utf-8"))
    key = next(iter(manifest["match_facts"]["B"]))
    manifest["match_facts"]["B"][key]["batch_amount_paise"] += 5_000
    (bench.dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    bench.expect_evaluation_failure()


@attack("match_facts deleted (historical exploit)", "evaluator_integrity")
def deleted_match_facts(bench: Bench) -> None:
    bench.reconcile()
    manifest = json.loads((bench.dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("match_facts")
    (bench.dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    bench.expect_evaluation_failure()


@attack("Input hashes forged", "evaluator_integrity")
def forged_hashes(bench: Bench) -> None:
    bench.reconcile()
    manifest = json.loads((bench.dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["input_hashes"]["orders"] = "0" * 64
    (bench.dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    bench.expect_evaluation_failure()


@attack("Stored database row edited", "evaluator_integrity")
def tampered_database(bench: Bench) -> None:
    bench.reconcile()
    with sqlite3.connect(bench.dir / "m.db") as conn:
        conn.execute("UPDATE raw_bank SET credit_paise=credit_paise+1 "
                     "WHERE line_id=(SELECT min(line_id) FROM raw_bank)")
    bench.expect_evaluation_failure()


@attack("Extra exception inflated into the queue", "evaluator_integrity")
def inflated_exceptions(bench: Bench) -> None:
    bench.reconcile()
    with sqlite3.connect(bench.dir / "m.db") as conn:
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        conn.execute(
            """INSERT INTO exceptions(run_id,scope_ids,reason_code,confidence,evidence,
               narrative,guidance,suggested_action,created_at)
               VALUES(?,'["invented"]','INGEST_REJECT',1.0,'{}','x','y','z','probe')""",
            (run_id,))
    message = bench.expect_evaluation_failure()
    require("exception" in message, f"unexpected rejection reason: {message}")


@attack("Expected exception suppressed", "evaluator_integrity")
def suppressed_exception(bench: Bench) -> None:
    bench.reconcile()
    with sqlite3.connect(bench.dir / "m.db") as conn:
        conn.execute("DELETE FROM exceptions WHERE id=(SELECT min(id) FROM exceptions)")
    bench.expect_evaluation_failure()


@attack("Duplicate exception scope", "evaluator_integrity")
def duplicate_exception_scope(bench: Bench) -> None:
    bench.reconcile()
    with sqlite3.connect(bench.dir / "m.db") as conn:
        row = conn.execute(
            "SELECT run_id,scope_ids,reason_code,evidence,narrative,guidance,suggested_action "
            "FROM exceptions ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return
        conn.execute(
            """INSERT INTO exceptions(run_id,scope_ids,reason_code,confidence,evidence,
               narrative,guidance,suggested_action,created_at)
               VALUES(?,?,?,1.0,?,?,?,?,'probe')""", row)
    bench.expect_evaluation_failure()


@attack("Fabricated match inserted into the database", "evaluator_integrity")
def fabricated_match(bench: Bench) -> None:
    probe = bench.reconcile()
    unmatched = next((row["line_id"] for row in probe.rows["bank"]
                      if row["line_id"] not in probe.matched("BANK_LINE")), None)
    if unmatched is None:
        return
    with sqlite3.connect(bench.dir / "m.db") as conn:
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        settlement_id = conn.execute(
            "SELECT settlement_id FROM settlement_batches ORDER BY settlement_id LIMIT 1"
        ).fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES(?, 'B','BATCH_BANK','B0',?,0,0,1.0,'{}','probe')""", (run_id, unmatched))
        match_id = cursor.lastrowid
        conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)",
                     (match_id, run_id, "B", "BANK_LINE", unmatched))
    bench.expect_evaluation_failure()


@attack("Memberless match row inserted into the ledger", "evaluator_integrity")
def memberless_match(bench: Bench) -> None:
    """A match row with no members must not be invisible to the evaluator."""
    bench.reconcile()
    with sqlite3.connect(bench.dir / "m.db") as conn:
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES(?, 'B','BATCH_BANK','B0','bank_probe',0,0,1.0,'{}','probe')""",
            (run_id,))
    message = bench.expect_evaluation_failure()
    require("member shape" in message, f"unexpected rejection reason: {message}")


@attack("Wrong-kind member attached to a match", "finance_safety")
def wrong_member_kind(bench: Bench) -> None:
    """The schema itself must refuse an order recorded as a Plane-B member."""
    bench.reconcile()
    conn = sqlite3.connect(bench.dir / "m.db")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES(?, 'B','BATCH_BANK','B0','bank_probe',0,0,1.0,'{}','probe')""",
            (run_id,))
        match_id = cursor.lastrowid
        try:
            conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)",
                         (match_id, run_id, "B", "ORDER", "order_000000"))
        except sqlite3.IntegrityError:
            return
        raise AttackFailed("the ledger accepted an ORDER as a settlement-plane member")
    finally:
        conn.rollback()
        conn.close()


@attack("Unknown plane written into the ledger", "finance_safety")
def unknown_plane(bench: Bench) -> None:
    bench.reconcile()
    conn = sqlite3.connect(bench.dir / "m.db")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
        try:
            conn.execute(
                """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
                   date_gap_bd,confidence,evidence,created_at)
                   VALUES(?, 'C','BATCH_BANK','B0','x',0,0,1.0,'{}','probe')""",
                (run_id,))
        except sqlite3.IntegrityError:
            return
        raise AttackFailed("the ledger accepted a match on an unknown plane")
    finally:
        conn.rollback()
        conn.close()


@attack("Negative bank credit")
def negative_bank_credit(bench: Bench) -> None:
    bench.edit("bank.csv", lambda rows: rows[0].update({"credit_paise": "-500000"}))
    probe = bench.reconcile()
    probe.assert_no_unsafe_match()


@attack("Zero-width space hidden inside a bank UTR")
def zero_width_utr(bench: Bench) -> None:
    settlement_id, line_id = next(iter(bench.expected_b().items()))

    def hide(rows: list[dict[str, str]]) -> None:
        for row in rows:
            if row["line_id"] == line_id:
                reference = row["ref_no"]
                row["narration"] = row["narration"].replace(
                    reference, f"{reference[:4]}\u200b{reference[4:]}"
                )
    bench.edit("bank.csv", hide)
    probe = bench.reconcile()
    probe.assert_no_unsafe_match()


# ----------------------------------------------------------------- runner


# Most attacks start from a clean dataset so the injected fault is the only
# anomaly. Attacks that tamper with the exception queue need a profile that
# actually has one.
NEEDS_MIXED = {
    "Missing bank credit", "Unexplained bank credit",
    "Extra exception inflated into the queue", "Expected exception suppressed",
    "Duplicate exception scope",
}


def _profile_for(label: str) -> str:
    return "mixed" if label in NEEDS_MIXED else "clean"


def run_attack(item: Attack) -> AttackOutcome:
    with tempfile.TemporaryDirectory(prefix="milaan-adversary-") as tmp:
        bench = Bench(Path(tmp) / "run", profile=_profile_for(item.label))
        try:
            item.run(bench)
        except AttackFailed as exc:
            return AttackOutcome(item.label, item.category, False, str(exc))
        except Exception as exc:  # noqa: BLE001 - an unexpected crash is a failure
            return AttackOutcome(item.label, item.category, False,
                                 f"unexpected {type(exc).__name__}: {exc}")
        return AttackOutcome(item.label, item.category, True)


def run_all() -> Iterator[AttackOutcome]:
    for item in REGISTRY:
        yield run_attack(item)


def _ai_outcomes() -> list[AttackOutcome]:
    """Hostile-model probes, run against a freshly reconciled and evaluated run."""
    from milaan.evalx.ai_probes import run_probes

    with tempfile.TemporaryDirectory(prefix="milaan-ai-probe-") as tmp:
        bench = Bench(Path(tmp) / "run", profile="mixed")
        bench.reconcile()
        bench.evaluate()
        return [AttackOutcome(item.label, "ai_authority", item.passed, item.detail)
                for item in run_probes(bench.dir, bench.dir / "m.db")]


def main() -> int:
    """Print the adversarial scorecard. Exit non-zero if anything is unsafe."""
    print("MILAAN ADVERSARIAL SAFETY")
    print("=" * 63)
    outcomes = list(run_all())
    outcomes.extend(_ai_outcomes())
    width = max(len(item.label) for item in outcomes) + 2
    for item in outcomes:
        dots = "." * max(3, width - len(item.label))
        print(f"{item.label} {dots} {'PASS' if item.passed else 'FAIL'}")
        if not item.passed:
            print(f"    -> {item.detail}")
    print("=" * 63)
    by_category = {
        "Financial safety failures": "finance_safety",
        "Evaluator-integrity failures": "evaluator_integrity",
        "AI authority violations": "ai_authority",
    }
    failures = 0
    for title, category in by_category.items():
        count = sum(1 for item in outcomes if item.category == category and not item.passed)
        failures += count
        print(f"{title}: {count}")
    print(f"Attacks executed: {len(outcomes)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
