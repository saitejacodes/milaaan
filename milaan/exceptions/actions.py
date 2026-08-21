"""Canonical narratives and investigation steps for every exception code."""

from __future__ import annotations

from dataclasses import replace

from milaan.models import ExceptionItem


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
    "NARRATION_UNPARSEABLE": ("Check the original bank narration.", "Obtain the UTR or remittance advice."),
    "FEE_MODEL_VIOLATION": ("Recalculate fee and GST in paise.", "Compare the gateway pricing configuration."),
    "ORPHAN_REFUND": ("Locate the original payment.", "Confirm the refund belongs to this merchant account."),
    "INGEST_REJECT": ("Correct the malformed source row.", "Re-export the affected source file."),
}


def canonical_text(item: ExceptionItem) -> tuple[str, str, str]:
    ids = ", ".join(item.scope_ids)
    narrative = (
        f"Milaan abstained on {ids} with reason {item.reason}. "
        "No accounting match was posted for the affected scope."
    )
    checks = GUIDANCE.get(item.reason, ("Review the attached evidence.",))
    guidance = "\n".join(f"- {check}" for check in checks[:3])
    action = checks[0]
    return narrative, guidance, action


def with_canonical_language(item: ExceptionItem) -> ExceptionItem:
    narrative, guidance, action = canonical_text(item)
    return replace(item, narrative=narrative, guidance=guidance, suggested_action=action)
