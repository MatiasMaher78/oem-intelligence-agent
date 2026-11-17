"""Deterministic offline checks; safe to run on every push."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
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

    def test_six_case_priorities_and_seven_read_calls(self):
        cases, harness = execute_readonly_workflow(self.path)
        self.assertEqual({case["inventory_id"]: case["priority"] for case in cases},
                         {3: "high", 7: "high", 8: "high", 5: "low", 1: "medium", 4: "medium"})
        self.assertEqual(harness.calls, 7)
        self.assertEqual([event["tool"] for event in harness.events],
                         ["list_candidates"] + ["market_evidence"] * 6)
        self.assertEqual(harness.events[0]["args"], {"days": 90, "limit": 10})
        self.assertEqual([event["args"]["oem"] for event in harness.events[1:]],
                         [case["oem"] for case in cases])

    def test_candidate_age_boundary_is_inclusive_and_independent_of_sales(self):
        with closing(read_only_connection(self.path)) as conn:
            at_boundary = list_candidates(conn, days=155, limit=20)
            above_boundary = list_candidates(conn, days=156, limit=20)
        case = next(row for row in at_boundary if row["id"] == 5)
        self.assertEqual(case["sales_90d"], 2)
        self.assertNotIn(5, [row["id"] for row in above_boundary])
        self.assertIn(7, [row["id"] for row in above_boundary])  # Unverified OEM exception.

    def test_existing_priority_boundaries_and_precedence(self):
        with closing(read_only_connection(self.path)) as conn:
            base = dict(conn.execute("SELECT * FROM inventory WHERE id = 5").fetchone())
            known_market = market_evidence(conn, base["oem"])
            empty_market = market_evidence(conn, "DEMO-SEN-8008")
        # Pure input copies exercise existing rules; stored fixtures are never changed.
        scenarios = (
            (89, 0, True, 85.0, True, 90, "low"),
            (90, 0, True, 85.0, True, 90, "medium"),
            (90, 1, True, 85.0, True, 90, "low"),
            (179, 0, True, 85.0, True, 90, "medium"),
            (180, 0, True, 85.0, True, 90, "high"),
            (181, 0, True, 85.0, True, 90, "high"),
            (180, 1, True, 85.0, True, 90, "low"),
            (47, 1, False, 85.0, True, 90, "high"),
            (47, 1, False, 85.0, False, 90, "high"),
            (155, 2, True, 90.0, True, 90, "low"),
            (155, 2, True, 90.01, True, 90, "medium"),
            (180, 0, True, 90.01, True, 90, "high"),
            (155, 2, True, 85.0, False, 90, "medium"),
            (180, 0, True, 85.0, False, 90, "high"),
            (104, 0, True, 85.0, True, 105, "low"),
            (105, 0, True, 85.0, True, 105, "medium"),
            (180, 0, True, 85.0, True, 200, "high"),
        )
        for days, sales, verified, price, offers, threshold, expected in scenarios:
            with self.subTest(days=days, sales=sales, verified=verified, price=price,
                              offers=offers, threshold=threshold):
                item = dict(base, days_in_stock=days, sales_90d=sales,
                            oem_verified=int(verified), our_price_eur=price)
                market = known_market if offers else dict(empty_market, oem=base["oem"])
                case = evaluate_case(item, market, age_days=threshold)
                self.assertEqual(case["priority"], expected)
                self.assertEqual(case["recommended_action"], "human_review_only")

    def test_markdown_order_preserves_records_json_and_trace(self):
        cases, harness = execute_readonly_workflow(self.path)
        before_cases = deepcopy(cases)
        before_events = deepcopy(harness.events)
        expected_payload = {"demo_only": True, "data_origin": "100% synthetic",
                            "mode": "rules", "threshold_days": 90,
                            "total_cases": 6, "cases": before_cases}
        report_dir = Path(self.tmp.name) / "ordered"
        write_outputs(report_dir, records=cases, harness=harness, threshold_days=90, mode="rules")
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        headings = [int(line.split()[1][1:]) for line in markdown.splitlines()
                    if line.startswith("## #")]
        self.assertEqual(headings, [8, 3, 7, 1, 4, 5])
        self.assertEqual(cases, before_cases)
        self.assertEqual([case["inventory_id"] for case in cases], [3, 7, 8, 5, 1, 4])
        self.assertEqual((report_dir / "report.json").read_text(encoding="utf-8"),
                         json.dumps(expected_payload, indent=2, ensure_ascii=False))
        self.assertEqual(harness.events, before_events)
        self.assertEqual(harness.calls, 7)
        self.assertEqual((report_dir / "trace.jsonl").read_text(encoding="utf-8"),
                         "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in before_events))

    def test_markdown_ties_use_age_then_inventory_id(self):
        cases, harness = execute_readonly_workflow(self.path)
        base = next(case for case in cases if case["inventory_id"] == 5)
        rows = [dict(base, inventory_id=25), dict(base, inventory_id=15, days_in_stock=180),
                dict(base, inventory_id=5)]
        before = deepcopy(rows)
        report_dir = Path(self.tmp.name) / "ties"
        write_outputs(report_dir, records=rows, harness=harness, threshold_days=90, mode="rules")
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        ids = [int(line.split()[1][1:]) for line in markdown.splitlines() if line.startswith("## #")]
        self.assertEqual(ids, [15, 5, 25])
        self.assertEqual(rows, before)

    def test_markdown_fields_selection_reasons_and_priority_legend(self):
        cases, harness = execute_readonly_workflow(self.path)
        report_dir = Path(self.tmp.name) / "readable"
        write_outputs(report_dir, records=cases, harness=harness, threshold_days=90, mode="rules")
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Selection: stock > 0 and (age >= 90 days or OEM unverified).", markdown)
        self.assertIn("Priority (DEMO; first matching rule wins):", markdown)
        self.assertIn("- HIGH: OEM unverified, or age >= 180 days with sales last 90 days = 0.", markdown)
        self.assertIn("- MEDIUM: only if HIGH does not apply; at least one risk flag, or "
                      "age >= 90 days with sales last 90 days = 0.", markdown)
        self.assertIn("- LOW: neither HIGH nor MEDIUM applies. Selected LOW cases still require human review.",
                      markdown)
        self.assertIn("Presentation order: HIGH, MEDIUM, LOW; then age descending and inventory ID ascending.",
                      markdown)
        sections = {int(block.split()[0]): block for block in markdown.split("## #")[1:]}
        for case in cases:
            with self.subTest(inventory_id=case["inventory_id"]):
                section = sections[case["inventory_id"]]
                self.assertIn(case["part_name"], section.splitlines()[0])
                self.assertTrue(section.splitlines()[0].endswith(case["priority"].upper()))
                self.assertIn(f"OEM: `{case['oem']}` | Stock: {case['stock_qty']} | "
                              f"Age: {case['days_in_stock']} days", section)
                self.assertIn(f"Sales last 90 days: {case['sales_90d']}", section)
                self.assertIn(f"OEM verified: {'yes' if case['oem_verified'] else 'no'}", section)
                self.assertIn(f"Inventory unit price: EUR {case['our_price_eur']:.2f}", section)
                self.assertIn("Action: human review only.", section)
        self.assertIn("Selection reason: Age >= 90 days; OEM requires verification", sections[3])
        self.assertIn("Selection reason: OEM requires verification", sections[7])
        self.assertNotIn("Selection reason: Age", sections[7])
        self.assertIn("Selection reason: Age >= 90 days", sections[5])
        self.assertIn("Flags: none", sections[5])
        self.assertNotIn("aged_inventory", markdown)
        self.assertNotIn("oem_needs_verification", markdown)

    def test_markdown_distinguishes_missing_sales_from_zero(self):
        cases, harness = execute_readonly_workflow(self.path)
        base = next(case for case in cases if case["inventory_id"] == 3)
        # Missing values exercise presentation only, not a new classification rule.
        for index, (present, value) in enumerate(((True, 0), (True, None), (False, None))):
            with self.subTest(present=present, value=value):
                case = deepcopy(base)
                if present:
                    case["sales_90d"] = value
                else:
                    case.pop("sales_90d")
                report_dir = Path(self.tmp.name) / f"sales-{index}"
                write_outputs(report_dir, records=[case], harness=harness, threshold_days=90, mode="rules")
                markdown = (report_dir / "report.md").read_text(encoding="utf-8")
                expected = str(value) if value is not None else "unknown"
                self.assertIn(f"Sales last 90 days: {expected}\n", markdown)
                self.assertNotIn(f"Sales last 90 days: {'unknown' if value == 0 else '0'}\n", markdown)
                stored = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))["cases"][0]
                self.assertEqual("sales_90d" in stored, present)
                if present:
                    self.assertEqual(stored["sales_90d"], value)

    def test_markdown_legend_uses_configured_selection_threshold(self):
        cases, harness = execute_readonly_workflow(self.path, threshold_days=105)
        report_dir = Path(self.tmp.name) / "custom-threshold"
        write_outputs(report_dir, records=cases, harness=harness, threshold_days=105, mode="rules")
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Selection: stock > 0 and (age >= 105 days or OEM unverified).", markdown)
        self.assertIn("age >= 105 days with sales last 90 days = 0.", markdown)
        self.assertIn("Selection reason: Age >= 105 days", markdown)
        self.assertIn("age >= 180 days with sales last 90 days = 0.", markdown)
        self.assertNotIn("Age >= 90 days", markdown)

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
        before = {path.name: path.read_bytes() for path in report_dir.iterdir()}
        with self.assertRaises(FileExistsError):
            write_outputs(report_dir, records=cases, harness=h, threshold_days=90, mode="rules")
        self.assertEqual({path.name: path.read_bytes() for path in report_dir.iterdir()}, before)

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
