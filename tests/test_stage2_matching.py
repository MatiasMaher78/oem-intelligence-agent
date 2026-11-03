"""Delivery 02: independent match axes, source errors and coverage regressions."""
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from oem_agent.db import read_only_connection
from oem_agent.harness import PolicyViolation
from oem_agent.stage2 import execute_stage2_selection
from oem_agent.stage2_db import list_stage2_offers, seed_stage2
from oem_agent.stage2_matching import compare_offer, execute_stage2_matching


IDENTITY = {"oem": "DEMO-S2-ALT-0002", "part_name": "Alternador", "brand": "MarcaDemo-A",
            "model": "ModeloDemo-A1", "version": "VersionDemo-Base"}


class OfferComparisonTests(unittest.TestCase):
    def test_exact_oem_and_fully_declared_compatibility(self):
        result = compare_offer(IDENTITY, dict(IDENTITY))
        self.assertEqual((result["oem_match"], result["compatibility"]), ("exact", "confirmed"))
        self.assertTrue(result["confirmed_match"])
        self.assertEqual(result["incompatible_fields"], [])
        self.assertEqual(result["unknown_fields"], [])

    def test_oem_is_literal_and_never_prefix_or_case_matching(self):
        for oem in ("DEMO-S2-ALT-00020", "demo-s2-alt-0002", "DEMO-S2-ALT-002", " DEMO-S2-ALT-0002"):
            with self.subTest(oem=oem):
                result = compare_offer(IDENTITY, {**IDENTITY, "oem": oem})
                self.assertEqual(result["oem_match"], "different")
                self.assertEqual(result["compatibility"], "confirmed")
                self.assertFalse(result["confirmed_match"])

    def test_missing_oem_is_unknown_on_either_side(self):
        for value in (None, "", "  ", 123):
            for inventory, offer in (({**IDENTITY, "oem": value}, IDENTITY),
                                     (IDENTITY, {**IDENTITY, "oem": value})):
                with self.subTest(value=value, inventory=inventory):
                    result = compare_offer(inventory, offer)
                    self.assertEqual(result["oem_match"], "unknown")
                    self.assertEqual(result["compatibility"], "confirmed")
                    self.assertFalse(result["confirmed_match"])

    def test_known_conflict_in_each_compatibility_field(self):
        for field in ("part_name", "brand", "model", "version"):
            with self.subTest(field=field):
                result = compare_offer(IDENTITY, {**IDENTITY, field: "OtroDemo"})
                self.assertEqual(result["oem_match"], "exact")
                self.assertEqual(result["compatibility"], "incompatible")
                self.assertEqual(result["incompatible_fields"], [field])
                self.assertFalse(result["confirmed_match"])

    def test_missing_compatibility_data_is_unknown(self):
        for field in ("part_name", "brand", "model", "version"):
            for value in (None, "", " "):
                with self.subTest(field=field, value=value):
                    result = compare_offer(IDENTITY, {**IDENTITY, field: value})
                    self.assertEqual(result["oem_match"], "exact")
                    self.assertEqual(result["compatibility"], "unknown")
                    self.assertEqual(result["unknown_fields"], [field])
                    self.assertFalse(result["confirmed_match"])

    def test_known_conflict_takes_precedence_over_missing_fields(self):
        result = compare_offer(IDENTITY, {**IDENTITY, "brand": "MarcaDemo-Z", "version": None})
        self.assertEqual(result["compatibility"], "incompatible")
        self.assertEqual(result["incompatible_fields"], ["brand"])
        self.assertEqual(result["unknown_fields"], ["version"])

    def test_axes_are_independent_and_inputs_are_not_modified(self):
        inventory = dict(IDENTITY)
        offer = {**IDENTITY, "oem": "OTRO-DEMO", "version": None}
        before = copy.deepcopy((inventory, offer))
        result = compare_offer(inventory, offer)
        self.assertEqual((result["oem_match"], result["compatibility"]), ("different", "unknown"))
        self.assertEqual((inventory, offer), before)
        self.assertEqual(result["field_evidence"]["version"]["comparison"], "unknown")


