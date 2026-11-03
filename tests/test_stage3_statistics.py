"""Delivery 03: exact percentiles, exclusions, complete audit and regression."""
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from oem_agent.db import read_only_connection
from oem_agent.stage2 import execute_stage2_selection
from oem_agent.stage2_db import seed_stage2
from oem_agent.stage2_matching import execute_stage2_matching
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import StatisticsConfig, analyze_matching, execute_stage2_statistics, linear_percentile


class PercentileTests(unittest.TestCase):
    def test_documented_linear_interpolation_and_endpoints(self):
        values = [10000, 1000, 9000, 2000, 8000, 3000, 7000, 4000, 6000, 5000]
        self.assertEqual(linear_percentile(values, 10), Fraction(1900))
        self.assertEqual(linear_percentile(values, 90), Fraction(9100))
        self.assertEqual(linear_percentile(values, 0), Fraction(1000))
        self.assertEqual(linear_percentile(values, 100), Fraction(10000))
        self.assertEqual(linear_percentile([123], 50), Fraction(123))

    def test_fractional_cents_are_not_rounded_before_filtering(self):
        self.assertEqual(linear_percentile([101, 102], "25"), Fraction(405, 4))
        self.assertEqual(linear_percentile([0, 1], Decimal("12.5")), Fraction(1, 8))

    def test_invalid_configuration_and_prices(self):
        for lower, upper, minimum in ((-1, 90, 10), (10, 101, 10), (90, 10, 10), (10, 10, 10),
                                      (True, 90, 10), ("NaN", 90, 10), (10, "Infinity", 10),
                                      ("bad", 90, 10), (10, 90, 0), (10, 90, True), (10, 90, 1.5)):
            with self.subTest(lower=lower, upper=upper, minimum=minimum), self.assertRaises(ValueError):
                StatisticsConfig(lower, upper, minimum)
        for prices in ([], [-1], [1.5], [True]):
            with self.subTest(prices=prices), self.assertRaises(ValueError):
                linear_percentile(prices, 10)


class StatisticsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "statistics.sqlite"
        seed_statistics_demo(self.path)

    def case(self, result, identifier=10):
        return next(row for row in result["results"] if row["inventory"]["id"] == identifier)

    def matching(self):
        return execute_stage2_matching(self.path)

    def exact_pair(self, matching):
        return next(pair for market in self.case(matching)["marketplaces"] for pair in market["comparisons"]
                    if pair["offer"]["offer_id"] == "STATS-01")

    def test_ten_offer_scenario_observed_and_filtered_prices_and_units(self):
        result = execute_stage2_statistics(self.path)
        stats = self.case(result)["statistics"]
        self.assertEqual(stats["evidence_status"], "sufficient_sample_demo")
        self.assertEqual((stats["observed_offers_count"], stats["observed_units"]), (10, 55))
        self.assertEqual((stats["observed_min_price_cents"], stats["observed_max_price_cents"]), (1000, 10000))
        self.assertEqual(stats["lower_cutoff_cents"], {"numerator": 1900, "denominator": 1})
        self.assertEqual(stats["upper_cutoff_cents"], {"numerator": 9100, "denominator": 1})
        self.assertEqual((stats["filtered_offers_count"], stats["filtered_units"]), (8, 44))
        self.assertEqual((stats["filtered_min_price_cents"], stats["filtered_max_price_cents"]), (2000, 9000))
        self.assertEqual(len(result["results"]), 27)
        self.assertEqual(len(result["trace"]), 56)
        self.assertEqual(result["trace"][-1]["scope"], "all_source_offers")
        self.assertEqual(len(result["statistics_trace"]), 27)

    def test_old_fixtures_are_insufficient_and_matching_is_unchanged(self):
        original = Path(self.tmp.name) / "original.sqlite"
        seed_stage2(original)
        before = execute_stage2_matching(original)
        result = execute_stage2_statistics(original)
        stats = self.case(result, 2)["statistics"]
        self.assertEqual(stats["evidence_status"], "insufficient_sample")
        self.assertEqual((stats["observed_offers_count"], stats["observed_units"]), (2, 4))
        self.assertEqual((stats["observed_min_price_cents"], stats["observed_max_price_cents"]), (11900, 12500))
        for key in ("lower_cutoff_cents", "upper_cutoff_cents", "filtered_offers_count", "filtered_units",
                    "filtered_min_price_cents", "filtered_max_price_cents"):
            self.assertIsNone(stats[key])
        self.assertFalse(stats["percentile_filter_applied"])
        self.assertEqual(before, execute_stage2_matching(original))
        self.assertEqual([row["inventory"] for row in result["results"]], execute_stage2_selection(original)["candidates"])

    def test_only_exact_confirmed_available_offers_are_used_and_all_others_are_audited(self):
        result = execute_stage2_statistics(self.path)
        case = self.case(result, 2)
        trap = next(row for row in case["offer_audit"] if row["offer"]["offer_id"] == "003")
        self.assertFalse(trap["observed_included"])
        self.assertIn("oem_not_exact", trap["exclusion_reasons"])
        self.assertEqual(trap["offer"]["price_cents"], 900)
        zero = self.case(result, 7)
        self.assertEqual(zero["statistics"]["evidence_status"], "no_comparable_offers")
        self.assertEqual(zero["statistics"]["observed_units"], 0)
        self.assertIsNone(zero["statistics"]["observed_min_price_cents"])
        self.assertIn("no_available_units", zero["offer_audit"][0]["exclusion_reasons"])
        matching = self.matching()
        pair = self.exact_pair(matching)
        pair["oem_match"] = "different"
        pair["confirmed_match"] = True
        self.assertEqual(self.case(analyze_matching(matching))["statistics"]["observed_offers_count"], 9)

    def test_outliers_are_retained_without_changing_their_original_prices(self):
        case = self.case(execute_stage2_statistics(self.path))
        for identifier, price in (("STATS-01", 1000), ("STATS-10", 10000)):
            row = next(row for row in case["offer_audit"] if row["offer"]["offer_id"] == identifier)
            self.assertTrue(row["observed_included"])
            self.assertFalse(row["filtered_included"])
            self.assertEqual(row["filter_reason"], "outside_percentile_band")
            self.assertEqual(row["offer"]["price_cents"], price)

    def test_configurable_percentiles_and_minimum_sample(self):
        all_prices = self.case(execute_stage2_statistics(self.path, config=StatisticsConfig(0, 100, 10)))["statistics"]
        self.assertEqual((all_prices["filtered_offers_count"], all_prices["filtered_units"]), (10, 55))
        self.assertEqual((all_prices["filtered_min_price_cents"], all_prices["filtered_max_price_cents"]), (1000, 10000))
        insufficient = self.case(execute_stage2_statistics(self.path, config=StatisticsConfig(10, 90, 11)))["statistics"]
        self.assertEqual(insufficient["evidence_status"], "insufficient_sample")
        self.assertIsNone(insufficient["filtered_min_price_cents"])

    def test_identical_prices_keep_inclusive_band_boundaries(self):
        matching = self.matching()
        for market in self.case(matching)["marketplaces"]:
            for pair in market["comparisons"]:
                if pair["oem_match"] == "exact":
                    pair["offer"]["price_cents"] = 1234
        stats = self.case(analyze_matching(matching))["statistics"]
        self.assertEqual((stats["filtered_offers_count"], stats["filtered_units"]), (10, 55))
        self.assertEqual((stats["filtered_min_price_cents"], stats["filtered_max_price_cents"]), (1234, 1234))

    def test_identifiable_duplicates_do_not_inflate_sample_or_units(self):
        matching = self.matching()
        pair = copy.deepcopy(self.exact_pair(matching))
        self.case(matching)["marketplaces"][0]["comparisons"].append(pair)
        case = self.case(analyze_matching(matching))
        self.assertEqual((case["statistics"]["observed_offers_count"], case["statistics"]["observed_units"]), (10, 55))
        duplicate = next(row for row in case["offer_audit"] if "duplicate_identity" in row["exclusion_reasons"])
        self.assertIsNotNone(duplicate["duplicate_of"])
        self.assertFalse(duplicate["observed_included"])

    def test_conflicting_duplicates_are_all_excluded_instead_of_choosing_a_price(self):
        matching = self.matching()
        pair = copy.deepcopy(self.exact_pair(matching))
        pair["offer"]["price_cents"] = 999999
        self.case(matching)["marketplaces"][0]["comparisons"].append(pair)
        case = self.case(analyze_matching(matching))
        self.assertEqual(case["statistics"]["observed_offers_count"], 9)
        self.assertEqual(case["statistics"]["evidence_status"], "insufficient_sample")
        conflicts = [row for row in case["offer_audit"] if "conflicting_duplicate_identity" in row["exclusion_reasons"]]
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(all(not row["observed_included"] for row in conflicts))

    def test_same_offer_id_in_independent_sources_is_not_a_duplicate(self):
        stats = self.case(execute_stage2_statistics(self.path, config=StatisticsConfig(0, 100, 2)), 2)["statistics"]
        self.assertEqual((stats["observed_offers_count"], stats["observed_units"]), (2, 4))
        self.assertEqual(stats["filtered_offers_count"], 2)

    def test_invalid_price_units_and_identity_are_excluded_with_reasons(self):
        changes = (("price_cents", None, "invalid_price"), ("price_cents", 0, "invalid_price"),
                   ("price_cents", True, "invalid_price"), ("available_units", None, "invalid_units"),
                   ("available_units", -1, "invalid_units"), ("offer_id", "", "invalid_offer_identity"))
        for field, value, reason in changes:
            with self.subTest(field=field, value=value):
                matching = self.matching()
                self.exact_pair(matching)["offer"][field] = value
                case = self.case(analyze_matching(matching))
                self.assertEqual(case["statistics"]["observed_offers_count"], 9)
                self.assertTrue(any(reason in row["exclusion_reasons"] for row in case["offer_audit"]))

    def test_empty_filtered_sample_has_zero_counts_and_absent_extrema(self):
        matching = self.matching()
        pairs = [pair for market in self.case(matching)["marketplaces"] for pair in market["comparisons"]
                 if pair["offer"]["offer_id"] in ("STATS-01", "STATS-02")]
        case = self.case(matching)
        case["marketplaces"][0]["comparisons"] = pairs
        case["marketplaces"][1]["comparisons"] = []
        stats = self.case(analyze_matching(matching, StatisticsConfig(40, 60, 2)))["statistics"]
        self.assertEqual(stats["evidence_status"], "empty_filtered_sample")
        self.assertEqual((stats["filtered_offers_count"], stats["filtered_units"]), (0, 0))
        self.assertIsNone(stats["filtered_min_price_cents"])
        self.assertTrue(stats["percentile_filter_applied"])

    def test_complete_source_catalog_audits_even_unrelated_offers(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""INSERT INTO stage2_market_offers VALUES
                ('DEMO-MARKET-A', 'UNRELATED', 'DEMO-UNRELATED', 'Pieza distinta', 'MarcaDemo-X',
                 'ModeloDemo-X', 'VersionDemo-X', 2, 7777, '2026-11-03')""")
        result = execute_stage2_statistics(self.path)
        self.assertEqual(len(result["source_offers"]), 16)
        self.assertEqual(len(result["unassociated_offer_audit"]), 1)
        row = result["unassociated_offer_audit"][0]
        self.assertEqual(row["offer"]["offer_id"], "UNRELATED")
        self.assertFalse(row["observed_included"])
        self.assertIn("not_retrieved_for_selected_inventory", row["exclusion_reasons"])

    def test_source_errors_prevent_filtered_ranges_even_with_enough_known_offers(self):
        matching = self.matching()
        self.case(matching)["matching_complete"] = False
        stats = self.case(analyze_matching(matching))["statistics"]
        self.assertEqual(stats["observed_offers_count"], 10)
        self.assertEqual(stats["evidence_status"], "source_error")
        self.assertIsNone(stats["filtered_min_price_cents"])
        with patch("oem_agent.stage3_statistics._list_source_offers", side_effect=sqlite3.OperationalError("audit failed")):
            result = execute_stage2_statistics(self.path)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["audit_complete"])
        self.assertEqual(result["trace"][-1]["status"], "error")
        self.assertEqual(self.case(result)["statistics"]["evidence_status"], "source_error")

    def test_statistics_do_not_mutate_inputs_or_database_and_are_reproducible(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        matching = self.matching()
        copied = copy.deepcopy(matching)
        analyze_matching(matching)
        self.assertEqual(matching, copied)
        result = execute_stage2_statistics(self.path)
        self.assertEqual(result, execute_stage2_statistics(self.path))
        other = Path(self.tmp.name) / "second.sqlite"
        seed_statistics_demo(other)
        reproduced = execute_stage2_statistics(other)
        reproduced["source"]["database"] = result["source"]["database"]
        self.assertEqual(reproduced, result)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_new_scenario_preserves_old_seed_selection_and_refuses_overwrite(self):
        original = Path(self.tmp.name) / "original.sqlite"
        seed_stage2(original)
        self.assertEqual(execute_stage2_selection(original)["candidates"], execute_stage2_selection(self.path)["candidates"])
        with closing(read_only_connection(original)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stage2_market_offers").fetchone()[0], 5)
        before = original.read_bytes()
        with self.assertRaises(FileExistsError):
            seed_statistics_demo(original)
        self.assertEqual(original.read_bytes(), before)

    def test_cli_stats_configuration_and_isolated_seed_command(self):
        command = [sys.executable, "-m", "oem_agent.stage2"]
        cwd = Path(__file__).resolve().parents[1]
        path = Path(self.tmp.name) / "cli.sqlite"
        seeded = subprocess.run([*command, "seed-stats", "--db", str(path)], cwd=cwd, text=True, capture_output=True)
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        stats = subprocess.run([*command, "stats", "--db", str(path), "--p-low", "0", "--p-high", "100"],
                               cwd=cwd, text=True, capture_output=True)
        self.assertEqual(stats.returncode, 0, stats.stderr)
        self.assertEqual(self.case(json.loads(stats.stdout))["statistics"]["filtered_offers_count"], 10)
        invalid = subprocess.run([*command, "stats", "--db", str(path), "--min-sample", "0"],
                                 cwd=cwd, text=True, capture_output=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")


if __name__ == "__main__":
    unittest.main()
