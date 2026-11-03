"""Delivery 01: range boundaries, complete selection and isolated fixtures."""
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

from oem_agent.db import read_only_connection, seed_demo
from oem_agent.harness import execute_readonly_workflow, write_outputs
from oem_agent.stage2 import execute_stage2_selection
from oem_agent.stage2_db import list_stage2_candidates, seed_stage2


class Stage2SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "stage2.sqlite"
        seed_stage2(self.path)

    def candidates(self, **kwargs):
        with closing(read_only_connection(self.path)) as conn:
            return list_stage2_candidates(conn, **kwargs)

    def test_inclusive_boundaries_and_zero_stock(self):
        rows = self.candidates()
        ids = {row["id"] for row in rows}
        self.assertTrue({2, 3, 7}.issubset(ids))  # 60, 90 and 75 days, with stock.
        self.assertTrue({1, 4, 5, 6, 8}.isdisjoint(ids))  # 59/91 days or no stock.
        self.assertTrue(all(row["stock_qty"] > 0 and 60 <= row["days_in_stock"] <= 90 for row in rows))

    def test_all_27_candidates_are_selected_in_stable_order(self):
        expected = [2, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
                    7, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 3]
        self.assertEqual([row["id"] for row in self.candidates()], expected)
        self.assertEqual(self.candidates(), self.candidates())

    def test_single_day_and_custom_intervals(self):
        self.assertEqual([row["id"] for row in self.candidates(min_days=60, max_days=60)], [2])
        self.assertEqual([row["id"] for row in self.candidates(min_days=90, max_days=90)], [3])
        self.assertEqual(len(self.candidates(min_days=59, max_days=91)), 29)
        self.assertEqual(self.candidates(min_days=100, max_days=110), [])

    def test_invalid_day_bounds_are_rejected(self):
        for minimum, maximum in ((-1, 90), (91, 60), ("60", 90), (60, 90.5),
                                 (True, 90), (60, False), (None, 90), ("60 OR 1=1", 90)):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(ValueError):
                    self.candidates(min_days=minimum, max_days=maximum)

    def test_required_identity_fields_are_preserved(self):
        row = next(row for row in self.candidates() if row["id"] == 2)
        self.assertEqual(row, {"id": 2, "oem": "DEMO-S2-ALT-0002", "part_name": "Alternador",
                               "brand": "MarcaDemo-A", "model": "ModeloDemo-A1",
                               "version": "VersionDemo-Base", "stock_qty": 2, "days_in_stock": 60})

    def test_two_marketplaces_have_independent_offer_ids_and_data(self):
        with closing(read_only_connection(self.path)) as conn:
            markets = [tuple(row) for row in conn.execute("SELECT id FROM stage2_marketplaces ORDER BY id")]
            counts = [tuple(row) for row in conn.execute("""
                SELECT marketplace_id, COUNT(*) FROM stage2_market_offers
                GROUP BY marketplace_id ORDER BY marketplace_id
            """)]
            shared_ids = [tuple(row) for row in conn.execute("""
                SELECT marketplace_id, available_units, price_cents FROM stage2_market_offers
                WHERE offer_id = '001' ORDER BY marketplace_id
            """)]
        self.assertEqual(markets, [("DEMO-MARKET-A",), ("DEMO-MARKET-B",)])
        self.assertEqual(counts, [("DEMO-MARKET-A", 3), ("DEMO-MARKET-B", 2)])
        self.assertEqual(shared_ids, [("DEMO-MARKET-A", 3, 12500), ("DEMO-MARKET-B", 1, 11900)])

    def test_fresh_seeds_have_identical_schema_data_and_selection(self):
        other_path = Path(self.tmp.name) / "second.sqlite"
        seed_stage2(other_path)
        with closing(read_only_connection(self.path)) as first, \
                closing(read_only_connection(other_path)) as second:
            self.assertEqual(list(first.iterdump()), list(second.iterdump()))
            self.assertEqual(list_stage2_candidates(first), list_stage2_candidates(second))

    def test_offer_coverage_does_not_change_inventory_eligibility(self):
        before = self.candidates()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DELETE FROM stage2_market_offers")
        self.assertEqual(self.candidates(), before)
        self.assertEqual(len(before), 27)

    def test_seed_refuses_overwrite_and_preserves_existing_bytes(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            seed_stage2(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        other_path = Path(self.tmp.name) / "not-a-database.sqlite"
        other_path.write_bytes(b"existing file must survive")
        with self.assertRaises(FileExistsError):
            seed_stage2(other_path)
        self.assertEqual(other_path.read_bytes(), b"existing file must survive")

    def test_schema_rejects_invalid_inventory_and_unknown_marketplace(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            for column, value in (("stock_qty", -1), ("stock_qty", 1.5), ("days_in_stock", -1),
                                  ("brand", None), ("oem", "")):
                with self.subTest(column=column, value=value), conn:
                    with self.assertRaises(sqlite3.IntegrityError):
                        row = [1000, "DEMO-S2-INVALID", "Sensor", "MarcaDemo-X", "ModeloDemo-X", "Base", 1, 60]
                        index = {"oem": 1, "brand": 3, "stock_qty": 6, "days_in_stock": 7}[column]
                        row[index] = value
                        conn.execute("INSERT INTO stage2_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
            with conn, self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO stage2_market_offers
                    SELECT 'UNKNOWN', 'bad', oem, part_name, brand, model, version,
                           available_units, price_cents, observed_on FROM stage2_market_offers LIMIT 1
                """)

    def test_selection_is_read_only_and_includes_source_and_trace(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = execute_stage2_selection(self.path)
        self.assertTrue(result["demo_only"])
        self.assertEqual(result["data_origin"], "100% synthetic")
        self.assertEqual(result["source"], {"database": str(self.path.resolve()), "table": "stage2_inventory"})
        self.assertEqual(result["total_candidates"], 27)
        self.assertEqual(len(result["trace"]), 1)
        event = result["trace"][0]
        self.assertEqual(event["tool"], "list_candidates")
        self.assertEqual(event["result_count"], 27)
        self.assertEqual(event["parameters"], [60, 90])
        self.assertNotIn("conn", event["args"])
        self.assertEqual(result, execute_stage2_selection(self.path))
        with closing(read_only_connection(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE stage2_inventory SET stock_qty=99 WHERE id=2")
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_empty_selection_still_has_successful_query_trace(self):
        result = execute_stage2_selection(self.path, min_days=100, max_days=110)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["total_candidates"], 0)
        self.assertEqual(result["trace"][0]["result_count"], 0)

    def test_seed_closes_connection_even_if_inserts_fail(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        with patch("oem_agent.stage2_db.sqlite3.connect", return_value=conn), \
                patch("oem_agent.stage2_db.MARKET_B_OFFERS", [(1,)]):
            with self.assertRaises(sqlite3.ProgrammingError):
                seed_stage2(Path(self.tmp.name) / "failed.sqlite")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_selection_closes_connection_on_query_exception(self):
        conn = read_only_connection(self.path)
        self.addCleanup(conn.close)
        with patch("oem_agent.stage2.read_only_connection", return_value=conn), \
                patch("oem_agent.stage2.list_stage2_candidates", side_effect=RuntimeError("query failed")):
            with self.assertRaisesRegex(RuntimeError, "query failed"):
                execute_stage2_selection(self.path)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_original_demo_and_reports_are_isolated(self):
        demo_path = Path(self.tmp.name) / "demo.sqlite"
        seed_demo(demo_path)
        cases, harness = execute_readonly_workflow(demo_path)
        report_dir = Path(self.tmp.name) / "original-reports"
        write_outputs(report_dir, records=cases, harness=harness, threshold_days=90, mode="rules")
        before_db = demo_path.read_bytes()
        before_reports = {path.name: path.read_bytes() for path in report_dir.iterdir()}
        with self.assertRaises(FileExistsError):
            seed_stage2(demo_path)
        execute_stage2_selection(self.path)
        after_cases, after_harness = execute_readonly_workflow(demo_path)
        self.assertEqual(cases, after_cases)
        self.assertEqual([case["inventory_id"] for case in after_cases], [3, 7, 8, 5, 1, 4])
        self.assertEqual(after_harness.calls, 7)
        self.assertEqual(demo_path.read_bytes(), before_db)
        self.assertEqual({path.name: path.read_bytes() for path in report_dir.iterdir()}, before_reports)
        with closing(read_only_connection(demo_path)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                list_stage2_candidates(conn)
        with closing(read_only_connection(self.path)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("SELECT * FROM inventory")

    def test_cli_seed_select_and_invalid_range(self):
        cli_path = Path(self.tmp.name) / "cli.sqlite"
        command = [sys.executable, "-m", "oem_agent.stage2"]
        cwd = Path(__file__).resolve().parents[1]
        subprocess.run([*command, "seed", "--db", str(cli_path)], cwd=cwd,
                       text=True, capture_output=True, check=True)
        completed = subprocess.run([*command, "select", "--db", str(cli_path)], cwd=cwd,
                                   text=True, capture_output=True, check=True)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout)["total_candidates"], 27)
        before = cli_path.read_bytes()
        invalid = subprocess.run([*command, "select", "--db", str(cli_path),
                                  "--min-days", "91", "--max-days", "60"],
                                 cwd=cwd, text=True, capture_output=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(cli_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
