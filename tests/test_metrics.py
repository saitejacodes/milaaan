from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from milaan.db import create_fresh, insert_run
from milaan.evalx.metrics import compute_metrics


class MetricsUnitTests(unittest.TestCase):
    def test_wrong_batch_to_right_credit_is_fully_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = create_fresh(Path(tmp) / "m.db")
            hashes = {key: key * 8 for key in ("orders", "txns", "bank", "fees", "timing")}
            insert_run(conn, run_id="1-clean-mock", seed=1, profile="clean",
                       llm_mode="mock", hashes=hashes, git_sha=None)
            cur = conn.execute(
                """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
                   date_gap_bd,confidence,evidence,created_at)
                   VALUES('1-clean-mock','B','BATCH_BANK','B0','bank_1',0,0,1.0,'{}','x')"""
            )
            match_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO match_members VALUES(?,?,?,?,?)",
                ((match_id, "1-clean-mock", "B", "BATCH", "setl_wrong"),
                 (match_id, "1-clean-mock", "B", "BANK_LINE", "bank_1")),
            )
            manifest = {
                "seed": 1, "profile": "clean", "generator_version": "1.2.1",
                "tier_labels": {"setl_right": "T0"},
                "expectations": {
                    "plane_a_matches": [],
                    "plane_b_matches": [["setl_right", "bank_1"]],
                    "exceptions": [],
                },
            }
            metrics = compute_metrics(conn, manifest)
            conn.close()
            self.assertEqual(metrics["planes"]["B"]["auto_match"]["numerator"], 0)
            self.assertEqual(metrics["planes"]["B"]["false_match_count"], 1)


if __name__ == "__main__":
    unittest.main()
