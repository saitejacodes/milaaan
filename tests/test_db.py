from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from milaan.db import create_fresh, insert_run


class DatabaseIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = create_fresh(Path(self.tmp.name) / "run.db")
        hashes = {key: "0" * 64 for key in ("orders", "txns", "bank", "fees", "timing")}
        insert_run(self.conn, run_id="1-clean-mock", seed=1, profile="clean",
                   llm_mode="mock", hashes=hashes, git_sha=None)
        self.conn.execute(
            """INSERT INTO matches(run_id,plane,kind,tier,right_id,amount_diff_paise,
               date_gap_bd,confidence,evidence,created_at)
               VALUES('1-clean-mock','A','ORDER_TXN','A0','pay_1',0,0,1.0,'{}','x')"""
        )
        self.match_id = self.conn.execute("SELECT match_id FROM matches").fetchone()[0]

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_foreign_keys_are_enabled(self) -> None:
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_member_columns_are_not_nullable(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO match_members VALUES(?,?,?,?,?)",
                (self.match_id, None, "A", "ORDER", "order_1"),
            )

    def test_duplicate_member_is_rejected(self) -> None:
        row = (self.match_id, "1-clean-mock", "A", "ORDER", "order_1")
        self.conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)", row)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO match_members VALUES(?,?,?,?,?)", row)

    def test_child_scope_must_match_parent(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO match_members VALUES(?,?,?,?,?)",
                (self.match_id, "1-clean-mock", "B", "ORDER", "order_1"),
            )


if __name__ == "__main__":
    unittest.main()