class Stage2MatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "stage2.sqlite"
        seed_stage2(self.path)

    def add_offer(self, offer_id, **changes):
        row = {"marketplace_id": "DEMO-MARKET-A", "offer_id": offer_id, **IDENTITY,
               "available_units": 1, "price_cents": 10000, "observed_on": "2026-11-03"}
        row.update(changes)
        fields = ("marketplace_id", "offer_id", "oem", "part_name", "brand", "model", "version",
                  "available_units", "price_cents", "observed_on")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO stage2_market_offers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         tuple(row[field] for field in fields))

    def case(self, result, inventory_id):
        return next(row for row in result["results"] if row["inventory"]["id"] == inventory_id)

    def market(self, case, identifier):
        return next(row for row in case["marketplaces"] if row["marketplace_id"] == identifier)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-m", "oem_agent.stage2", "match", "--db", str(self.path), *arguments],
                              cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)

    def test_sql_retrieves_exact_oem_or_name_in_each_source(self):
        with closing(read_only_connection(self.path)) as conn:
            first = list_stage2_offers(conn, marketplace_id="DEMO-MARKET-A",
                                       oem=IDENTITY["oem"], part_name=IDENTITY["part_name"])
            second = list_stage2_offers(conn, marketplace_id="DEMO-MARKET-B",
                                        oem=IDENTITY["oem"], part_name=IDENTITY["part_name"])
        self.assertEqual([row["offer_id"] for row in first], ["001", "003"])
        self.assertEqual([row["offer_id"] for row in second], ["001"])
        self.assertTrue(all(row["marketplace_id"] == "DEMO-MARKET-A" for row in first))
        self.assertEqual(second[0]["price_cents"], 11900)

    def test_sql_is_parameterized_and_rejects_unknown_sources(self):
        with closing(read_only_connection(self.path)) as conn:
            self.assertEqual(list_stage2_offers(conn, marketplace_id="DEMO-MARKET-A",
                                                oem="' OR 1=1 --", part_name="' OR 1=1 --"), [])
            with self.assertRaises(ValueError):
                list_stage2_offers(conn, marketplace_id="DEMO-MARKET-A' OR 1=1 --",
                                   oem=IDENTITY["oem"], part_name=IDENTITY["part_name"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stage2_inventory").fetchone()[0], 32)

    def test_sql_retains_exact_oem_even_with_conflicting_piece_name(self):
        self.add_offer("TEST-NAME", part_name="Otro tipo de pieza")
        case = self.case(execute_stage2_matching(self.path), 2)
        comparisons = self.market(case, "DEMO-MARKET-A")["comparisons"]
        comparison = next(row for row in comparisons if row["offer"]["offer_id"] == "TEST-NAME")
        self.assertEqual((comparison["oem_match"], comparison["compatibility"]), ("exact", "incompatible"))

    def test_missing_offer_fields_produce_unknown_classifications(self):
        self.add_offer("TEST-OEM", oem="")
        self.add_offer("TEST-VERSION", version="")
        case = self.case(execute_stage2_matching(self.path), 2)
        comparisons = {row["offer"]["offer_id"]: row for row in self.market(case, "DEMO-MARKET-A")["comparisons"]}
        self.assertEqual(comparisons["TEST-OEM"]["oem_match"], "unknown")
        self.assertEqual(comparisons["TEST-OEM"]["compatibility"], "confirmed")
        self.assertEqual(comparisons["TEST-VERSION"]["oem_match"], "exact")
        self.assertEqual(comparisons["TEST-VERSION"]["compatibility"], "unknown")
        self.assertFalse(comparisons["TEST-OEM"]["confirmed_match"])
        self.assertFalse(comparisons["TEST-VERSION"]["confirmed_match"])

    def test_all_27_pieces_and_55_traced_read_attempts_are_retained(self):
        selected = execute_stage2_selection(self.path)
        result = execute_stage2_matching(self.path)
        self.assertEqual([row["inventory"] for row in result["results"]], selected["candidates"])
        self.assertEqual(result["total_candidates"], 27)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["trace"]), 55)
        self.assertEqual([event["step"] for event in result["trace"]], list(range(1, 56)))
        for event in result["trace"]:
            self.assertEqual(event["status"], "ok")
            self.assertIn("sql", event)
            self.assertIn("parameters", event)
            self.assertNotIn("conn", event["args"])
        for row in result["results"]:
            self.assertEqual([market["marketplace_id"] for market in row["marketplaces"]],
                             ["DEMO-MARKET-A", "DEMO-MARKET-B"])
            self.assertEqual(row["catalog_status"], "not_assessed")

    def test_empty_marketplaces_do_not_remove_pieces_or_imply_bad_cataloguing(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DELETE FROM stage2_market_offers")
        result = execute_stage2_matching(self.path)
        self.assertEqual(len(result["results"]), 27)
        self.assertTrue(result["complete"])
        for row in result["results"]:
            self.assertEqual(row["catalog_status"], "not_assessed")
            self.assertFalse(row["has_confirmed_match"])
            for market in row["marketplaces"]:
                self.assertEqual(market["status"], "no_results")
                self.assertEqual(market["offers_count"], 0)
                self.assertEqual(market["comparisons"], [])

    def test_prefix_trap_is_different_even_with_confirmed_compatibility(self):
        case = self.case(execute_stage2_matching(self.path), 2)
        trap = next(row for row in self.market(case, "DEMO-MARKET-A")["comparisons"]
                    if row["offer"]["offer_id"] == "003")
        self.assertEqual((trap["oem_match"], trap["compatibility"]), ("different", "confirmed"))
        self.assertFalse(trap["confirmed_match"])

    def test_matching_preserves_zero_availability_without_price_decisions(self):
        case = self.case(execute_stage2_matching(self.path), 7)
        comparison = self.market(case, "DEMO-MARKET-B")["comparisons"][0]
        self.assertEqual(comparison["offer"]["available_units"], 0)
        self.assertTrue(comparison["confirmed_match"])
        self.assertNotIn("recommended_price", comparison)

    def test_one_source_failure_preserves_all_pieces_and_other_source_evidence(self):
        def fail_first_source(conn, *, marketplace_id, oem, part_name):
            if marketplace_id == "DEMO-MARKET-A":
                raise sqlite3.OperationalError("synthetic source A failure")
            return list_stage2_offers(conn, marketplace_id=marketplace_id, oem=oem, part_name=part_name)
        with patch("oem_agent.stage2_matching.list_stage2_offers", side_effect=fail_first_source):
            result = execute_stage2_matching(self.path)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["results"]), 27)
        self.assertEqual(len(result["trace"]), 55)
        self.assertEqual(sum(event["status"] == "error" for event in result["trace"]), 27)
        for row in result["results"]:
            failed = self.market(row, "DEMO-MARKET-A")
            self.assertEqual(failed["status"], "error")
            self.assertIsNone(failed["offers_count"])
            self.assertIsNone(failed["confirmed_matches_count"])
            self.assertEqual(row["catalog_status"], "not_assessed")
        self.assertTrue(self.case(result, 2)["has_confirmed_match"])
        self.assertIsNone(self.case(result, 10)["has_confirmed_match"])

    def test_inventory_read_failure_is_unknown_coverage_with_error_trace(self):
        with patch("oem_agent.stage2_matching.list_stage2_candidates",
                   side_effect=sqlite3.OperationalError("synthetic inventory failure")):
            result = execute_stage2_matching(self.path)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["total_candidates"])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["trace"][0]["status"], "error")
        self.assertIsNone(result["trace"][0]["result_count"])

    def test_unexpected_errors_propagate_and_close_the_connection(self):
        conn = read_only_connection(self.path)
        self.addCleanup(conn.close)
        with patch("oem_agent.stage2_matching.read_only_connection", return_value=conn), \
                patch("oem_agent.stage2_matching.list_stage2_offers", side_effect=RuntimeError("unexpected bug")):
            with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                execute_stage2_matching(self.path)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_policy_violations_are_not_converted_into_empty_or_partial_results(self):
        with patch("oem_agent.stage2_matching.Harness.call", side_effect=PolicyViolation("blocked")):
            with self.assertRaises(PolicyViolation):
                execute_stage2_matching(self.path)

    def test_matching_is_reproducible_and_keeps_selection_and_database_unchanged(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        selection = execute_stage2_selection(self.path)
        result = execute_stage2_matching(self.path)
        self.assertEqual(result, execute_stage2_matching(self.path))
        self.assertEqual(execute_stage2_selection(self.path), selection)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        other_path = Path(self.tmp.name) / "second.sqlite"
        seed_stage2(other_path)
        other = execute_stage2_matching(other_path)
        other["source"]["database"] = result["source"]["database"]
        self.assertEqual(other, result)
        with closing(read_only_connection(self.path)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE stage2_inventory SET stock_qty=99 WHERE id=2")

    def test_empty_inventory_selection_only_executes_one_read(self):
        result = execute_stage2_matching(self.path, min_days=100, max_days=110)
        self.assertTrue(result["complete"])
        self.assertEqual(result["total_candidates"], 0)
        self.assertEqual(result["results"], [])
        self.assertEqual(len(result["trace"]), 1)

    def test_cli_success_partial_and_fatal_read_statuses(self):
        success = self.cli()
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stderr, "")
        self.assertEqual(json.loads(success.stdout)["total_candidates"], 27)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DROP TABLE stage2_market_offers")
        partial = self.cli()
        self.assertEqual(partial.returncode, 1)
        payload = json.loads(partial.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(len(payload["results"]), 27)
        self.assertTrue(all(event["status"] == "error" for event in payload["trace"][1:]))
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DROP TABLE stage2_inventory")
        fatal = self.cli()
        self.assertEqual(fatal.returncode, 1)
        self.assertEqual(json.loads(fatal.stdout)["status"], "error")
        self.assertIsNone(json.loads(fatal.stdout)["total_candidates"])

    def test_cli_rejects_invalid_ranges_and_does_not_create_missing_databases(self):
        invalid = self.cli("--min-days", "91", "--max-days", "60")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        missing = Path(self.tmp.name) / "missing.sqlite"
        result = self.cli("--db", str(missing))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
