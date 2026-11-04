"""Increment 5.1 contract, integrity, pagination and original-pipeline regression checks."""
import copy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from oem_agent.stage2 import execute_stage2_selection
from oem_agent.stage2_db import seed_stage2, list_stage2_offers
from oem_agent.stage2_matching import compare_offer
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import analyze_matching, execute_stage2_statistics
from oem_agent.stage4_review import create_dossier, fingerprint, record_decision
from oem_agent.stage5_contracts import tool_contracts, validate_arguments
from oem_agent.stage5_evidence import EvidenceError, EvidenceSnapshot


class EvidenceSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.db = Path(cls.folder.name) / "statistics.sqlite"
        seed_statistics_demo(cls.db)
        cls.good = execute_stage2_statistics(cls.db)
        old = Path(cls.folder.name) / "old.sqlite"
        seed_stage2(old)
        cls.old = execute_stage2_statistics(old)
        cls.review = create_dossier(cls.good, dossier_id="TEST-51", at="2026-11-04T12:00:00+00:00")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = copy.deepcopy(self.good)
        self.dossier = copy.deepcopy(self.review)
        self.snapshot = EvidenceSnapshot(self.evidence, self.dossier)

    def write(self, name, document):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def pages(self, tool, **arguments):
        items, refs, cursor = [], [], None
        while True:
            request = dict(arguments)
            if cursor is not None:
                request["cursor"] = cursor
            response = self.snapshot.query(tool, request)
            self.assertIn(response["status"], ("ok", "partial"))
            data = response["data"]
            self.assertEqual(data["returned_rows"], len(data["items"]))
            items.extend(data["items"])
            refs.extend(response["references"])
            cursor = data["next_cursor"]
            if cursor is None:
                self.assertEqual(len(items), data["total_rows"])
                return items, refs

    def test_reuses_selection_matching_statistics_and_review_without_requerying_db(self):
        with patch("sqlite3.connect", side_effect=AssertionError("Readers must not query SQLite")):
            response = self.snapshot.query("summarize_run")
            self.assertEqual(response["coverage"]["total_candidates"], 27)
            self.assertEqual(response["data"]["source_offers_count"], 15)
            self.assertEqual(response["data"]["evidence_status_counts"]["sufficient_sample_demo"], 1)
            case = self.snapshot.query("inspect_case", {"inventory_id": 10})["data"]["content"]["statistics"]
            self.assertEqual((case["observed_offers_count"], case["observed_units"]), (10, 55))
            self.assertEqual((case["filtered_offers_count"], case["filtered_units"]), (8, 44))
            self.assertEqual((case["filtered_min_price_cents"], case["filtered_max_price_cents"]), (2000, 9000))
            review = self.snapshot.query("review_status", {"inventory_id": 10})["data"]
            self.assertEqual(review["case"]["demo_estimate"]["unit_cost_cents"], 250)
            self.assertEqual(review["case"]["review_state"], "pending")
            self.assertFalse(review["case"]["ready_for_demo_use"])

    def test_snapshot_is_frozen_and_every_exposed_value_is_an_independent_copy(self):
        before = self.snapshot.query("summarize_run")
        self.evidence["results"].clear()
        self.dossier["cases"].clear()
        self.snapshot.evidence["results"].clear()
        self.snapshot.dossier["cases"].clear()
        response = self.snapshot.query("list_cases")
        response["data"]["items"][0]["inventory"]["oem"] = "CHANGED"
        self.snapshot.resolve_reference(response["references"][0]).clear()
        self.assertEqual(before, self.snapshot.query("summarize_run"))
        self.assertNotEqual(self.snapshot.query("list_cases")["data"]["items"][0]["inventory"]["oem"], "CHANGED")
        with self.assertRaises(FrozenInstanceError):
            self.snapshot.snapshot_id = "changed"

    def test_file_snapshot_survives_source_change_deletion_and_releases_windows_handles(self):
        source = self.write("evidence.json", self.evidence)
        review = self.write("dossier.json", self.dossier)
        snapshot = EvidenceSnapshot.load(source, review)
        before = snapshot.query("list_cases")
        source.write_text("{}", encoding="utf-8")
        source.unlink()
        review.rename(review.with_name("moved.json"))
        self.assertEqual(before, snapshot.query("list_cases"))
        with self.assertRaises(EvidenceError) as error:
            EvidenceSnapshot.load(source)
        self.assertEqual(error.exception.code, "file_read_error")

    def test_fingerprints_are_reproducible_and_optional_expected_hash_is_enforced(self):
        reordered = json.loads(json.dumps(self.evidence, sort_keys=True))
        second = EvidenceSnapshot(reordered, self.dossier, expected_evidence_sha256=fingerprint(self.evidence))
        self.assertEqual(second.snapshot_id, self.snapshot.snapshot_id)
        self.assertEqual(second.query("list_cases"), self.snapshot.query("list_cases"))
        self.assertNotEqual(EvidenceSnapshot(self.evidence).snapshot_id, self.snapshot.snapshot_id)
        with self.assertRaises(EvidenceError) as error:
            EvidenceSnapshot(self.evidence, expected_evidence_sha256="0" * 64)
        self.assertEqual(error.exception.code, "integrity_error")

    def test_malformed_json_duplicate_keys_nonfinite_values_and_missing_files(self):
        path = Path(self.tmp.name) / "invalid.json"
        for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{', b'\xff'):
            with self.subTest(value=value):
                path.write_bytes(value)
                with self.assertRaises(EvidenceError) as error:
                    EvidenceSnapshot.load(path)
                self.assertEqual(error.exception.code, "invalid_json")
        with self.assertRaises(EvidenceError):
            EvidenceSnapshot.load(path.parent / "missing.json")

    def test_corrupt_statistics_matching_catalog_review_and_trace_are_rejected(self):
        def change_statistics(e):
            e["results"][1]["statistics"]["observed_units"] += 1
        def change_matching(e):
            case = e["results"][0]
            row = case["marketplaces"][0]["comparisons"][0]
            row["oem_match"] = "different"
            row["confirmed_match"] = False
            case["marketplaces"][0]["confirmed_matches_count"] -= 1
            return analyze_matching(e, source_offers=e["source_offers"])
        def change_catalog(e):
            e["source_offers"][0]["price_cents"] += 1
            return analyze_matching(e, source_offers=e["source_offers"])
        def change_trace(e):
            del e["trace"][0]["sql"]
        for change in (change_statistics, change_matching, change_catalog, change_trace):
            e = copy.deepcopy(self.evidence)
            changed = change(e)
            with self.subTest(change=change.__name__), self.assertRaises(EvidenceError):
                EvidenceSnapshot(changed or e)
        self.dossier["cases"][0]["review_state"] = "approved"
        with self.assertRaises(EvidenceError):
            EvidenceSnapshot(self.evidence, self.dossier)

    def test_all_27_cases_paginate_in_the_exact_original_selection_order(self):
        items, refs = self.pages("list_cases", limit=4)
        self.assertEqual([item["inventory"] for item in items], execute_stage2_selection(self.db)["candidates"])
        self.assertEqual(len({item["inventory"]["id"] for item in items}), 27)
        self.assertEqual(len(refs), 27)

    def test_default_page_explicit_end_and_exact_oem_and_status_filters(self):
        first = self.snapshot.query("list_cases")["data"]
        self.assertEqual(first["returned_rows"], 20)
        last = self.snapshot.query("list_cases", {"cursor": first["next_cursor"]})["data"]
        self.assertEqual(last["returned_rows"], 7)
        self.assertIsNone(last["next_cursor"])
        exact = self.snapshot.query("list_cases", {"oem": "DEMO-S2-ALT-0002"})["data"]
        self.assertEqual([row["inventory"]["id"] for row in exact["items"]], [2])
        self.assertEqual(self.snapshot.query("list_cases", {"oem": "DEMO-S2-ALT"})["data"]["total_rows"], 0)
        sufficient = self.snapshot.query("list_cases", {"evidence_status": "sufficient_sample_demo"})["data"]
        self.assertEqual([row["inventory"]["id"] for row in sufficient["items"]], [10])

    def test_cursors_reject_changes_of_snapshot_tool_filters_or_offset(self):
        cursor = self.snapshot.query("list_cases", {"limit": 2})["data"]["next_cursor"]
        requests = [("list_cases", {"cursor": "3." + cursor.split(".")[1]}),
                    ("list_cases", {"cursor": cursor, "oem": "DEMO-S2-ALT-0002"}),
                    ("list_offers", {"cursor": cursor}), ("list_cases", {"cursor": "nonsense"})]
        for tool, arguments in requests:
            result = self.snapshot.query(tool, arguments)
            self.assertEqual(result["error"]["code"], "invalid_cursor")
        another = EvidenceSnapshot(self.evidence)
        self.assertEqual(another.query("list_cases", {"cursor": cursor})["error"]["code"], "invalid_cursor")
        # Page size can change without changing the query's filters or ordering.
        self.assertEqual(self.snapshot.query("list_cases", {"cursor": cursor, "limit": 1})["data"]["returned_rows"], 1)

    def test_offer_pages_keep_every_excluded_row_and_separate_ads_from_units(self):
        items, refs = self.pages("list_offers", inventory_id=10, limit=3)
        original = next(case for case in self.evidence["results"] if case["inventory"]["id"] == 10)
        self.assertEqual([item["audit"] for item in items], original["offer_audit"])
        comparable = [item["audit"] for item in items if item["audit"]["observed_included"]]
        filtered = [row for row in comparable if row["filtered_included"]]
        self.assertEqual((len(comparable), sum(row["offer"]["available_units"] for row in comparable)), (10, 55))
        self.assertEqual((len(filtered), sum(row["offer"]["available_units"] for row in filtered)), (8, 44))
        self.assertTrue(any(item["audit"]["exclusion_reasons"] for item in items))
        self.assertEqual(len(refs), len(items))

    def test_global_offer_pagination_and_marketplace_filter_preserve_audit_context(self):
        items, refs = self.pages("list_offers", limit=7)
        expected = [(case["inventory"]["id"], row) for case in self.evidence["results"] for row in case["offer_audit"]]
        expected += [(None, row) for row in self.evidence["unassociated_offer_audit"]]
        self.assertEqual([(item["inventory_id"], item["audit"]) for item in items], expected)
        self.assertEqual(len(refs), len(expected))
        source, _ = self.pages("list_offers", inventory_id=2, marketplace_id="DEMO-MARKET-A", limit=1)
        self.assertTrue(all(item["audit"]["source_marketplace_id"] == "DEMO-MARKET-A" for item in source))
        all_identities = {(item["audit"]["offer"]["marketplace_id"], item["audit"]["offer"]["offer_id"]) for item in items}
        self.assertEqual(all_identities, {(row["marketplace_id"], row["offer_id"]) for row in self.evidence["source_offers"]})

    def test_unknown_id_is_not_a_successful_empty_result(self):
        for tool in ("inspect_case", "list_offers", "review_status"):
            result = self.snapshot.query(tool, {"inventory_id": 999})
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"]["code"], "unknown_inventory_id")
            self.assertIsNone(result["data"])

    def test_missing_review_insufficient_prices_and_zero_units_remain_distinct(self):
        snapshot = EvidenceSnapshot(self.old)
        missing = snapshot.query("review_status", {"inventory_id": 2})
        self.assertEqual(missing["status"], "not_available")
        self.assertIsNone(missing["data"])
        self.assertIsNone(missing["error"])
        stats = snapshot.query("inspect_case", {"inventory_id": 2})["data"]["content"]["statistics"]
        self.assertEqual(stats["evidence_status"], "insufficient_sample")
        self.assertIsNotNone(stats["observed_min_price_cents"])
        self.assertIsNone(stats["filtered_min_price_cents"])
        rows = snapshot.query("list_offers", {"inventory_id": 7})["data"]["items"]
        zero = next(row["audit"] for row in rows if row["audit"]["offer"]["available_units"] == 0)
        self.assertEqual(zero["offer"]["available_units"], 0)
        self.assertIn("no_available_units", zero["exclusion_reasons"])

    def test_missing_offer_fields_stay_null_and_classifications_unknown(self):
        e = copy.deepcopy(self.evidence)
        for case in e["results"]:
            for market in case["marketplaces"]:
                for index, row in enumerate(market["comparisons"]):
                    if row["offer"]["offer_id"] == "003":
                        row["offer"]["oem"] = None
                        row["offer"]["version"] = None
                        market["comparisons"][index] = compare_offer(case["inventory"], row["offer"])
                market["confirmed_matches_count"] = sum(row["confirmed_match"] for row in market["comparisons"])
        for offer in e["source_offers"]:
            if offer["offer_id"] == "003":
                offer.update(oem=None, version=None)
        changed = analyze_matching(e, source_offers=e["source_offers"])
        snapshot = EvidenceSnapshot(changed)
        rows = snapshot.query("list_offers", {"inventory_id": 2})["data"]["items"]
        row = next(item["audit"] for item in rows if item["audit"]["offer"]["offer_id"] == "003")
        self.assertIsNone(row["offer"]["oem"])
        self.assertEqual((row["oem_match"], row["compatibility"]), ("unknown", "unknown"))

    def test_all_sections_and_source_references_resolve_in_the_pinned_snapshot(self):
        for section in ("inventory", "matching", "statistics", "trace"):
            response = self.snapshot.query("inspect_case", {"inventory_id": 10, "section": section})
            self.assertEqual(response["data"]["section"], section)
            for ref in response["references"]:
                self.snapshot.resolve_reference(ref)
        trace = self.snapshot.query("inspect_case", {"inventory_id": 10, "section": "trace"})["data"]["content"]
        self.assertEqual(len(trace["sql_events"]), 4)
        self.assertTrue(all("sql" in event and "parameters" in event for event in trace["sql_events"]))
        self.assertEqual(trace["statistics_event"]["inventory_id"], 10)

    def test_response_trace_hashes_cover_success_errors_and_missing_review(self):
        for snapshot, tool, args in ((self.snapshot, "list_cases", {}),
                                     (self.snapshot, "inspect_case", {"inventory_id": 999}),
                                     (EvidenceSnapshot(self.old), "review_status", {"inventory_id": 2})):
            response = snapshot.query(tool, args)
            trace = response.pop("trace")
            self.assertEqual(trace["operation"], tool)
            self.assertEqual(trace["arguments"], args)
            self.assertEqual(trace["status"], response["status"])
            self.assertEqual(trace["result_sha256"], fingerprint(response))
            self.assertEqual(trace["request_sha256"], fingerprint({"tool": tool, "arguments": args}))

    def test_invalid_references_cannot_select_other_documents_or_paths(self):
        ref = self.snapshot.query("list_cases")["references"][0]
        for invalid in ({**ref, "sha256": "0" * 64}, {**ref, "document": "C:/secrets"},
                        {**ref, "pointer": "/results/-1"}, {**ref, "pointer": "/results/999"},
                        {**ref, "pointer": "/results/00"}, {**ref, "pointer": "/bad~2"}):
            with self.subTest(invalid=invalid), self.assertRaises(EvidenceError):
                self.snapshot.resolve_reference(invalid)

    def test_current_and_stale_human_decisions_are_only_read_never_recreated(self):
        approved = record_decision(self.dossier, self.evidence, inventory_id=10, state="approved", reviewer="TEST-ONLY",
                                   reason="Automated test fixture, not human approval", decision_id="TEST-D1",
                                   at="2026-11-04T12:00:00+00:00")
        current = EvidenceSnapshot(self.evidence, approved)
        result = current.query("review_status", {"inventory_id": 10})["data"]
        self.assertEqual(result["case"]["review_state"], "approved")
        self.assertEqual(result["current_decision"]["decision_id"], "TEST-D1")
        changed = copy.deepcopy(self.evidence)
        changed["source"]["database"] = "changed-provenance.sqlite"
        stale = EvidenceSnapshot(changed, approved)
        result = stale.query("review_status", {"inventory_id": 10})
        self.assertTrue(result["data"]["needs_refresh"])
        self.assertEqual(result["data"]["case"]["review_state"], "review_required")
        self.assertFalse(result["data"]["case"]["ready_for_demo_use"])
        self.assertIsNone(result["data"]["current_decision"])
        self.assertIn("review_evidence_changed", result["warnings"])
        self.assertEqual(approved["cases"][1]["review_state"], "approved")

    def test_partial_sources_keep_27_cases_errors_and_unknown_counts(self):
        def fail_a(conn, *, marketplace_id, oem, part_name):
            if marketplace_id == "DEMO-MARKET-A":
                raise sqlite3.OperationalError("Synthetic source error")
            return list_stage2_offers(conn, marketplace_id=marketplace_id, oem=oem, part_name=part_name)
        with patch("oem_agent.stage2_matching.list_stage2_offers", side_effect=fail_a):
            e = execute_stage2_statistics(self.db)
        snapshot = EvidenceSnapshot(e)
        result = snapshot.query("inspect_case", {"inventory_id": 10, "section": "matching"})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["total_candidates"], 27)
        market = result["data"]["content"]["marketplaces"][0]
        self.assertEqual(market["status"], "error")
        self.assertIsNone(market["offers_count"])
        self.assertTrue(result["data"]["content"]["has_confirmed_match"])
        self.assertIsNone(snapshot.query("inspect_case", {"inventory_id": 11, "section": "matching"})
                          ["data"]["content"]["has_confirmed_match"])
        self.assertEqual(result["data"]["content"]["catalog_status"], "not_assessed")

    def test_fatal_inventory_error_is_not_empty_success_and_audit_error_remains_unknown(self):
        with patch("oem_agent.stage2_matching.list_stage2_candidates", side_effect=sqlite3.OperationalError("Synthetic failure")):
            e = execute_stage2_statistics(self.db)
        snapshot = EvidenceSnapshot(e)
        response = snapshot.query("list_cases")
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["coverage"]["status"], "error")
        self.assertIsNone(response["coverage"]["total_candidates"])
        self.assertEqual(response["data"]["items"], [])
        with patch("oem_agent.stage3_statistics._list_source_offers", side_effect=sqlite3.OperationalError("Synthetic audit failure")):
            e = execute_stage2_statistics(self.db)
        response = EvidenceSnapshot(e).query("summarize_run")
        self.assertEqual(response["status"], "partial")
        self.assertIsNone(response["data"]["source_offers_count"])

    def test_readers_leave_database_source_files_and_original_results_unchanged(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        original = copy.deepcopy(self.evidence)
        path = self.write("evidence.json", self.evidence)
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        loaded = EvidenceSnapshot.load(path)
        self.pages("list_cases", limit=5)
        self.pages("list_offers", limit=5)
        loaded.query("summarize_run")
        self.assertEqual(self.evidence, original)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file_hash)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_unassociated_offers_are_kept_and_corrupt_unassociated_audit_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        offer = dict(evidence["source_offers"][0], offer_id="UNASSOCIATED", oem="TEST-UNRELATED", part_name="Test unrelated")
        evidence["source_offers"].append(offer)
        evidence = analyze_matching(evidence, source_offers=evidence["source_offers"])
        snapshot = EvidenceSnapshot(evidence)
        cursor, found = None, []
        while True:
            response = snapshot.query("list_offers", {"cursor": cursor} if cursor else {})
            found.extend(item for item in response["data"]["items"] if item["inventory_id"] is None)
            cursor = response["data"]["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([item["audit"]["offer"] for item in found], [offer])
        evidence["unassociated_offer_audit"][0]["offer"]["price_cents"] += 1
        with self.assertRaises(EvidenceError):
            EvidenceSnapshot(evidence)

    def test_numeric_alphanumeric_and_separator_oems_remain_literal_text(self):
        # Transport/filter checks only: no new dataset or original fixture changes.
        for oem in ("0000421001", "EV5A0102B", "EV5-AB/03.01"):
            evidence = copy.deepcopy(self.evidence)
            case = next(case for case in evidence["results"] if case["inventory"]["id"] == 33)
            case["inventory"]["oem"] = oem
            for market in case["marketplaces"]:
                market["comparisons"] = [compare_offer(case["inventory"], row["offer"]) for row in market["comparisons"]]
            evidence = analyze_matching(evidence, source_offers=evidence["source_offers"])
            path = self.write("text-oem.json", evidence)
            result = EvidenceSnapshot.load(path).query("list_cases", {"oem": oem})["data"]
            self.assertEqual([item["inventory"]["oem"] for item in result["items"]], [oem])
            self.assertIsInstance(result["items"][0]["inventory"]["oem"], str)

    def test_exactly_five_contracts_reject_writes_and_extra_or_malformed_arguments(self):
        self.assertEqual({c["name"] for c in tool_contracts()},
                         {"summarize_run", "list_cases", "inspect_case", "list_offers", "review_status"})
        requests = [("approve", {}), ("summarize_run", {"sql": "UPDATE inventory"}),
                    ("list_cases", {"path": "../secret"}), ("list_cases", {"limit": True}),
                    ("list_cases", {"limit": 0}), ("list_cases", {"limit": 21}),
                    ("list_cases", {"oem": 123}), ("list_cases", {"cursor": ""}),
                    ("list_cases", {"evidence_status": "invented"}), ("inspect_case", {}),
                    ("inspect_case", {"inventory_id": False}), ("inspect_case", {"inventory_id": -1}),
                    ("inspect_case", {"inventory_id": 10, "section": "write"}),
                    ("list_offers", {"marketplace_id": "external"}), ("review_status", {"inventory_id": 10, "state": "approved"})]
        for tool, args in requests:
            with self.subTest(tool=tool, args=args), self.assertRaises(ValueError):
                validate_arguments(tool, args)

    def test_contract_copies_do_not_mutate_validation_or_other_contract_exports(self):
        first = tool_contracts()
        first[1]["input_schema"]["properties"]["limit"]["maximum"] = 999
        first[0]["output_schema"]["properties"]["tool"]["type"] = "boolean"
        self.assertEqual(tool_contracts()[0]["output_schema"]["properties"]["tool"]["type"], "string")
        with self.assertRaises(ValueError):
            validate_arguments("list_cases", {"limit": 21})

    def test_contract_errors_are_structured_and_do_not_execute_any_action(self):
        snapshot = EvidenceSnapshot(self.good)
        for tool, args in (("approve", {}), ("list_cases", []), ("inspect_case", {}),
                           ("list_cases", {"limit": float("nan")}), ("list_cases", {"limit": object()})):
            response = snapshot.query(tool, args)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "invalid_arguments")
            self.assertIsNone(response["data"])
            self.assertIn("trace", response)

    def test_success_partial_missing_and_error_responses_match_declared_output_shapes(self):
        # Check the JSON Schema vocabulary used by these contracts, without adding a dependency.
        def matches(value, schema):
            if "anyOf" in schema:
                return any(matches(value, choice) for choice in schema["anyOf"])
            if "const" in schema and value != schema["const"]:
                return False
            if "enum" in schema and value not in schema["enum"]:
                return False
            types = schema.get("type")
            if types:
                types = [types] if isinstance(types, str) else types
                allowed = {"object": type(value) is dict, "array": type(value) is list,
                           "integer": type(value) is int, "string": type(value) is str,
                           "boolean": type(value) is bool, "null": value is None}
                if not any(allowed[kind] for kind in types):
                    return False
            if isinstance(value, dict):
                properties = schema.get("properties", {})
                if set(schema.get("required", [])) - set(value):
                    return False
                if schema.get("additionalProperties") is False and set(value) - set(properties):
                    return False
                return all(matches(v, properties[k]) for k, v in value.items() if k in properties)
            if isinstance(value, list):
                return all(matches(item, schema.get("items", {})) for item in value)
            return True
        snapshots = [EvidenceSnapshot(self.good, self.review), EvidenceSnapshot(self.old)]
        with patch("oem_agent.stage3_statistics._list_source_offers", side_effect=sqlite3.OperationalError("Test audit error")):
            snapshots.append(EvidenceSnapshot(execute_stage2_statistics(self.db)))
        for snapshot in snapshots:
            for contract in tool_contracts():
                args = {"inventory_id": 10} if "inventory_id" in contract["input_schema"]["required"] else {}
                for request in (args, {"unexpected": True}):
                    response = snapshot.query(contract["name"], request)
                    with self.subTest(tool=contract["name"], request=request):
                        self.assertTrue(matches(response, contract["output_schema"]))


if __name__ == "__main__":
    unittest.main()
