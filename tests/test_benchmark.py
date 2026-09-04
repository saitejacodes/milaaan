from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from milaan.evalx.benchmark import run_benchmark


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_records_full_provenance_per_repetition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "benchmark.json"
            message = run_benchmark(out, sizes=(50, 200), repetitions=3)
            payload = json.loads(out.read_text())

        self.assertIn("median engine throughput", message)
        self.assertEqual(payload["note"], "canonical sweep")
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(len(payload["raw_runs"]), 6)

        environment = payload["environment"]
        for key in ("python_version", "platform", "machine", "processor", "measured_at"):
            self.assertTrue(environment[key], key)
        self.assertIn("git_sha", environment)

        for row in payload["results"]:
            self.assertEqual(row["repetitions"], 3)
            self.assertTrue(row["all_gates_passed"])
            self.assertEqual(row["total_false_matches"], 0)
            self.assertEqual(row["min_plane_a_precision"], 1.0)
            self.assertEqual(row["min_plane_b_precision"], 1.0)
            self.assertEqual(row["min_source_conservation"], 1.0)
            self.assertEqual(row["max_abs_amount_delta_paise"], 0)
            # A physical record count strictly larger than the requested order
            # count is what makes the 50+ record track requirement real.
            self.assertGreater(row["median_physical_source_records"], row["requested_orders"])
            rate = row["reconcile_records_per_second"]
            self.assertLessEqual(rate["min"], rate["median"])
            self.assertLessEqual(rate["median"], rate["max"])
            self.assertGreater(rate["min"], 0)

        for run in payload["raw_runs"]:
            self.assertEqual(run["gate"]["status"], "PASS")
            self.assertEqual(run["truth_integrity"], "PASS")
            self.assertEqual(run["amount_delta_paise"], 0)
            self.assertEqual(run["source_conservation"], 1.0)
            self.assertEqual(
                run["orders_rows"] + run["gateway_recon_rows"] + run["bank_rows"],
                run["physical_source_records"],
            )
            self.assertEqual(
                run["generate_ms"] + run["reconcile_wall_ms"] + run["evaluate_ms"],
                run["total_ms"],
            )
            self.assertEqual(set(run["input_hashes"]), {"orders", "gateway_recon", "bank"})

    def test_a_short_sweep_is_labelled_non_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "benchmark.json"
            run_benchmark(out, sizes=(50,), repetitions=1)
            payload = json.loads(out.read_text())
        self.assertIn("NON-CANONICAL", payload["note"])

    def test_reported_throughput_excludes_generation_and_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "benchmark.json"
            run_benchmark(out, sizes=(200,), repetitions=3)
            payload = json.loads(out.read_text())
        self.assertIn("reconciliation engine only", payload["measurement"]["reported_throughput"])
        self.assertIn("generation", payload["measurement"]["excluded_from_throughput"])
        for run in payload["raw_runs"]:
            expected = round(
                run["physical_source_records"] / max(run["reconcile_wall_ms"] / 1000, 0.001), 2
            )
            self.assertAlmostEqual(run["reconcile_records_per_second"], expected, places=2)


if __name__ == "__main__":
    unittest.main()
