from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from milaan.generator.emit import generate_to_directory


class GeneratorGateTests(unittest.TestCase):
    def test_seed_is_byte_identical_and_manifest_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left, right = Path(tmp) / "left", Path(tmp) / "right"
            generate_to_directory(300, 42, "mixed", left)
            generate_to_directory(300, 42, "mixed", right)
            names = ("orders.csv", "gateway_recon.csv", "bank.csv", "manifest.json", "run_meta.json")
            for name in names:
                self.assertEqual((left / name).read_bytes(), (right / name).read_bytes(), name)
            manifest = json.loads((left / "manifest.json").read_text())
            injected_ids = [entity_id for item in manifest["injected"] for entity_id in item["ids"]]
            self.assertEqual(len(injected_ids), len(set(injected_ids)))
            self.assertTrue(manifest["structural"]["multi_settlement_days"])

    def test_engine_and_ingest_do_not_reference_ground_truth_manifest(self) -> None:
        root = Path(__file__).parents[1] / "milaan"
        for package in (root / "engine", root / "ingest"):
            for path in package.glob("*.py"):
                self.assertNotIn("manifest.json", path.read_text(), str(path))


if __name__ == "__main__":
    unittest.main()
