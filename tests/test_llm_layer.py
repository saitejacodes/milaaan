from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from milaan.llm.cache import ResponseCache
from milaan.llm.polish import content_is_invariant
from milaan.models import ExceptionItem


class LanguageLayerTests(unittest.TestCase):
    def test_cache_survives_independent_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "persistent.sqlite"
            with ResponseCache(path) as cache:
                cache.put("k", "p", "m", "3.0", {"narrative": "safe"})
            with ResponseCache(path) as cache:
                self.assertEqual(cache.get("k"), {"narrative": "safe"})

    def test_content_filter_rejects_invented_id_and_amount(self) -> None:
        item = ExceptionItem(("setl_1",), "MISSING_IN_BANK", 1.0,
                             {"settlement_id": "setl_1", "amount_paise": 10000})
        self.assertTrue(content_is_invariant(item, "Check setl_1 for ₹100.00.", []))
        self.assertFalse(content_is_invariant(item, "Check setl_9 for ₹100.00.", []))
        self.assertFalse(content_is_invariant(item, "Check setl_1 for ₹999.00.", []))


if __name__ == "__main__":
    unittest.main()
