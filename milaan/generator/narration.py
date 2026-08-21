"""UTR families, bank narration templates, and deterministic damage operators."""

from __future__ import annotations

import random
import re
import string
from typing import Any


F1_RE = re.compile(r"^[A-Z]{4}[A-Z0-9]{12}$")
F2_RE = re.compile(r"^[0-9]{10}[a-z0-9]{6}$")
F3_RE = re.compile(r"^[0-9]{12}$")
SHAPE_RES = {"F1": F1_RE, "F2": F2_RE, "F3": F3_RE}

TEMPLATES = (
    "NEFT-{utr}-RAZORPAY SOFTWARE PVT LTD-SETL",
    "IMPS/P2A/{utr}/RAZORPAYSOF/UTIB0000123",
    "BY TRANSFER-NEFT*HDFC0000001*{utr}*RAZORPAY SOFT",
)

CONFUSABLE_REPLACEMENTS = {
    "0": "O", "O": "0", "o": "0",
    "1": "I", "I": "1", "i": "1", "L": "1", "l": "1",
}


def _chars(rng: random.Random, alphabet: str, count: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(count))


def make_utr(rng: random.Random, family: str | None = None) -> tuple[str, str]:
    if family is None:
        roll = rng.random()
        family = "F1" if roll < 0.45 else "F2" if roll < 0.90 else "F3"
    if family == "F1":
        utr = _chars(rng, string.ascii_uppercase, 4) + "1" + _chars(
            rng, string.ascii_uppercase + string.digits, 11
        )
    elif family == "F2":
        utr = _chars(rng, string.digits, 9) + "1" + _chars(
            rng, string.ascii_lowercase + string.digits, 6
        )
    elif family == "F3":
        utr = _chars(rng, string.digits, 11) + "1"
    else:
        raise ValueError(f"unknown UTR family: {family}")
    return utr, family


def clean_narration(rng: random.Random, utr: str) -> str:
    return rng.choice(TEMPLATES).format(utr=utr)


def mangle_narration(rng: random.Random, narration: str, utr: str,
                     operator: str) -> tuple[str, dict[str, Any]]:
    """Apply exactly one documented operator and return auditable details."""
    if utr not in narration and operator not in {"PREFIX_NOISE", "STRIP_SPACES", "COUNTERPARTY_TYPO"}:
        raise ValueError("UTR must occur verbatim before identifier damage")
    detail: dict[str, Any] = {"op": operator}
    if operator == "TRUNCATE_SUFFIX":
        length = rng.randint(6, min(10, len(utr)))
        replacement = utr[-length:]
        detail["suffix_length"] = length
        return narration.replace(utr, replacement, 1), detail
    if operator == "STRIP_SPACES":
        return narration.replace(" ", ""), detail
    if operator == "COUNTERPARTY_TYPO":
        changed = narration.replace("RAZORPAY", "RAZORPY", 1)
        if changed == narration:
            changed = narration.replace("RAZORPAYSOF", "RAZRPAYSOF", 1)
        return changed, detail
    if operator == "PREFIX_NOISE":
        prefix = rng.choice(("MB:", "INB ", "TRF/"))
        detail["prefix"] = prefix
        return prefix + narration, detail
    if operator == "INSERT_SEPARATOR":
        pos = rng.randint(2, len(utr) - 2)
        sep = rng.choice(("-", " "))
        damaged = utr[:pos] + sep + utr[pos:]
        detail.update({"pos": pos, "separator": sep})
        return narration.replace(utr, damaged, 1), detail
    if operator == "CONFUSABLE_SUB":
        positions = [i for i, ch in enumerate(utr) if ch in CONFUSABLE_REPLACEMENTS]
        if not positions:
            raise ValueError("UTR has no allow-listed confusable character")
        pos = rng.choice(positions)
        replacement = CONFUSABLE_REPLACEMENTS[utr[pos]]
        damaged = utr[:pos] + replacement + utr[pos + 1:]
        detail.update({"pos": pos, "from": utr[pos], "to": replacement})
        return narration.replace(utr, damaged, 1), detail
    if operator == "DROP_UTR":
        return narration.replace(utr, "NOREF", 1), detail
    raise ValueError(f"unknown narration operator: {operator}")
