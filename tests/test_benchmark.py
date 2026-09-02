from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from milaan.evalx.benchmark import run_benchmark


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_real_denominator_and_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "benchmark.json"
            run_benchmark(out, sizes=(50,), repetitions=1)
            result = json.loads(out.read_text())["results"][0]
            self.assertEqual(result["orders"], 50)
            self.assertGreaterEqual(result["median_source_records"], 50)
            self.assertGreater(result["median_source_records_per_second"], 0)


if __name__ == "__main__":
    unittest.main()
