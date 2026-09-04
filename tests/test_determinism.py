"""The same inputs must produce the same finance output, every time.

A reconciliation result that shifts between runs is not defensible: a finance
team cannot re-derive last week's close, and an auditor cannot reproduce a
figure. These tests pin generation, matching, exceptions and published metrics.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


CASES = ((200, 3, "clean"), (600, 42, "mixed"), (600, 11, "hard"))

# Wall-clock and environment values belong in telemetry, not in a functional
# result, so they are excluded from the functional fingerprint by design.
VOLATILE_KEYS = {"truth_integrity"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finance_state(database: Path) -> dict[str, object]:
    """Every decision the engine made, in a stable order."""
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        matches = sorted(
            (row["plane"], row["tier"], row["right_id"], row["amount_diff_paise"],
             row["date_gap_bd"], row["confidence"], row["evidence"])
            for row in conn.execute("SELECT * FROM matches")
        )
        members = sorted(
            (row["plane"], row["entity_type"], row["entity_id"])
            for row in conn.execute("SELECT * FROM match_members")
        )
        exceptions = sorted(
            (row["scope_ids"], row["reason_code"], row["confidence"], row["evidence"],
             row["narrative"], row["guidance"], row["suggested_action"])
            for row in conn.execute("SELECT * FROM exceptions")
        )
        batches = sorted(
            (row["settlement_id"], row["settlement_utr"], row["amount_paise"],
             row["processed_at"], row["member_count"], row["tainted"])
            for row in conn.execute("SELECT * FROM settlement_batches")
        )
        quarantine = sorted(
            (row["source"], row["source_row_id"], row["reason"], row["raw_json"])
            for row in conn.execute("SELECT * FROM quarantine_rows")
        )
    finally:
        conn.close()
    return {"matches": matches, "members": members, "exceptions": exceptions,
            "batches": batches, "quarantine": quarantine}


class DeterminismTests(unittest.TestCase):
    def _run(self, root: Path, name: str, records: int, seed: int, profile: str) -> dict:
        run = root / name
        generate_to_directory(records, seed, profile, run)
        run_pipeline(run, run / "m.db", "mock")
        evaluate_run(run, run / "m.db", run, profile)
        metrics = json.loads((run / "functional_metrics.json").read_text(encoding="utf-8"))
        return {
            "inputs": {name: sha256(run / f"{name}.csv")
                       for name in ("orders", "gateway_recon", "bank")},
            "manifest": sha256(run / "manifest.json"),
            "state": finance_state(run / "m.db"),
            "metrics": {key: value for key, value in metrics.items()
                        if key not in VOLATILE_KEYS},
        }

    def test_repeated_runs_are_identical(self) -> None:
        for records, seed, profile in CASES:
            with self.subTest(profile=profile, seed=seed), \
                    tempfile.TemporaryDirectory(prefix="milaan-determinism-") as tmp:
                root = Path(tmp)
                first = self._run(root, "a", records, seed, profile)
                second = self._run(root, "b", records, seed, profile)
                self.assertEqual(first["inputs"], second["inputs"],
                                 "generation is not deterministic")
                self.assertEqual(first["manifest"], second["manifest"],
                                 "ground truth is not deterministic")
                self.assertEqual(first["state"], second["state"],
                                 "the engine made different decisions on identical input")
                self.assertEqual(first["metrics"], second["metrics"],
                                 "published metrics differ between identical runs")

    def test_different_seeds_produce_different_data(self) -> None:
        """Guards against a determinism test that would pass on a constant."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._run(root, "a", 200, 1, "mixed")
            second = self._run(root, "b", 200, 2, "mixed")
            self.assertNotEqual(first["inputs"], second["inputs"])

    def test_metrics_exclude_wall_clock_values(self) -> None:
        """Functional results must not carry anything environment-dependent."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(200, 4, "mixed", run)
            run_pipeline(run, run / "m.db", "mock")
            evaluate_run(run, run / "m.db", run, "mixed")
            metrics = json.loads((run / "functional_metrics.json").read_text())
            text = json.dumps({k: v for k, v in metrics.items() if k != "truth_integrity"})
            for token in ("wall_ms", "records_per_second", "latency", "measured_at",
                          "started_at", "elapsed"):
                with self.subTest(token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
