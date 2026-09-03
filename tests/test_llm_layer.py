from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from milaan.llm.cache import ResponseCache
from milaan.llm.polish import content_is_invariant
from milaan.exceptions.actions import with_canonical_language
from milaan.models import ExceptionItem


class LanguageLayerTests(unittest.TestCase):
    def test_cache_survives_independent_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "persistent.sqlite"
            with ResponseCache(path) as cache:
                cache.put("k", "p", "m", "3.1", {"narrative": "safe"})
            with ResponseCache(path) as cache:
                self.assertEqual(cache.get("k"), {"narrative": "safe"})

    def test_content_filter_rejects_invented_id_and_amount(self) -> None:
        item = with_canonical_language(ExceptionItem(
            ("setl_1",), "MISSING_IN_BANK", 1.0,
            {"settlement_id": "setl_1", "amount_paise": 10000},
        ))
        guidance = [line.removeprefix("- ") for line in item.guidance.splitlines()]
        self.assertTrue(content_is_invariant(item, item.narrative, guidance))
        self.assertFalse(content_is_invariant(item, "Check setl_9 for ₹100.00.", guidance))
        self.assertFalse(content_is_invariant(item, "Check setl_1 for ₹999.00.", guidance))

    def test_content_filter_rejects_semantic_reversal_and_empty_output(self) -> None:
        item = with_canonical_language(ExceptionItem(
            ("setl_1",), "MISSING_IN_BANK", 1.0,
            {"settlement_id": "setl_1", "amount_paise": 10000},
        ))
        self.assertFalse(content_is_invariant(
            item, "Settlement received successfully. No exception remains.", ["Close the case."],
        ))
        self.assertFalse(content_is_invariant(item, "", []))


if __name__ == "__main__":
    unittest.main()
