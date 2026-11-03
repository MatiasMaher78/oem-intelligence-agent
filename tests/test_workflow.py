"""Deterministic offline checks; safe to run on every push."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from oem_agent.db import list_candidates, market_evidence, read_only_connection, seed_demo
from oem_agent.harness import Harness, PolicyViolation, execute_readonly_workflow, write_outputs
from oem_agent.rules import evaluate_case


class SyntheticAgentLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "demo.sqlite"
        seed_demo(self.path)

    def test_demo_refuses_accidental_db_overwrite(self):
        with self.assertRaises(FileExistsError):
            seed_demo(self.path)

    def test_expected_candidate_ids(self):
        with closing(read_only_connection(self.path)) as conn:
            candidates = list_candidates(conn, days=90, limit=10)
        self.assertEqual([r["id"] for r in candidates], [3, 7, 8, 5, 1, 4])

    def test_excludes_zero_stock_and_non_candidates(self):
        with closing(read_only_connection(self.path)) as conn:
            candidates = list_candidates(conn, days=90, limit=20)
        self.assertNotIn(6, [r["id"] for r in candidates])
        self.assertNotIn(2, [r["id"] for r in candidates])

    def test_exact_reference_does_not_prefix_match(self):
        with closing(read_only_connection(self.path)) as conn:
            evidence = market_evidence(conn, "DEMO-ALT-1001")
        self.assertEqual(evidence["offers_count"], 3)
        self.assertEqual(evidence["total_listed_units"], 6)
        self.assertEqual(evidence["min_price_eur"], 95.0)
        self.assertEqual(evidence["max_price_eur"], 125.0)

    def test_missing_reference_has_no_invented_prices(self):
        with closing(read_only_connection(self.path)) as conn:
            evidence = market_evidence(conn, "DEMO-SEN-8008")
        self.assertEqual(evidence["offers_count"], 0)
        self.assertIsNone(evidence["min_price_eur"])
        self.assertIsNone(evidence["max_price_eur"])

    def test_read_only_connection_prevents_changes(self):
        with closing(read_only_connection(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE inventory SET stock_qty=99 WHERE id=1")

    def test_seed_closes_connection_on_exception(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        with patch("oem_agent.db.sqlite3.connect", return_value=conn), \
                patch("oem_agent.db.MARKET", [(1,)]):
            with self.assertRaises(sqlite3.ProgrammingError):
                seed_demo(Path(self.tmp.name) / "failed.sqlite")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_read_only_setup_closes_connection_on_exception(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        with patch("oem_agent.db.sqlite3.connect", return_value=conn):
            with self.assertRaises(sqlite3.DatabaseError):
                read_only_connection(self.path)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_workflow_closes_connection_on_tool_exception(self):
        conn = read_only_connection(self.path)
        self.addCleanup(conn.close)
        with patch("oem_agent.harness.read_only_connection", return_value=conn), \
                patch("oem_agent.harness.market_evidence", side_effect=RuntimeError("tool failed")):
            with self.assertRaisesRegex(RuntimeError, "tool failed"):
                execute_readonly_workflow(self.path)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_harness_denies_non_allowlisted_tool(self):
        h = Harness()
        with self.assertRaises(PolicyViolation):
            h.call("update_price", lambda: None)
        self.assertEqual(h.calls, 0)

    def test_harness_fails_closed_when_budget_exhausted(self):
        h = Harness(max_tool_calls=1)
        h.call("list_candidates", lambda **_: [], days=90)
        with self.assertRaises(PolicyViolation):
            h.call("market_evidence", lambda **_: {"offers_count": 0}, oem="DEMO")

    def test_reproducible_outputs_and_non_overwrite(self):
        cases, h = execute_readonly_workflow(self.path)
        report_dir = Path(self.tmp.name) / "run_1"
        write_outputs(report_dir, records=cases, harness=h, threshold_days=90, mode="rules")
        payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["total_cases"], 6)
        self.assertEqual(payload["data_origin"], "100% synthetic")
        self.assertEqual(len((report_dir / "trace.jsonl").read_text().splitlines()), 7)
        with self.assertRaises(FileExistsError):
            write_outputs(report_dir, records=cases, harness=h, threshold_days=90, mode="rules")

    def test_unverified_oem_never_automatically_changes_price(self):
        cases, _ = execute_readonly_workflow(self.path)
        row = next(case for case in cases if case["inventory_id"] == 3)
        self.assertEqual(row["priority"], "high")
        self.assertFalse(row["oem_verified"])
        self.assertEqual(row["recommended_action"], "human_review_only")
        self.assertTrue(any("unverified" in msg for msg in row["risk_flags"]))

    def test_boundaries(self):
        with self.assertRaises(ValueError):
            execute_readonly_workflow(self.path, max_candidates=21)
        with self.assertRaises(ValueError):
            execute_readonly_workflow(self.path, threshold_days=0)


if __name__ == "__main__":
    unittest.main()
