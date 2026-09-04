"""Canonical narratives and investigation steps for every exception code.

Two rules shape this file.

The narrative states the *control* that fired and why abstaining was the safe
outcome, in words a finance operator can act on. "Milaan abstained on X" tells a
reader nothing they did not already know from the reason code.

The narrative never introduces a new monetary figure. Amounts are displayed
beside the exception from verified evidence; keeping them out of the prose means
the optional language layer has no amount it could drift, and the fail-closed
firewall in milaan.llm.polish has a smaller surface to defend.
"""

from __future__ import annotations

from dataclasses import replace

from milaan.models import ExceptionItem


NARRATIVES = {
    "MISSING_IN_BANK":
        "The gateway settled this batch, but no bank credit in the statement matches "
        "it inside the allowed value-date window. The cash is claimed but not yet "
        "evidenced, so it is reported as expected-but-unbanked rather than banked.",
    "UNKNOWN_BANK_CREDIT":
        "This bank credit does not correspond to any settlement batch Milaan can "
        "verify. Attributing it to a batch on amount alone would post money against "
        "the wrong obligation, so it stays unattributed.",
    "PAID_ORDER_MISSING_FROM_GATEWAY":
        "The merchant recorded this order as paid, but no gateway payment row exists "
        "for it. Milaan will not infer a payment that the gateway never reported.",
    "AMOUNT_MISMATCH_BEYOND_TOL":
        "The identifiers line up but the amounts do not. Amount equality is exact on "
        "every tier, so a difference of even one paise is a real discrepancy and is "
        "raised here instead of being absorbed inside a posted match.",
    "DATE_OUT_OF_WINDOW":
        "The records reference each other, but the chronology is not one this control "
        "accepts: either the later record precedes the earlier one, or the gap exceeds "
        "the configured business-day window.",
    "AMBIGUOUS_TIE":
        "More than one candidate fits the evidence equally well, and no identifier "
        "separates them. Choosing between equally supported candidates would be an "
        "arbitrary decision about real money, so Milaan abstains on all of them.",
    "AMBIGUOUS_COMBINED":
        "One bank credit appears to combine two settlement batches. Milaan deliberately "
        "does not run a subset-sum solver over settlement amounts: a coincidental sum "
        "is indistinguishable from a real combined payout without the bank's breakup.",
    "DUPLICATE_UTR":
        "One bank reference appears on two different credits. Until the authoritative "
        "line is confirmed, matching either one risks posting the same settlement twice.",
    "DUPLICATE_BANK_LINE":
        "The same bank line appears twice in the statement export. Matching a duplicated "
        "credit would double-count cash that arrived once.",
    "UTR_CONFLICT_IN_BATCH":
        "Members of a single settlement batch carry different non-null bank references, "
        "so the batch does not have one trustworthy identifier to match on.",
    "UNSUPPORTED_MEMBER_IN_BATCH":
        "This batch contains a member type no approved adapter can interpret. The batch "
        "total cannot be trusted while part of it is unreadable.",
    "TAINTED_SETTLEMENT":
        "A member of this settlement was rejected during ingestion, so the batch total "
        "is incomplete. The whole batch is held: matching the surviving members against "
        "a bank credit would reconcile a total that is missing money.",
    "UNSUPPORTED_TXN_TYPE":
        "This gateway row carries a transaction type outside the approved set. It is "
        "quarantined rather than guessed at, and its settlement is held with it.",
    "DUPLICATE_SOURCE_ID":
        "The same source identifier appears on more than one row. Milaan keeps the first "
        "row and quarantines the rest, because silently merging them could either "
        "double-count or discard a real transaction.",
    "IDENTITY_CONFLICT":
        "The order and payment identifiers contradict each other: an order claims more "
        "than one payment, or a payment is claimed by more than one order. Resolving "
        "this by sort order would be an arbitrary choice, so the whole conflict is held.",
    "UNMATCHED_GATEWAY_PAYMENT":
        "The gateway captured this payment, but no merchant order references it and no "
        "unique amount-and-date evidence identifies one.",
    "NARRATION_UNPARSEABLE":
        "The bank narration carries no reference Milaan can recover deterministically, "
        "so this credit cannot be tied to a settlement by identifier.",
    "FEE_MODEL_VIOLATION":
        "Fee, GST or net arithmetic does not hold for this row against the configured "
        "pricing. The row is quarantined because its net amount cannot be trusted in a "
        "settlement total.",
    "ORPHAN_REFUND":
        "This refund or chargeback references an original payment that is not present. "
        "Milaan will not attribute a reversal to a payment it cannot see.",
    "INGEST_REJECT":
        "This source row could not be read safely -- a malformed value, a missing "
        "required field, or contradictory settlement timestamps. It is quarantined with "
        "its original content preserved for audit.",
}

