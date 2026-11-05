"""Increment 5.1: immutable local evidence and structured, read-only projections."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .stage2_db import validate_day_range
from .stage2_matching import compare_offer
from .stage3_statistics import StatisticsConfig, analyze_matching
from .stage4_review import dossier_view, fingerprint, read_json, validate_evidence
from .stage5_contracts import CONTRACT_VERSION, MARKETPLACES, validate_arguments


class EvidenceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _validate(evidence: dict) -> None:
    """Reuse audited math and verify its matching/coverage inputs, without changing rules."""
    validate_evidence(evidence)
    try:
        selection = evidence["selection"]
        validate_day_range(selection["min_days"], selection["max_days"])
        if type(selection["stock_qty_gt"]) is not int or selection["stock_qty_gt"] != 0 or selection["inclusive"] is not True:
            raise ValueError("Unsupported selection contract")
        if evidence["marketplaces"] != list(MARKETPLACES):
            raise ValueError("Expected the two synthetic marketplaces")
        for step, event in enumerate(evidence["trace"], 1):
            if (type(event["step"]) is not int or event["step"] != step
                    or event["tool"] not in ("list_candidates", "market_evidence")
                    or not isinstance(event["args"], dict) or not isinstance(event["sql"], str)
                    or not event["sql"].strip() or not isinstance(event["parameters"], list)):
                raise ValueError("Malformed SQL trace")
        cases = evidence["results"]
        for case in cases:
            item = case["inventory"]
            for key in ("oem", "part_name", "brand", "model", "version"):
                if item[key] is not None and not isinstance(item[key], str):
                    raise ValueError("Inventory identity fields must remain text or null")
            if (type(item["stock_qty"]) is not int or item["stock_qty"] <= 0
                    or type(item["days_in_stock"]) is not int
                    or not selection["min_days"] <= item["days_in_stock"] <= selection["max_days"]):
                raise ValueError("Inventory row does not satisfy its recorded selection")
            if [market["marketplace_id"] for market in case["marketplaces"]] != list(MARKETPLACES):
                raise ValueError("Missing or repeated marketplace coverage")
            for market in case["marketplaces"]:
                rows = market["comparisons"]
                if market["status"] == "error":
                    if rows or market["offers_count"] is not None or market["confirmed_matches_count"] is not None:
                        raise ValueError("Source error cannot be represented as an empty success")
                elif (market["status"] != ("ok" if rows else "no_results")
                      or market["offers_count"] != len(rows)
                      or market["confirmed_matches_count"] != sum(row["confirmed_match"] for row in rows)):
                    raise ValueError("Inconsistent source coverage")
                for comparison in rows:
                    if comparison != compare_offer(item, comparison["offer"]):
                        raise ValueError("Matching classification does not match source fields")
            complete = all(market["status"] != "error" for market in case["marketplaces"])
            found = any(market["confirmed_matches_count"] for market in case["marketplaces"])
            expected = True if found else False if complete else None
            if (case["matching_complete"] is not complete or case["has_confirmed_match"] is not expected
                    or case["catalog_status"] != "not_assessed"):
                raise ValueError("Inconsistent matching summary")
        if cases != sorted(cases, key=lambda case: (case["inventory"]["days_in_stock"], case["inventory"]["id"])):
            raise ValueError("Selection order must remain stable")
        failed = any(event["status"] == "error" for event in evidence["trace"])
        complete = evidence["audit_complete"] and not failed and all(case["matching_complete"] for case in cases)
        if evidence["status"] == "error":
            if cases or evidence["total_candidates"] is not None or not failed or evidence["complete"]:
                raise ValueError("Invalid fatal inventory coverage")
        elif evidence["complete"] is not complete or evidence["status"] != ("complete" if complete else "partial"):
            raise ValueError("Inconsistent global coverage")
        catalog = evidence.get("source_offers")
        if evidence["audit_complete"]:
            if not isinstance(catalog, list):
                raise ValueError("Complete source audit requires its original offers")
            encoded = {_encode(offer) for offer in catalog}
            if any(_encode(row["offer"]) not in encoded for case in cases for row in case["offer_audit"]):
                raise ValueError("Matching offers disagree with the complete source catalog")
        rebuilt = analyze_matching(evidence, StatisticsConfig(**{
            "lower_percentile": evidence["statistics_config"]["lower_percentile"],
            "upper_percentile": evidence["statistics_config"]["upper_percentile"],
            "min_sample_size": evidence["statistics_config"]["min_sample_size"]}),
            source_offers=catalog, audit_complete=evidence["audit_complete"])
        if evidence["unassociated_offer_audit"] != rebuilt["unassociated_offer_audit"]:
            raise ValueError("Unassociated source audit does not match the catalog")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed evidence structure") from exc


@dataclass(frozen=True, init=False)
class EvidenceSnapshot:
    """Canonical JSON bytes are immutable; every exposed document/result is a new copy."""
    _evidence: bytes
    _dossier: bytes | None
    _view: bytes | None
    evidence_sha256: str
    snapshot_id: str

    def __init__(self, evidence: dict, dossier: dict | None = None, *, expected_evidence_sha256: str | None = None):
        try:
            # Copy before validation: later mutations of caller-owned objects cannot change this snapshot.
            frozen = _encode(evidence)
            evidence = json.loads(frozen)
            _validate(evidence)
            digest = fingerprint(evidence)
            if expected_evidence_sha256 is not None and expected_evidence_sha256 != digest:
                raise EvidenceError("integrity_error", "Evidence fingerprint does not match the expected value")
            frozen_dossier = _encode(dossier) if dossier is not None else None
            dossier = json.loads(frozen_dossier) if frozen_dossier is not None else None
            view = dossier_view(dossier, evidence) if dossier is not None else None
            identifier = fingerprint({"contract_version": CONTRACT_VERSION, "evidence_sha256": digest,
                                      "dossier_revision_sha256": dossier["revision_sha256"] if dossier else None})
        except EvidenceError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
            raise EvidenceError("invalid_evidence", "Evidence or review failed structural/integrity validation") from exc
        for name, value in (("_evidence", frozen), ("_dossier", frozen_dossier),
                            ("_view", _encode(view) if view is not None else None),
                            ("evidence_sha256", digest), ("snapshot_id", identifier)):
            object.__setattr__(self, name, value)

    @classmethod
    def load(cls, evidence_path: Path, dossier_path: Path | None = None, **kwargs) -> EvidenceSnapshot:
        """Read explicit paths once. No directory discovery, regeneration, writes or DB connections."""
        try:
            evidence = read_json(evidence_path)
            dossier = read_json(dossier_path) if dossier_path is not None else None
        except OSError as exc:
            raise EvidenceError("file_read_error", "Cannot read a requested evidence file") from exc
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise EvidenceError("invalid_json", "Expected strict UTF-8 JSON objects without duplicate keys or nonfinite values") from exc
        return cls(evidence, dossier, **kwargs)

    @property
    def evidence(self) -> dict:
        return json.loads(self._evidence)

    @property
    def dossier(self) -> dict | None:
        return json.loads(self._dossier) if self._dossier is not None else None

    def _ref(self, pointer: str, document: str = "evidence") -> dict:
        return {"document": document, "sha256": self.evidence_sha256 if document == "evidence"
                else self.dossier["revision_sha256"], "pointer": pointer}

    def resolve_reference(self, reference: dict):
        """Resolve hash-bound JSON Pointers inside pinned documents, never filesystem paths."""
        try:
            if set(reference) != {"document", "sha256", "pointer"} or reference["document"] not in ("evidence", "dossier"):
                raise ValueError
            document = self.evidence if reference["document"] == "evidence" else self.dossier
            expected = self.evidence_sha256 if reference["document"] == "evidence" else document["revision_sha256"]
            pointer = reference["pointer"]
            if reference["sha256"] != expected or not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
                raise ValueError
            value = document
            for token in pointer.split("/")[1:] if pointer else []:
                if "~" in token.replace("~0", "").replace("~1", ""):
                    raise ValueError
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    index = int(token)
                    if token != str(index) or index < 0:
                        raise ValueError
                    value = value[index]
                else:
                    value = value[token]
            return value
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise EvidenceError("invalid_reference", "Reference is not present in this snapshot") from exc

    def _case(self, evidence, identifier):
        for index, case in enumerate(evidence["results"]):
            if case["inventory"]["id"] == identifier:
                return index, case
        raise EvidenceError("unknown_inventory_id", "Inventory ID is not selected in this snapshot")

    def _page(self, tool, arguments, rows, references, scope):
        filters = {key: value for key, value in arguments.items() if key not in ("cursor", "limit")}
        def cursor(offset):
            return f"{offset}." + fingerprint({"snapshot_id": self.snapshot_id, "tool": tool, "filters": filters, "offset": offset})
        start = 0
        if "cursor" in arguments:
            try:
                start = int(arguments["cursor"].split(".", 1)[0])
                if not 0 < start < len(rows) or arguments["cursor"] != cursor(start):
                    raise ValueError
            except ValueError as exc:
                raise EvidenceError("invalid_cursor", "Cursor does not belong to this snapshot, tool and filters") from exc
        end = min(start + arguments.get("limit", 20), len(rows))
        return {"scope": scope, "items": rows[start:end], "total_rows": len(rows),
                "returned_rows": end - start, "next_cursor": cursor(end) if end < len(rows) else None}, references[start:end] or [self._ref("")]

    def _read(self, tool, arguments, evidence):
        cases = evidence["results"]
        if "inventory_id" in arguments:
            index, case = self._case(evidence, arguments["inventory_id"])
        if tool == "summarize_run":
            data = {"selection": evidence["selection"], "marketplaces": evidence["marketplaces"],
                    "evidence_status_counts": dict(Counter(case["statistics"]["evidence_status"] for case in cases)),
                    "marketplace_status_counts": dict(Counter(market["status"] for case in cases for market in case["marketplaces"])),
                    "source_offers_count": len(evidence["source_offers"]) if evidence["audit_complete"] else None,
                    "review_available": self._dossier is not None}
            return data, [self._ref("")]
        if tool == "list_cases":
            costs = arguments.get("view") == "costs"
            view = json.loads(self._view) if costs else None
            effective = {row["inventory"]["id"]: row for row in view["cases"]} if costs else {}
            rows, refs = [], []
            for offset, case in enumerate(cases):
                if "oem" in arguments and case["inventory"]["oem"] != arguments["oem"]:
                    continue
                if "evidence_status" in arguments and case["statistics"]["evidence_status"] != arguments["evidence_status"]:
                    continue
                if costs:
                    current = effective[case["inventory"]["id"]]
                    rows.append({"inventory_id": current["inventory"]["id"], **{
                        key: current[key] for key in ("eligible", "block_reasons", "review_state")}})
                else:
                    rows.append({"inventory": case["inventory"], "evidence_status": case["statistics"]["evidence_status"],
                                 "matching_complete": case["matching_complete"], "has_confirmed_match": case["has_confirmed_match"]})
                refs.append(self._ref(f"/results/{offset}"))
            data, refs = self._page(tool, arguments, rows, refs, "selected_cost_review" if costs else "selected_inventory")
            if costs:
                data["needs_refresh"] = view["needs_refresh"]
                refs.append(self._ref("", "dossier"))
            return data, refs
        if tool == "inspect_case":
            section = arguments.get("section", "statistics")
            refs = [self._ref(f"/results/{index}")]
            if section == "inventory":
                content = case["inventory"]
            elif section == "statistics":
                content = {"statistics": case["statistics"], "config": evidence["statistics_config"]}
                refs.append(self._ref("/statistics_config"))
            elif section == "matching":
                content = {key: case[key] for key in ("matching_complete", "has_confirmed_match", "catalog_status")}
                content["marketplaces"] = [{key: value for key, value in market.items() if key != "comparisons"}
                                           for market in case["marketplaces"]]
            else:
                content = {"sql_events": [], "statistics_event": evidence["statistics_trace"][index]}
                refs.append(self._ref(f"/statistics_trace/{index}"))
                for offset, event in enumerate(evidence["trace"]):
                    args = event.get("args", {})
                    if (event["tool"] == "list_candidates" or event.get("scope") == "all_source_offers"
                            or (args.get("oem") == case["inventory"]["oem"] and args.get("part_name") == case["inventory"]["part_name"])):
                        content["sql_events"].append(event)
                        refs.append(self._ref(f"/trace/{offset}"))
            return {"inventory_id": arguments["inventory_id"], "section": section, "content": content}, refs
        if tool == "list_offers":
            rows, refs = [], []
            for offset, case in enumerate(cases):
                identifier = case["inventory"]["id"]
                if "inventory_id" in arguments and identifier != arguments["inventory_id"]:
                    continue
                for audit_index, row in enumerate(case["offer_audit"]):
                    rows.append({"inventory_id": identifier, "audit": row})
                    refs.append(self._ref(f"/results/{offset}/offer_audit/{audit_index}"))
            if "inventory_id" not in arguments:
                for offset, row in enumerate(evidence["unassociated_offer_audit"]):
                    rows.append({"inventory_id": None, "audit": row})
                    refs.append(self._ref(f"/unassociated_offer_audit/{offset}"))
            if "marketplace_id" in arguments:
                pairs = [(row, ref) for row, ref in zip(rows, refs)
                         if row["audit"]["source_marketplace_id"] == arguments["marketplace_id"]]
                rows, refs = [row for row, _ in pairs], [ref for _, ref in pairs]
            return self._page(tool, arguments, rows, refs, "offer_audit_rows_not_unique_advertisements")
        view = json.loads(self._view)
        effective = next(row for row in view["cases"] if row["inventory"]["id"] == arguments["inventory_id"])
        decision = next((row for row in view["decisions"] if row["decision_id"] == effective["current_decision_id"]), None)
        return {"dossier_id": view["dossier_id"], "revision": view["revision"], "needs_refresh": view["needs_refresh"],
                "case": effective, "current_decision": decision}, [self._ref("", "dossier"), self._ref("")]

    def query(self, tool: str, arguments: dict | None = None) -> dict:
        """Exercise a read contract directly. Agent/harness orchestration belongs to later increments."""
        evidence = self.evidence
        request = {} if arguments is None else arguments
        # Non-JSON callers get a structured error without leaking arbitrary object representations.
        try:
            request = json.loads(_encode(request))
        except (TypeError, ValueError, OverflowError, RecursionError):
            request = {"invalid_argument_type": type(request).__name__}
        response = {"contract_version": CONTRACT_VERSION, "tool": tool, "snapshot_id": self.snapshot_id,
                    "evidence_sha256": self.evidence_sha256,
                    "coverage": {key: evidence[key] for key in ("status", "complete", "audit_complete", "total_candidates")},
                    "status": "ok" if evidence["complete"] else "partial", "data": None,
                    "references": [], "warnings": [] if evidence["complete"] else ["incomplete_source_coverage"], "error": None}
        try:
            try:
                validate_arguments(tool, request)
            except (ValueError, TypeError) as exc:
                raise EvidenceError("invalid_arguments", str(exc)) from exc
            costs = tool == "list_cases" and request.get("view") == "costs"
            if (tool == "review_status" or costs) and self._view is None:
                if tool == "review_status":
                    self._case(evidence, request["inventory_id"])
                response.update({"status": "not_available", "warnings": [*response["warnings"], "review_not_provided"]})
            else:
                response["data"], response["references"] = self._read(tool, request, evidence)
                if (tool == "review_status" or costs) and response["data"]["needs_refresh"]:
                    response["warnings"].append("review_evidence_changed")
        except EvidenceError as exc:
            response.update({"status": "error", "data": None, "references": [],
                             "error": {"code": exc.code, "message": str(exc)}})
        response["trace"] = {"operation": tool, "arguments": request, "status": response["status"],
                             "request_sha256": fingerprint({"tool": tool, "arguments": request}),
                             "result_sha256": fingerprint(response)}
        return response
