"""Delivery 04: DEMO cost gates, immutable decisions and stale-evidence handling."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from oem_agent.stage2_db import seed_stage2
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import StatisticsConfig, analyze_matching, execute_stage2_statistics
from oem_agent.stage4_review import (DemoCostConfig, create_dossier, dossier_view, fingerprint,
                                     read_json, record_decision, refresh_dossier, validate_dossier,
                                     validate_evidence, write_dossier)

AT = "2026-11-03T12:00:00+00:00"


class DemoReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.database = Path(cls.fixtures.name) / "statistics.sqlite"
        seed_statistics_demo(cls.database)
        cls.good = execute_stage2_statistics(cls.database)
        old = Path(cls.fixtures.name) / "old.sqlite"
        seed_stage2(old)
        cls.insufficient = execute_stage2_statistics(old)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = copy.deepcopy(self.good)
        self.dossier = create_dossier(self.evidence, dossier_id="TEST-DOSSIER", at=AT)

    def case(self, value, identifier=10):
        return next(case for case in value["cases"] if case["inventory"]["id"] == identifier)

    def decide(self, dossier=None, **changes):
        values = {"inventory_id": 10, "state": "approved", "reviewer": "TEST-REVIEWER",
                  "reason": "Automated test fixture; not a real human approval", "decision_id": "TEST-D1", "at": AT}
        values.update(changes)
        return record_decision(dossier or self.dossier, self.evidence, **values)

    def changed_evidence(self):
        changed = copy.deepcopy(self.evidence)
        for case in changed["results"]:
            for market in case["marketplaces"]:
                for comparison in market["comparisons"]:
                    offer = comparison["offer"]
                    if offer["marketplace_id"] == "DEMO-MARKET-A" and offer["offer_id"] == "STATS-01":
                        offer["price_cents"] += 100
        for offer in changed["source_offers"]:
            if offer["marketplace_id"] == "DEMO-MARKET-A" and offer["offer_id"] == "STATS-01":
                offer["price_cents"] += 100
        return analyze_matching(changed, StatisticsConfig(), source_offers=changed["source_offers"], audit_complete=True)

    def test_cost_formula_is_invented_exact_and_pending_by_default(self):
        self.assertEqual(len(self.dossier["cases"]), 27)
        self.assertTrue(all(case["review_state"] == "pending" for case in self.dossier["cases"]))
        self.assertFalse(any(case["ready_for_demo_use"] for case in self.dossier["cases"]))
        eligible = [case for case in self.dossier["cases"] if case["eligible"]]
        self.assertEqual([case["inventory"]["id"] for case in eligible], [10])
        estimate = eligible[0]["demo_estimate"]
        self.assertEqual((estimate["unit_cost_cents"], estimate["total_cost_cents"]), (250, 250))
        self.assertEqual(estimate["label"], "DEMO")
        self.assertEqual(estimate["reference_band_cents"], {"min": 2000, "max": 9000})
        self.assertNotIn("recommended_price", estimate)

    def test_parameters_are_explicit_and_zero_cost_is_not_missing(self):
        document = create_dossier(self.evidence, DemoCostConfig(10, 20, 30, 4), dossier_id="PARAMS", at=AT)
        self.assertEqual(self.case(document)["demo_estimate"]["total_cost_cents"], 240)
        zero = create_dossier(self.evidence, DemoCostConfig(0, 0, 0, 1), at=AT)
        self.assertEqual(self.case(zero)["demo_estimate"]["unit_cost_cents"], 0)
        self.assertIsNone(self.case(zero, 2)["demo_estimate"])
        for values in ((-1, 0, 0, 1), (True, 0, 0, 1), (1.5, 0, 0, 1), (0, 0, 0, 0), (0, 0, 0, True)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DemoCostConfig(*values)

    def test_insufficient_evidence_cannot_produce_estimates_or_be_approved(self):
        document = create_dossier(self.insufficient, at=AT)
        self.assertTrue(all(case["demo_estimate"] is None for case in document["cases"]))
        with self.assertRaisesRegex(ValueError, "Cannot approve"):
            record_decision(document, self.insufficient, inventory_id=2, state="approved", reviewer="TEST",
                            reason="Test-only attempted approval", at=AT)

    def test_source_errors_block_all_demo_estimates(self):
        partial = analyze_matching(self.evidence, StatisticsConfig(), source_offers=self.evidence["source_offers"], audit_complete=False)
        partial.update({"status": "partial", "complete": False})
        document = create_dossier(partial, at=AT)
        self.assertFalse(any(case["eligible"] for case in document["cases"]))
        self.assertTrue(all(case["demo_estimate"] is None for case in document["cases"]))
        self.assertEqual(dossier_view(document, partial)["evidence_status"], "partial")

    def test_forged_or_missing_statistics_are_rejected_before_calculating(self):
        forged = copy.deepcopy(self.evidence)
        next(case for case in forged["results"] if case["inventory"]["id"] == 10)["statistics"]["filtered_min_price_cents"] = 1
        with self.assertRaisesRegex(ValueError, "Statistics do not match"):
            create_dossier(forged)
        missing = copy.deepcopy(self.evidence)
        del missing["statistics_config"]
        with self.assertRaises(ValueError):
            create_dossier(missing)
        unsupported = {**self.evidence, "data_origin": "real data"}
        with self.assertRaises(ValueError):
            create_dossier(unsupported)

    def test_all_four_review_states_and_withdrawal_are_auditable(self):
        approved = self.decide()
        self.assertTrue(self.case(approved)["ready_for_demo_use"])
        for index, state in enumerate(("rejected", "review_required", "pending"), 2):
            approved = self.decide(approved, state=state, decision_id=f"TEST-D{index}")
            self.assertEqual(self.case(approved)["review_state"], state)
            self.assertFalse(self.case(approved)["ready_for_demo_use"])
        self.assertEqual(len(approved["decisions"]), 4)
        validate_dossier(approved)

    def test_decision_contains_identity_utc_actor_reason_and_evidence_links(self):
        approved = self.decide(at="2026-11-03T09:00:00-03:00")
        decision = approved["decisions"][0]
        self.assertEqual(decision["decision_id"], "TEST-D1")
        self.assertEqual(decision["recorded_at"], AT)
        self.assertEqual(decision["reviewer"], "TEST-REVIEWER")
        self.assertTrue(decision["reason"])
        self.assertEqual(decision["evidence_sha256"], fingerprint(self.evidence))
        self.assertEqual(decision["basis_sha256"], approved["basis_sha256"])
        self.assertEqual(approved["parent_revision_sha256"], self.dossier["revision_sha256"])

    def test_blank_metadata_invalid_dates_unknown_ids_and_duplicate_decisions_are_rejected(self):
        for changes in ({"reviewer": " "}, {"reason": ""}, {"decision_id": ""}, {"at": "2026-11-03"},
                        {"at": "bad-date"}, {"state": "automatic"}, {"inventory_id": True}, {"inventory_id": 999}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.decide(**changes)
        approved = self.decide()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.decide(approved)

    def test_changed_evidence_invalidates_approval_even_before_refresh(self):
        approved = self.decide()
        changed = self.changed_evidence()
        validate_evidence(changed)
        view = dossier_view(approved, changed)
        self.assertTrue(view["needs_refresh"])
        self.assertEqual(self.case(view)["review_state"], "review_required")
        self.assertFalse(self.case(view)["ready_for_demo_use"])
        with self.assertRaisesRegex(ValueError, "refresh"):
            record_decision(approved, changed, inventory_id=10, state="approved", reviewer="TEST", reason="Test")
        self.assertEqual(self.case(approved)["review_state"], "approved")  # Historical revision is preserved.

    def test_refresh_retains_history_and_requires_a_new_human_decision(self):
        approved = self.decide()
        changed = self.changed_evidence()
        refreshed = refresh_dossier(approved, changed, at=AT)
        self.assertEqual(refreshed["decisions"], approved["decisions"])
        self.assertEqual(refreshed["trace"][-1]["invalidated_approval_ids"], ["TEST-D1"])
        self.assertEqual(self.case(refreshed)["review_state"], "review_required")
        self.assertEqual(refreshed["review_epoch"], 2)
        renewed = record_decision(refreshed, changed, inventory_id=10, state="approved", reviewer="TEST-REVIEWER",
                                  reason="Test-only new review", decision_id="TEST-D2", at=AT)
        self.assertTrue(self.case(renewed)["ready_for_demo_use"])
        self.assertEqual(len(renewed["decisions"]), 2)

    def test_reverting_evidence_does_not_resurrect_an_invalidated_approval(self):
        approved = self.decide()
        changed = refresh_dossier(approved, self.changed_evidence(), at=AT)
        reverted = refresh_dossier(changed, self.evidence, at=AT)
        self.assertEqual(reverted["evidence_sha256"], approved["evidence_sha256"])
        self.assertEqual(reverted["review_epoch"], 3)
        self.assertEqual(self.case(reverted)["review_state"], "review_required")
        self.assertFalse(self.case(reverted)["ready_for_demo_use"])

    def test_parameter_changes_invalidate_approval_and_identical_refresh_does_not(self):
        approved = self.decide()
        unchanged = refresh_dossier(approved, self.evidence, at=AT)
        self.assertEqual(unchanged["review_epoch"], 1)
        self.assertTrue(self.case(unchanged)["ready_for_demo_use"])
        changed = refresh_dossier(approved, self.evidence, DemoCostConfig(121, 80, 50, 1), at=AT)
        self.assertEqual(changed["evidence_sha256"], approved["evidence_sha256"])
        self.assertNotEqual(changed["basis_sha256"], approved["basis_sha256"])
        self.assertEqual(self.case(changed)["demo_estimate"]["unit_cost_cents"], 251)
        self.assertFalse(self.case(changed)["ready_for_demo_use"])

    def test_hashes_ignore_json_object_order_and_whitespace_but_cover_source_details(self):
        reordered = json.loads(json.dumps(self.evidence, sort_keys=True, indent=4))
        self.assertEqual(fingerprint(reordered), fingerprint(self.evidence))
        changed = copy.deepcopy(self.evidence)
        changed["source"]["database"] = "another-synthetic-snapshot.sqlite"
        self.assertNotEqual(fingerprint(changed), fingerprint(self.evidence))
        self.assertTrue(dossier_view(self.decide(), changed)["needs_refresh"])

    def test_input_and_prior_revisions_and_sqlite_database_are_unchanged(self):
        before_data = hashlib.sha256(self.database.read_bytes()).hexdigest()
        before_evidence = copy.deepcopy(self.evidence)
        before_dossier = copy.deepcopy(self.dossier)
        approved = self.decide()
        refresh_dossier(approved, self.changed_evidence(), at=AT)
        self.assertEqual(self.evidence, before_evidence)
        self.assertEqual(self.dossier, before_dossier)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before_data)
        again = create_dossier(self.evidence, dossier_id="TEST-DOSSIER", at=AT)
        self.assertEqual(again, self.dossier)

    def test_revision_and_decision_integrity_and_materialized_state_are_checked(self):
        approved = self.decide()
        altered = copy.deepcopy(approved)
        altered["decisions"][0]["reason"] = "silently modified"
        with self.assertRaises(ValueError):
            validate_dossier(altered)
        altered["revision_sha256"] = fingerprint({key: value for key, value in altered.items() if key != "revision_sha256"})
        with self.assertRaisesRegex(ValueError, "chain"):
            validate_dossier(altered)
        materialized = copy.deepcopy(self.dossier)
        self.case(materialized).update({"review_state": "approved", "ready_for_demo_use": True})
        materialized["revision_sha256"] = fingerprint({key: value for key, value in materialized.items() if key != "revision_sha256"})
        with self.assertRaisesRegex(ValueError, "Materialized"):
            validate_dossier(materialized)

    def test_local_revisions_are_exclusive_and_reject_invalid_outputs(self):
        first = Path(self.tmp.name) / "v1.review.json"
        second = Path(self.tmp.name) / "v2.review.json"
        write_dossier(first, self.dossier)
        before = first.read_bytes()
        approved = self.decide()
        write_dossier(second, approved)
        self.assertEqual(read_json(second), approved)
        with self.assertRaises(FileExistsError):
            write_dossier(first, approved)
        self.assertEqual(first.read_bytes(), before)
        bad = Path(self.tmp.name) / "not-json.sqlite"
        with self.assertRaises(ValueError):
            write_dossier(bad, approved)
        self.assertFalse(bad.exists())

    def test_reader_rejects_duplicate_keys_nonfinite_values_and_nonobjects(self):
        path = Path(self.tmp.name) / "bad.json"
        for content in ('{"x": 1, "x": 2}', '{"x": NaN}', '{"x": Infinity}', '[]'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_json(path)

    def test_concurrent_output_creation_is_preserved(self):
        path = Path(self.tmp.name) / "race.review.json"
        original_open = Path.open
        def race(target, mode="r", *args, **kwargs):
            if target == path and mode == "xb":
                with original_open(target, "wb") as stream:
                    stream.write(b"another writer")
            return original_open(target, mode, *args, **kwargs)
        with patch.object(Path, "open", race), self.assertRaises(FileExistsError):
            write_dossier(path, self.dossier)
        self.assertEqual(path.read_bytes(), b"another writer")

    def test_cli_rejects_malformed_dossier_without_creating_an_output(self):
        evidence = Path(self.tmp.name) / "evidence.json"
        evidence.write_text(json.dumps(self.evidence), encoding="utf-8")
        dossier = Path(self.tmp.name) / "bad.review.json"
        malformed = copy.deepcopy(self.dossier)
        malformed["cost_config"]["unsupported"] = 1
        dossier.write_text(json.dumps(malformed), encoding="utf-8")
        output = Path(self.tmp.name) / "new.review.json"
        result = subprocess.run([sys.executable, "-m", "oem_agent.stage4", "refresh", "--dossier", str(dossier),
                                 "--evidence", str(evidence), "--out", str(output)],
                                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(output.exists())

    def test_cli_create_decide_stale_show_refresh_and_reapproval(self):
        cwd = Path(__file__).resolve().parents[1]
        evidence_path = Path(self.tmp.name) / "evidence.json"
        evidence_path.write_text(json.dumps(self.evidence), encoding="utf-8")
        paths = [Path(self.tmp.name) / f"v{index}.review.json" for index in range(1, 5)]
        base = [sys.executable, "-m", "oem_agent.stage4"]
        def run(command, *arguments):
            return subprocess.run([*base, command, "--evidence", str(evidence_path), *arguments],
                                  cwd=cwd, text=True, capture_output=True)
        created = run("create", "--out", str(paths[0]), "--dossier-id", "CLI-TEST", "--at", AT)
        self.assertEqual(created.returncode, 0, created.stderr)
        args = ["--inventory-id", "10", "--state", "approved", "--reviewer", "TEST-REVIEWER",
                "--reason", "Automated fixture only", "--at", AT]
        approved = run("decide", "--dossier", str(paths[0]), "--out", str(paths[1]), "--decision-id", "CLI-D1", *args)
        self.assertEqual(approved.returncode, 0, approved.stderr)
        self.assertEqual(json.loads(approved.stdout)["ready_for_demo_use_count"], 1)
        evidence_path.write_text(json.dumps(self.changed_evidence()), encoding="utf-8")
        stale = run("show", "--dossier", str(paths[1]))
        self.assertEqual(stale.returncode, 1)
        self.assertEqual(json.loads(stale.stdout)["ready_for_demo_use_count"], 0)
        rejected = run("decide", "--dossier", str(paths[1]), "--out", str(paths[2]), *args)
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(paths[2].exists())
        refreshed = run("refresh", "--dossier", str(paths[1]), "--out", str(paths[2]), "--at", AT)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertEqual(self.case(json.loads(refreshed.stdout))["review_state"], "review_required")
        renewed = run("decide", "--dossier", str(paths[2]), "--out", str(paths[3]), "--decision-id", "CLI-D2", *args)
        self.assertEqual(renewed.returncode, 0, renewed.stderr)
        self.assertEqual(json.loads(renewed.stdout)["ready_for_demo_use_count"], 1)
        before = paths[3].read_bytes()
        duplicate_output = run("refresh", "--dossier", str(paths[3]), "--out", str(paths[3]), "--at", AT)
        self.assertEqual(duplicate_output.returncode, 2)
        self.assertEqual(paths[3].read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