GUIDANCE = {
    "MISSING_IN_BANK": ("Check the settlement UTR in the bank portal.", "Raise the missing credit with the bank."),
    "UNKNOWN_BANK_CREDIT": ("Trace the bank reference to its remitter.", "Confirm whether the credit belongs to another account."),
    "PAID_ORDER_MISSING_FROM_GATEWAY": ("Search the gateway by order and payment reference.", "Verify capture or webhook delivery."),
    "AMOUNT_MISMATCH_BEYOND_TOL": ("Compare gross, fee, tax, and net fields.", "Check for a partial refund or manual adjustment."),
    "DATE_OUT_OF_WINDOW": ("Verify the settlement processing date.", "Check bank holidays and delayed value dates."),
    "AMBIGUOUS_TIE": ("Review every listed candidate together.", "Do not post until one candidate has independent evidence."),
    "AMBIGUOUS_COMBINED": ("Confirm whether multiple settlements were combined.", "Request the bank credit breakup."),
    "DUPLICATE_UTR": ("Confirm which bank line is authoritative.", "Remove or reverse the duplicate source entry."),
    "DUPLICATE_BANK_LINE": ("Compare source row identifiers.", "De-duplicate the bank export before posting."),
    "UTR_CONFLICT_IN_BATCH": ("Inspect all non-null member UTRs.", "Correct the recon export before retrying."),
    "UNSUPPORTED_MEMBER_IN_BATCH": ("Inspect the unsupported member row.", "Re-run only after the whole batch is supported."),
    "TAINTED_SETTLEMENT": ("Inspect every quarantined settlement member.", "Re-export the settlement and re-run once the complete batch is valid."),
    "UNSUPPORTED_TXN_TYPE": ("Map the unsupported transaction type explicitly.", "Do not post its settlement until the adapter is approved."),
    "DUPLICATE_SOURCE_ID": ("Compare the duplicate source rows.", "Keep one authoritative row and re-export the source."),
    "IDENTITY_CONFLICT": ("Verify the order and payment identifiers at source.", "Do not choose between conflicting identity claims automatically."),
    "UNMATCHED_GATEWAY_PAYMENT": ("Search for the merchant order using gateway evidence.", "Post only after an independent order reference is confirmed."),
    "NARRATION_UNPARSEABLE": ("Check the original bank narration.", "Obtain the UTR or remittance advice."),
    "FEE_MODEL_VIOLATION": ("Recalculate fee and GST in paise.", "Compare the gateway pricing configuration."),
    "ORPHAN_REFUND": ("Locate the original payment.", "Confirm the refund belongs to this merchant account."),
    "INGEST_REJECT": ("Correct the malformed source row.", "Re-export the affected source file."),
}

GENERIC = (
    "Milaan could not establish defensible evidence for this scope, so no accounting "
    "match was posted."
)


def canonical_text(item: ExceptionItem) -> tuple[str, str, str]:
    ids = ", ".join(item.scope_ids)
    explanation = NARRATIVES.get(item.reason, GENERIC)
    narrative = f"{explanation} Affected records: {ids}."
    checks = GUIDANCE.get(item.reason, ("Review the attached evidence.",))
    guidance = "\n".join(f"- {check}" for check in checks[:3])
    action = checks[0]
    return narrative, guidance, action


def with_canonical_language(item: ExceptionItem) -> ExceptionItem:
    narrative, guidance, action = canonical_text(item)
    return replace(item, narrative=narrative, guidance=guidance, suggested_action=action)
