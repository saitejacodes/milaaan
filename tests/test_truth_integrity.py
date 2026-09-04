"""Truth-boundary attacks. Every one of them must fail closed.

Each test starts from a *pristine, passing* run whose ``functional_metrics.json``
already exists, applies exactly one tampering operation, and asserts two things:

* evaluation raises, and
* the published metrics file is gone.

The second assertion matters as much as the first. An evaluator that raises but
leaves yesterday's passing numbers on disk still lets a tampered run be quoted.
"""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from milaan.engine.pipeline import run_pipeline
from milaan.evalx.harness import evaluate_run
from milaan.generator.emit import generate_to_directory


RECORDS, SEED, PROFILE = 200, 4242, "mixed"


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class TruthIntegrityTests(unittest.TestCase):
    """One pristine run is built once; every test tampers with a private copy."""

    pristine: Path
    _root: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = tempfile.TemporaryDirectory(prefix="milaan-truth-")
        cls.pristine = Path(cls._root.name) / "pristine"
        generate_to_directory(RECORDS, SEED, PROFILE, cls.pristine)
        run_pipeline(cls.pristine, cls.pristine / "m.db", "mock")
        evaluate_run(cls.pristine, cls.pristine / "m.db", cls.pristine, PROFILE)
        assert (cls.pristine / "functional_metrics.json").is_file()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._root.cleanup()

    def setUp(self) -> None:
        self._case = tempfile.TemporaryDirectory(prefix="milaan-attack-")
        self.run = Path(self._case.name) / "run"
        shutil.copytree(self.pristine, self.run)
        self.addCleanup(self._case.cleanup)

    # -- helpers ---------------------------------------------------------

    def manifest(self) -> dict:
        return json.loads((self.run / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, manifest: dict) -> None:
        (self.run / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def meta(self) -> dict:
        return json.loads((self.run / "run_meta.json").read_text(encoding="utf-8"))

    def write_meta(self, meta: dict) -> None:
        (self.run / "run_meta.json").write_text(
            json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def assert_fails_closed(self) -> str:
        """Evaluation must raise and must leave no quotable metrics behind."""
        with self.assertRaises(RuntimeError) as caught:
            evaluate_run(self.run, self.run / "m.db", self.run, PROFILE)
        self.assertFalse(
            (self.run / "functional_metrics.json").exists(),
            "a failed gate must not leave a published metrics file on disk",
        )
        return str(caught.exception)

    def assert_still_passes(self) -> None:
        evaluate_run(self.run, self.run / "m.db", self.run, PROFILE)
        self.assertTrue((self.run / "functional_metrics.json").is_file())

    # -- baseline --------------------------------------------------------

    def test_00_untampered_copy_still_passes(self) -> None:
        self.assert_still_passes()

    # -- match_facts attacks ---------------------------------------------

    def test_01_delete_entire_match_facts(self) -> None:
        manifest = self.manifest()
        manifest.pop("match_facts")
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_02_delete_one_plane_a_fact(self) -> None:
        manifest = self.manifest()
        manifest["match_facts"]["A"].pop(next(iter(manifest["match_facts"]["A"])))
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_03_delete_one_plane_b_fact(self) -> None:
        manifest = self.manifest()
        manifest["match_facts"]["B"].pop(next(iter(manifest["match_facts"]["B"])))
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_04_modify_plane_a_amount_truth(self) -> None:
        manifest = self.manifest()
        key = next(iter(manifest["match_facts"]["A"]))
        manifest["match_facts"]["A"][key]["payment_gross_paise"] += 1
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_05_modify_plane_b_settlement_amount_truth(self) -> None:
        manifest = self.manifest()
        key = next(iter(manifest["match_facts"]["B"]))
        manifest["match_facts"]["B"][key]["batch_amount_paise"] += 100
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_06_modify_expected_bank_credit(self) -> None:
        manifest = self.manifest()
        key = next(iter(manifest["match_facts"]["B"]))
        manifest["match_facts"]["B"][key]["bank_credit_paise"] -= 100
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_07_remove_one_expected_settlement_member(self) -> None:
        manifest = self.manifest()
        key = next(sid for sid, fact in manifest["match_facts"]["B"].items()
                   if len(fact["member_txn_ids"]) > 1)
        manifest["match_facts"]["B"][key]["member_txn_ids"].pop()
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_08_add_fake_settlement_member(self) -> None:
        manifest = self.manifest()
        key = next(iter(manifest["match_facts"]["B"]))
        manifest["match_facts"]["B"][key]["member_txn_ids"].append("pay_fabricated")
        self.write_manifest(manifest)
        self.assert_fails_closed()

    # -- expectation attacks ----------------------------------------------

    def test_09_change_expected_plane_a_pair(self) -> None:
        manifest = self.manifest()
        manifest["expectations"]["plane_a_matches"][0][1] = "order_999999"
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_10_change_expected_plane_b_pair(self) -> None:
        manifest = self.manifest()
        manifest["expectations"]["plane_b_matches"][0][1] = "bank_99999"
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_11_delete_one_expected_exception(self) -> None:
        manifest = self.manifest()
        self.assertTrue(manifest["expectations"]["exceptions"])
        manifest["expectations"]["exceptions"].pop()
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_12_add_fake_expected_exception(self) -> None:
        manifest = self.manifest()
        manifest["expectations"]["exceptions"].append(
            {"ids": ["order_000000"], "allowed_codes": ["MISSING_IN_BANK"], "tier": "T9"}
        )
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_13_duplicate_expected_exception_scope(self) -> None:
        manifest = self.manifest()
        manifest["expectations"]["exceptions"].append(
            dict(manifest["expectations"]["exceptions"][0])
        )
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_14_modify_allowed_exception_reason(self) -> None:
        manifest = self.manifest()
        manifest["expectations"]["exceptions"][0]["allowed_codes"] = ["INGEST_REJECT"]
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_15_widen_allowed_exception_reasons(self) -> None:
        manifest = self.manifest()
        for item in manifest["expectations"]["exceptions"]:
            item["allowed_codes"] = sorted(
                set(item["allowed_codes"]) | {"MISSING_IN_BANK", "INGEST_REJECT"}
            )
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_16_modify_tier_labels(self) -> None:
        manifest = self.manifest()
        key = next(iter(manifest["tier_labels"]))
        manifest["tier_labels"][key] = "T0"
        manifest["tier_labels"]["fabricated_entity"] = "T0"
        self.write_manifest(manifest)
        self.assert_fails_closed()

    # -- generation-input attacks -----------------------------------------

    def test_17_change_seed(self) -> None:
        meta = self.meta()
        meta["seed"] += 1
        self.write_meta(meta)
        self.assert_fails_closed()

    def test_18_change_profile(self) -> None:
        meta = self.meta()
        meta["profile"] = "clean"
        self.write_meta(meta)
        self.assert_fails_closed()

    def test_19_change_generator_version(self) -> None:
        meta = self.meta()
        meta["generator_version"] = "0.0.1-forged"
        self.write_meta(meta)
        message = self.assert_fails_closed()
        self.assertIn("generator_version", message)

    def test_20_change_record_count(self) -> None:
        meta = self.meta()
        meta["records"] = RECORDS * 2
        self.write_meta(meta)
        self.assert_fails_closed()

    def test_21_shrink_record_count_below_track_minimum(self) -> None:
        meta = self.meta()
        meta["records"] = 10
        self.write_meta(meta)
        message = self.assert_fails_closed()
        self.assertIn("at least 50", message)

    def test_22_delete_run_meta(self) -> None:
        (self.run / "run_meta.json").unlink()
        self.assert_fails_closed()

    def test_23_corrupt_run_meta(self) -> None:
        (self.run / "run_meta.json").write_text("{not json", encoding="utf-8")
        self.assert_fails_closed()

    def test_24_forge_config_hashes(self) -> None:
        meta = self.meta()
        meta["config_hashes"] = {"fees": "0" * 64, "timing": "0" * 64}
        self.write_meta(meta)
        self.assert_fails_closed()

    # -- source-file attacks ----------------------------------------------

    def test_25_modify_orders_csv(self) -> None:
        fields, rows = read_csv(self.run / "orders.csv")
        rows[0]["amount_paise"] = str(int(rows[0]["amount_paise"]) + 1)
        write_csv(self.run / "orders.csv", fields, rows)
        self.assert_fails_closed()

    def test_26_modify_gateway_csv(self) -> None:
        fields, rows = read_csv(self.run / "gateway_recon.csv")
        rows[0]["captured_at"] = "2026-01-01T00:00:00"
        write_csv(self.run / "gateway_recon.csv", fields, rows)
        self.assert_fails_closed()

    def test_27_modify_bank_csv(self) -> None:
        fields, rows = read_csv(self.run / "bank.csv")
        rows[0]["credit_paise"] = str(int(rows[0]["credit_paise"]) + 500)
        write_csv(self.run / "bank.csv", fields, rows)
        self.assert_fails_closed()

    def test_28_whitespace_only_change_is_still_tampering(self) -> None:
        path = self.run / "bank.csv"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assert_fails_closed()

    def test_29_change_manifest_input_hash(self) -> None:
        manifest = self.manifest()
        manifest["input_hashes"]["bank"] = "f" * 64
        self.write_manifest(manifest)
        self.assert_fails_closed()

    def test_30_substitute_a_valid_manifest_from_another_run(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            foreign = Path(other) / "foreign"
            generate_to_directory(RECORDS, SEED + 1, PROFILE, foreign)
            shutil.copy(foreign / "manifest.json", self.run / "manifest.json")
        self.assert_fails_closed()

    def test_31_delete_manifest_entirely(self) -> None:
        (self.run / "manifest.json").unlink()
        self.assert_fails_closed()

    # -- database attacks --------------------------------------------------

    def _sqlite(self) -> sqlite3.Connection:
        return sqlite3.connect(self.run / "m.db")

    def test_32_edit_a_stored_bank_credit(self) -> None:
        with self._sqlite() as conn:
            line_id = conn.execute("SELECT line_id FROM raw_bank ORDER BY line_id").fetchone()[0]
            conn.execute(
                "UPDATE raw_bank SET credit_paise=credit_paise+1 WHERE line_id=?", (line_id,)
            )
        self.assert_fails_closed()

    def test_33_edit_a_stored_order_amount(self) -> None:
        with self._sqlite() as conn:
            conn.execute(
                "UPDATE raw_orders SET amount_paise=amount_paise+7 "
                "WHERE order_id=(SELECT min(order_id) FROM raw_orders)"
            )
        self.assert_fails_closed()

    def test_34_delete_a_stored_source_row(self) -> None:
        with self._sqlite() as conn:
            conn.execute(
                "DELETE FROM raw_orders WHERE order_id=(SELECT max(order_id) FROM raw_orders)"
            )
        self.assert_fails_closed()

    def test_35_invent_a_stored_bank_line(self) -> None:
        with self._sqlite() as conn:
            run_id = conn.execute("SELECT run_id FROM runs").fetchone()[0]
            conn.execute(
                "INSERT INTO raw_bank VALUES('bank_forged','2026-08-03','2026-08-03',"
                "'NEFT-FORGED',123456,0,0,'FORGED','bank:forged',?)", (run_id,)
            )
        self.assert_fails_closed()

    def test_36_forge_the_stored_input_hashes(self) -> None:
        with self._sqlite() as conn:
            conn.execute("UPDATE runs SET bank_sha256=?", ("e" * 64,))
        self.assert_fails_closed()

    # -- the historical exploit, end to end --------------------------------

    def test_37_historical_exploit_falsified_money_plus_deleted_facts(self) -> None:
        """Falsify real money, re-hash the manifest, then delete ``match_facts``.

        This is the exact chain that converted a failing run into a published
        100% PASS before the truth boundary was rebuilt. Every step is applied,
        including recomputing the manifest hashes so the old hash gate would be
        satisfied.
        """
        import hashlib

        manifest = self.manifest()
        expected_b = {a: b for a, b in manifest["expectations"]["plane_b_matches"]}
        txn_fields, txns = read_csv(self.run / "gateway_recon.csv")
        by_settlement: dict[str, list[dict[str, str]]] = {}
        for row in txns:
            by_settlement.setdefault(row["settlement_id"], []).append(row)
        settlement_id, adjustment = next(
            (sid, rows[0]) for sid, line in expected_b.items()
            if (rows := [r for r in by_settlement.get(sid, ()) if r["type"] == "adjustment"])
        )
        bank_line_id = expected_b[settlement_id]
        delta = -100_000  # a full ₹1,000.00 silently removed from the batch

        for row in txns:
            if row["entity_id"] == adjustment["entity_id"]:
                row["gross_paise"] = str(int(row["gross_paise"]) + delta)
                row["net_paise"] = str(int(row["net_paise"]) + delta)
        write_csv(self.run / "gateway_recon.csv", txn_fields, txns)

        bank_fields, lines = read_csv(self.run / "bank.csv")
        for row in lines:
            if row["line_id"] == bank_line_id:
                row["credit_paise"] = str(int(row["credit_paise"]) + delta)
        write_csv(self.run / "bank.csv", bank_fields, lines)

        def sha(name: str) -> str:
            return hashlib.sha256((self.run / name).read_bytes()).hexdigest()

        manifest["input_hashes"] = {
            "orders": sha("orders.csv"),
            "gateway_recon": sha("gateway_recon.csv"),
            "bank": sha("bank.csv"),
        }
        manifest.pop("match_facts")
        self.write_manifest(manifest)

        run_pipeline(self.run, self.run / "m.db", "mock")
        self.assert_fails_closed()

    def test_38_falsified_money_alone_is_caught_even_with_facts_intact(self) -> None:
        fields, rows = read_csv(self.run / "bank.csv")
        rows[0]["credit_paise"] = str(int(rows[0]["credit_paise"]) + 1)
        write_csv(self.run / "bank.csv", fields, rows)
        run_pipeline(self.run, self.run / "m.db", "mock")
        self.assert_fails_closed()

    def test_39_rejected_evidence_is_written_under_a_distinct_name(self) -> None:
        manifest = self.manifest()
        manifest.pop("match_facts")
        self.write_manifest(manifest)
        self.assert_fails_closed()
        rejected = self.run / "functional_metrics.rejected.json"
        self.assertTrue(rejected.is_file())
        payload = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertEqual(payload["gate"]["status"], "FAIL")
        self.assertTrue(payload["gate"]["failures"])


if __name__ == "__main__":
    unittest.main()
