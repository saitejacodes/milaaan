from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


class GoldenMetricsTests(unittest.TestCase):
    def test_seed_42_functional_metrics_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            generate_to_directory(1200, 42, "mixed", run)
            run_pipeline(run, run / "m.db", "mock")
            evaluate_run(run, run / "m.db", run, "mixed")
            golden = (Path(__file__).parents[1] / "milaan" / "evalx" / "golden" /
                      "functional_metrics_seed42_mock.json")
            self.assertEqual((run / "functional_metrics.json").read_bytes(), golden.read_bytes())


if __name__ == "__main__":
    unittest.main()
