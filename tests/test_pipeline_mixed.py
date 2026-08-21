from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


class MixedPipelineGateTests(unittest.TestCase):
    def test_seed_42_passes_mixed_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(1200, 42, "mixed", run)
            run_pipeline(run, run / "milaan.db", "mock")
            evaluate_run(run, run / "milaan.db", run, "mixed")
            metrics = json.loads((run / "functional_metrics.json").read_text())
            self.assertEqual(metrics["false_match_count"], 0)
            self.assertEqual(metrics["exceptions"]["recall"]["rate"], 1.0)
            self.assertEqual(metrics["exceptions"]["precision"]["rate"], 1.0)
            self.assertEqual(metrics["completeness"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
