from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


class CleanPipelineGateTests(unittest.TestCase):
    def test_clean_seeds_reconcile_completely(self) -> None:
        for seed in (1, 2):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp) / "run"
                generate_to_directory(200, seed, "clean", run)
                run_pipeline(run, run / "milaan.db", "mock")
                evaluate_run(run, run / "milaan.db", run, "clean")
                metrics = json.loads((run / "functional_metrics.json").read_text())
                self.assertEqual(metrics["false_match_count"], 0)
                self.assertEqual(metrics["planes"]["A"]["auto_match"]["rate"], 1.0)
                self.assertEqual(metrics["planes"]["B"]["auto_match"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
