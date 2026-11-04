"""Simulated tool calls only: no model, network, services or original fixture edits."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from oem_agent.harness import Harness, PolicyViolation
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import execute_stage2_statistics
from oem_agent.stage4_review import create_dossier, fingerprint
from oem_agent.stage5_dispatcher import DispatchLimits, ReadOnlyDispatcher
from oem_agent.stage5_evidence import EvidenceError, EvidenceSnapshot


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class DispatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.db = Path(cls.folder.name) / "independent.sqlite"
        seed_statistics_demo(cls.db)
        cls.evidence = execute_stage2_statistics(cls.db)
        cls.review = create_dossier(cls.evidence, dossier_id="TEST-DISPATCH", at="2026-11-04T12:00:00+00:00")
        cls.snapshot = EvidenceSnapshot(cls.evidence, cls.review)

    def setUp(self):
        self.dispatcher = ReadOnlyDispatcher(self.snapshot)

    def test_five_tools_reuse_exact_existing_outputs_and_finish_explicitly(self):
        calls = [("summarize_run", {}), ("list_cases", {"oem": "DEMO-S2-BULK-0001"}),
                 ("inspect_case", {"inventory_id": 10}), ("list_offers", {"inventory_id": 3}),
                 ("review_status", {"inventory_id": 10})]
        with patch("sqlite3.connect", side_effect=AssertionError("No DB access allowed")):
            for tool, arguments in calls:
                outcome = self.dispatcher.dispatch(tool, arguments)
                self.assertEqual(outcome["status"], "ok")
                self.assertFalse(outcome["complete"])
                self.assertEqual(outcome["result"], self.snapshot.query(tool, arguments))
        self.assertFalse(self.dispatcher.report()["complete"])
        report = self.dispatcher.finish()
        self.assertTrue(report["complete"])
        self.assertEqual((report["attempts"], report["executions"], report["errors"]), (5, 5, 0))

    def test_forbidden_tools_never_reach_a_reader_and_stop_the_session(self):
        for tool in ("approve", "record_decision", "refresh_dossier", "write_report", "seed", "sql", "shell", "web", "set_price"):
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            with patch.object(EvidenceSnapshot, "query") as reader:
                result = dispatcher.dispatch(tool, {})
                reader.assert_not_called()
                self.assertEqual(result["error"]["code"], "tool_forbidden")
                self.assertEqual(result["session"]["attempts"], 1)
                self.assertEqual(result["session"]["executions"], 0)
                self.assertTrue(result["session"]["stopped"])
            self.assertFalse(dispatcher.finish()["complete"])

    def test_allowlist_can_only_be_narrowed_and_contract_exports_are_copies(self):
        for allowed in (("summarize_run", "approve"), "summarize_run", (False,)):
            with self.assertRaises(ValueError):
                ReadOnlyDispatcher(self.snapshot, allowed_tools=allowed)
        dispatcher = ReadOnlyDispatcher(self.snapshot, allowed_tools=("summarize_run",))
        contracts = dispatcher.contracts()
        self.assertEqual([c["name"] for c in contracts], ["summarize_run"])
        contracts[0]["name"] = "approve"
        self.assertEqual(dispatcher.contracts()[0]["name"], "summarize_run")
        self.assertEqual(dispatcher.dispatch("inspect_case", {"inventory_id": 10})["error"]["code"], "tool_forbidden")

    def test_limits_are_frozen_and_cannot_raise_hard_maxima(self):
        cases = ({"max_calls": True}, {"max_calls": 0}, {"max_calls": 9}, {"max_errors": 3},
                 {"session_seconds": float("nan")}, {"session_seconds": 181}, {"tool_seconds": 3},
                 {"request_bytes": 4097}, {"response_bytes": 8193})
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                DispatchLimits(**values)
        limits = DispatchLimits()
        with self.assertRaises(FrozenInstanceError):
            limits.max_calls = 99
        with self.assertRaises(TypeError):
            ReadOnlyDispatcher(self.snapshot, limits={"max_calls": 99})
        with self.assertRaises(TypeError):
            ReadOnlyDispatcher(object())

    def test_malformed_arguments_are_counted_before_any_execution(self):
        calls = [("inspect_case", None), ("inspect_case", '{"inventory_id":10}'),
                 ("inspect_case", {"inventory_id": True}), ("inspect_case", {"inventory_id": 10.0}),
                 ("inspect_case", {"inventory_id": 10, "section": ["statistics"]}),
                 ("summarize_run", {"sql": "DELETE FROM inventory"}),
                 ("list_cases", {"limit": 21}), ("list_cases", {"oem": 123}),
                 ("list_cases", {1: "bad key"}), ("list_cases", {"cursor": None}),
                 ("list_cases", {"oem": object()}), ("list_cases", {"oem": "\ud800"}),
                 ("list_cases", {"limit": float("nan")})]
        for tool, arguments in calls:
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            with patch.object(EvidenceSnapshot, "query") as reader:
                outcome = dispatcher.dispatch(tool, arguments)
                reader.assert_not_called()
            with self.subTest(tool=tool, arguments_type=type(arguments).__name__):
                self.assertEqual(outcome["error"]["code"], "invalid_arguments")
                self.assertEqual((outcome["session"]["attempts"], outcome["session"]["executions"]), (1, 0))
                self.assertEqual(outcome["session"]["calls_remaining"], 7)
                self.assertFalse(dispatcher.finish()["complete"])

    def test_invalid_tool_name_is_structured_counted_and_not_serialized_as_an_object(self):
        result = self.dispatcher.dispatch(object(), {})
        self.assertEqual(result["tool"], "<invalid-tool-name>")
        self.assertEqual(result["error"]["code"], "invalid_tool_name")
        self.assertEqual(result["session"]["attempts"], 1)

    def test_every_error_remains_sticky_after_a_successful_read(self):
        self.dispatcher.dispatch("inspect_case", {"inventory_id": False})
        result = self.dispatcher.dispatch("inspect_case", {"inventory_id": 10})
        self.assertEqual(result["result"]["status"], "ok")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(self.dispatcher.finish()["complete"])

    def test_two_errors_stop_execution_and_later_attempts_are_still_counted(self):
        self.dispatcher.dispatch("inspect_case", {})
        self.dispatcher.dispatch("inspect_case", {"inventory_id": 999})
        report = self.dispatcher.report()
        self.assertEqual((report["attempts"], report["executions"], report["errors"]), (2, 1, 2))
        self.assertEqual(report["stop_reason"], "error_budget_exhausted")
        self.dispatcher.dispatch("summarize_run", {})
        report = self.dispatcher.finish()
        self.assertEqual(report["attempts"], 3)
        self.assertEqual(report["executions"], 1)
        self.assertFalse(report["complete"])

    def test_last_call_slot_preserves_valid_data_but_exhaustion_is_never_complete(self):
        dispatcher = ReadOnlyDispatcher(self.snapshot, limits=DispatchLimits(max_calls=1))
        result = dispatcher.dispatch("summarize_run")
        self.assertIsNotNone(result["result"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["session"]["stop_reason"], "call_budget_exhausted")
        self.assertEqual(result["session"]["calls_remaining"], 0)
        self.assertFalse(dispatcher.finish()["complete"])
        dispatcher.dispatch("inspect_case", {"inventory_id": 10})
        self.assertEqual((dispatcher.report()["attempts"], dispatcher.report()["executions"]), (2, 1))

    def test_invalid_attempt_uses_the_last_budget_slot_without_executing(self):
        dispatcher = ReadOnlyDispatcher(self.snapshot, limits=DispatchLimits(max_calls=1))
        dispatcher.dispatch("inspect_case", {})
        result = dispatcher.dispatch("summarize_run")
        self.assertEqual(result["session"]["calls_remaining"], 0)
        self.assertEqual(result["session"]["executions"], 0)
        self.assertEqual(result["session"]["attempts"], 2)
        self.assertFalse(dispatcher.finish()["complete"])

    def test_serialization_prevents_concurrent_budget_overrun(self):
        identifiers = [case["inventory"]["id"] for case in self.evidence["results"][:12]]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda identifier: self.dispatcher.dispatch("inspect_case", {"inventory_id": identifier}), identifiers))
        report = self.dispatcher.finish()
        self.assertEqual(report["attempts"], 12)
        self.assertEqual(report["executions"], 8)
        self.assertEqual([event["sequence"] for event in report["trace"]], list(range(1, 13)))
        self.assertFalse(report["complete"])

    def test_repeated_calls_and_equivalent_defaults_stop_without_a_second_read(self):
        pairs = [(('summarize_run', {}), ('summarize_run', {})),
                 (('inspect_case', {"inventory_id": 10}),
                  ('inspect_case', {"inventory_id": 10, "section": "statistics"}))]
        for first, repeated in pairs:
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            dispatcher.dispatch(*first)
            outcome = dispatcher.dispatch(*repeated)
            self.assertEqual(outcome["error"]["code"], "repeated_call")
            self.assertEqual(outcome["session"]["executions"], 1)
            self.assertFalse(dispatcher.finish()["complete"])
        self.dispatcher.dispatch("summarize_run")
        self.dispatcher.dispatch("inspect_case", {"inventory_id": 10})
        self.assertEqual(self.dispatcher.dispatch("summarize_run")["error"]["code"], "repeated_call")

    def test_all_27_cases_are_paginated_without_truncation_and_then_complete(self):
        items, cursor = [], None
        while True:
            args = {"limit": 10}
            if cursor:
                args["cursor"] = cursor
            outcome = self.dispatcher.dispatch("list_cases", args)
            self.assertIsNone(outcome["error"])
            data = outcome["result"]["data"]
            items.extend(data["items"])
            cursor = data["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([item["inventory"] for item in items], [case["inventory"] for case in self.evidence["results"]])
        self.assertEqual(self.dispatcher.report()["attempts"], 3)
        self.assertTrue(self.dispatcher.finish()["complete"])

    def test_offer_pagination_keeps_exclusions_and_units_separate_from_ads(self):
        rows, cursor = [], None
        while True:
            args = {"inventory_id": 10, "limit": 2}
            if cursor:
                args["cursor"] = cursor
            outcome = self.dispatcher.dispatch("list_offers", args)
            self.assertIsNone(outcome["error"])
            data = outcome["result"]["data"]
            rows.extend(item["audit"] for item in data["items"])
            cursor = data["next_cursor"]
            if cursor is None:
                break
        expected = next(case for case in self.evidence["results"] if case["inventory"]["id"] == 10)["offer_audit"]
        self.assertEqual(rows, expected)
        observed = [row for row in rows if row["observed_included"]]
        retained = [row for row in rows if row["filtered_included"]]
        self.assertEqual((len(observed), sum(row["offer"]["available_units"] for row in observed)), (10, 55))
        self.assertEqual((len(retained), sum(row["offer"]["available_units"] for row in retained)), (8, 44))
        self.assertTrue(self.dispatcher.finish()["complete"])

    def test_unfinished_pagination_cannot_be_finalized_as_complete(self):
        self.dispatcher.dispatch("list_cases", {"limit": 5})
        report = self.dispatcher.finish()
        self.assertEqual(report["pending_pages"], 1)
        self.assertEqual(report["errors"], 0)
        self.assertFalse(report["complete"])

    def test_cursor_must_be_issued_by_this_session_and_match_next_page_filters(self):
        cursor = self.snapshot.query("list_cases", {"limit": 5})["data"]["next_cursor"]
        result = self.dispatcher.dispatch("list_cases", {"limit": 5, "cursor": cursor})
        self.assertEqual(result["error"]["code"], "invalid_cursor_sequence")
        self.assertEqual(result["session"]["executions"], 0)
        dispatcher = ReadOnlyDispatcher(self.snapshot)
        first = dispatcher.dispatch("list_cases", {"limit": 5})
        cursor = first["result"]["data"]["next_cursor"]
        result = dispatcher.dispatch("list_cases", {"cursor": cursor, "oem": "DEMO-S2-ALT-0002"})
        self.assertEqual(result["error"]["code"], "invalid_cursor_sequence")
        self.assertFalse(dispatcher.finish()["complete"])

    def test_restarting_pagination_with_a_different_page_size_is_still_no_progress(self):
        self.dispatcher.dispatch("list_cases", {"limit": 5})
        result = self.dispatcher.dispatch("list_cases", {"limit": 6})
        self.assertEqual(result["error"]["code"], "repeated_call")
        self.assertEqual(result["session"]["executions"], 1)

    def test_oversized_response_is_discarded_and_recovery_never_erases_the_error(self):
        oversized = self.dispatcher.dispatch("list_cases")
        self.assertEqual(oversized["error"]["code"], "response_too_large")
        self.assertIsNone(oversized["result"])
        event = self.dispatcher.report()["trace"][0]
        self.assertGreater(event["tool_result_bytes"], 8192)
        self.assertIsNotNone(event["tool_result_sha256"])
        outcome = self.dispatcher.dispatch("list_cases", {"limit": 10})
        self.assertIsNone(outcome["error"])
        self.assertIsNotNone(outcome["result"]["data"]["next_cursor"])
        self.assertFalse(self.dispatcher.finish()["complete"])

    def test_request_limit_counts_utf8_bytes_and_rejects_before_execution(self):
        for oem in ("x" * 4097, "\U0001f697" * 1200):
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            result = dispatcher.dispatch("list_cases", {"oem": oem})
            self.assertEqual(result["error"]["code"], "request_too_large")
            self.assertEqual(result["session"]["executions"], 0)
            self.assertFalse(dispatcher.finish()["complete"])

    def test_missing_review_is_not_an_error_but_cannot_be_presented_as_complete(self):
        dispatcher = ReadOnlyDispatcher(EvidenceSnapshot(self.evidence))
        outcome = dispatcher.dispatch("review_status", {"inventory_id": 10})
        self.assertEqual(outcome["result"]["status"], "not_available")
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["session"]["errors"], 0)
        self.assertFalse(dispatcher.finish()["complete"])

    def test_source_and_inventory_failures_preserve_unknown_coverage_and_prevent_completeness(self):
        faults = ("oem_agent.stage3_statistics._list_source_offers", "oem_agent.stage2_matching.list_stage2_candidates")
        for target in faults:
            with patch(target, side_effect=sqlite3.OperationalError("Synthetic source failure")):
                evidence = execute_stage2_statistics(self.db)
            dispatcher = ReadOnlyDispatcher(EvidenceSnapshot(evidence))
            outcome = dispatcher.dispatch("summarize_run")
            self.assertEqual(outcome["status"], "partial")
            self.assertIsNone(outcome["result"]["data"]["source_offers_count"])
            self.assertFalse(dispatcher.finish()["complete"])
            if target.endswith("list_stage2_candidates"):
                self.assertIsNone(outcome["result"]["coverage"]["total_candidates"])

    def test_evidence_and_unexpected_exceptions_are_audited_without_exception_messages(self):
        for exception, code in ((EvidenceError("bad", "PRIVATE-DETAIL"), "evidence_error"),
                                (RuntimeError("PRIVATE-DETAIL"), "tool_execution_error")):
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            with patch.object(EvidenceSnapshot, "query", side_effect=exception):
                result = dispatcher.dispatch("summarize_run")
            self.assertEqual(result["error"]["code"], code)
            self.assertEqual(result["session"]["executions"], 1)
            self.assertNotIn("PRIVATE-DETAIL", json.dumps(dispatcher.report()))
            self.assertFalse(dispatcher.finish()["complete"])

    def test_underlying_harness_policy_remains_active(self):
        with patch.object(Harness, "call", side_effect=PolicyViolation("PRIVATE-DETAIL")):
            outcome = self.dispatcher.dispatch("summarize_run")
        self.assertEqual(outcome["error"]["code"], "policy_violation")
        self.assertFalse(self.dispatcher.finish()["complete"])

    def test_keyboard_interrupt_is_audited_and_propagated_without_leaving_a_completable_session(self):
        with patch.object(EvidenceSnapshot, "query", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.dispatcher.dispatch("summarize_run")
        report = self.dispatcher.finish()
        self.assertEqual(len(report["trace"]), 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["stop_reason"], "tool_execution_error")

    def test_invalid_reader_shapes_hashes_and_references_are_terminal(self):
        original = self.snapshot.query("summarize_run")
        invalid = [None, {**original, "snapshot_id": "wrong"}, {**original, "unexpected": True}]
        bad_reference = deepcopy(original)
        bad_reference["references"][0]["sha256"] = "0" * 64
        bad_reference["trace"]["result_sha256"] = fingerprint({k: v for k, v in bad_reference.items() if k != "trace"})
        invalid.append(bad_reference)
        bad_status = deepcopy(original)
        bad_status["status"] = "partial"
        bad_status["trace"]["status"] = "partial"
        bad_status["trace"]["result_sha256"] = fingerprint({k: v for k, v in bad_status.items() if k != "trace"})
        invalid.append(bad_status)
        for value in invalid:
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            with patch.object(EvidenceSnapshot, "query", return_value=value):
                outcome = dispatcher.dispatch("summarize_run")
            self.assertEqual(outcome["error"]["code"], "invalid_tool_result")
            self.assertIsNone(outcome["result"])
            self.assertTrue(outcome["session"]["stopped"])
            self.assertFalse(dispatcher.finish()["complete"])

    def test_session_timeout_blocks_before_reading_and_at_finalization(self):
        clock = Clock()
        with patch("oem_agent.stage5_dispatcher.monotonic", clock):
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            clock.now = 180
            result = dispatcher.dispatch("summarize_run")
            self.assertEqual(result["error"]["code"], "session_timeout")
            self.assertEqual(result["session"]["executions"], 0)
            self.assertFalse(dispatcher.finish()["complete"])
            clock.now = 0
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            dispatcher.dispatch("summarize_run")
            clock.now = 180
            report = dispatcher.finish()
            self.assertEqual(report["stop_reason"], "session_timeout")
            self.assertFalse(report["complete"])

    def test_inconsistent_pagination_cannot_claim_complete_even_with_recomputed_hashes(self):
        original = self.snapshot.query("list_cases", {"limit": 5})
        for field, value in (("next_cursor", None), ("returned_rows", 4), ("total_rows", 4)):
            result = deepcopy(original)
            result["data"][field] = value
            result["trace"]["result_sha256"] = fingerprint({k: v for k, v in result.items() if k != "trace"})
            dispatcher = ReadOnlyDispatcher(self.snapshot)
            with patch.object(EvidenceSnapshot, "query", return_value=result):
                outcome = dispatcher.dispatch("list_cases", {"limit": 5})
            self.assertEqual(outcome["error"]["code"], "invalid_tool_result")
            self.assertIsNone(outcome["result"])
            self.assertFalse(dispatcher.finish()["complete"])

    def test_late_tool_results_are_discarded_for_both_tool_and_session_deadlines(self):
        original = EvidenceSnapshot.query
        for elapsed, code in ((2, "tool_timeout"), (180, "session_timeout")):
            clock = Clock()
            with patch("oem_agent.stage5_dispatcher.monotonic", clock):
                dispatcher = ReadOnlyDispatcher(self.snapshot)
                def late_read(tool, arguments):
                    result = original(self.snapshot, tool, arguments)
                    clock.now = elapsed
                    return result
                with patch.object(EvidenceSnapshot, "query", side_effect=late_read):
                    result = dispatcher.dispatch("summarize_run")
                self.assertEqual(result["error"]["code"], code)
                self.assertIsNone(result["result"])
                self.assertFalse(dispatcher.finish()["complete"])

    def test_trace_chain_and_report_hash_are_verifiable_and_exposed_copies_cannot_change_them(self):
        self.dispatcher.dispatch("summarize_run")
        self.dispatcher.dispatch("inspect_case", {"inventory_id": 10})
        report = self.dispatcher.finish()
        previous = None
        for event in report["trace"]:
            self.assertEqual(event["previous_event_sha256"], previous)
            self.assertEqual(event["event_sha256"], fingerprint({k: v for k, v in event.items() if k != "event_sha256"}))
            previous = event["event_sha256"]
        self.assertEqual(report["report_sha256"], fingerprint({k: v for k, v in report.items() if k != "report_sha256"}))
        report["trace"][0]["outcome"]["result"]["data"].clear()
        self.assertNotEqual(report, self.dispatcher.report())
        self.assertEqual(self.dispatcher.finish(), self.dispatcher.report())

    def test_after_finalization_an_attempt_is_logged_and_changes_current_report_to_partial(self):
        self.dispatcher.dispatch("summarize_run")
        earlier = self.dispatcher.finish()
        self.assertTrue(earlier["complete"])
        result = self.dispatcher.dispatch("inspect_case", {"inventory_id": 10})
        self.assertEqual(result["error"]["code"], "session_closed")
        self.assertFalse(self.dispatcher.report()["complete"])
        self.assertTrue(earlier["complete"])  # Historical report is an independent copy.
        self.assertEqual(self.dispatcher.report()["executions"], 1)

    def test_export_is_operator_only_and_never_overwrites_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.json"
            self.dispatcher.dispatch("summarize_run")
            self.dispatcher.finish()
            self.dispatcher.write_report(path)
            before = path.read_bytes()
            self.assertTrue(json.loads(before)["complete"])
            with self.assertRaises(FileExistsError):
                self.dispatcher.write_report(path)
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(ValueError):
                self.dispatcher.write_report(path.with_suffix(".sqlite"))
            race = Path(folder) / "race.json"
            original_open = Path.open
            def concurrent_writer(target, mode="r", *args, **kwargs):
                if target == race and mode == "xb":
                    with original_open(target, "wb") as stream:
                        stream.write(b"another writer")
                return original_open(target, mode, *args, **kwargs)
            with patch.object(Path, "open", concurrent_writer), self.assertRaises(FileExistsError):
                self.dispatcher.write_report(race)
            self.assertEqual(race.read_bytes(), b"another writer")

    def test_dispatcher_leaves_original_evidence_review_and_database_bytes_unchanged(self):
        evidence, review = deepcopy(self.evidence), deepcopy(self.review)
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.dispatcher.dispatch("inspect_case", {"inventory_id": 10})
        self.dispatcher.dispatch("approve", {"inventory_id": 10})
        self.dispatcher.finish()
        self.assertEqual(self.evidence, evidence)
        self.assertEqual(self.review, review)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
