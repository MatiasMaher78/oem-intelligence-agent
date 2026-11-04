"""DEMO-only invented costs and immutable, evidence-bound local review revisions."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .stage3_statistics import StatisticsConfig, analyze_matching

RULE_ID = "DEMO-COST-001"
STATES = ("pending", "approved", "rejected", "review_required")


def fingerprint(value) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value.strip()


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _stamp(value: str | None = None) -> str:
    instant = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone offset")
    return instant.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class DemoCostConfig:
    handling_cents: int = 120
    packaging_cents: int = 80
    inspection_cents: int = 50
    demo_units: int = 1

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for key, value in asdict(self).items() if key != "demo_units"):
            raise ValueError("DEMO cost components must be non-negative integer cents")
        if not _positive_int(self.demo_units):
            raise ValueError("DEMO units must be a positive integer")


def validate_evidence(evidence: dict) -> None:
    """Verify the structured Delivery 03 contract and recompute its audited statistics."""
    try:
        if (evidence["delivery"] != "stage2-03" or evidence["demo_only"] is not True
                or evidence["data_origin"] != "100% synthetic"):
            raise ValueError("Only synthetic Delivery 03 evidence is supported")
        if evidence["status"] not in ("complete", "partial", "error"):
            raise ValueError("Invalid evidence coverage status")
        if type(evidence["complete"]) is not bool or type(evidence["audit_complete"]) is not bool:
            raise ValueError("Coverage flags must be explicit booleans")
        if not isinstance(evidence["results"], list) or not isinstance(evidence["trace"], list):
            raise ValueError("Evidence must contain structured results and trace")
        if not evidence["trace"] or any(event["status"] not in ("ok", "error") for event in evidence["trace"]):
            raise ValueError("Evidence must include SQL read status")
        identifiers = [case["inventory"]["id"] for case in evidence["results"]]
        if any(not _positive_int(identifier) for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("Inventory identities must be unique positive integers")
        total = evidence["total_candidates"]
        if total is None and (evidence["status"] != "error" or identifiers):
            raise ValueError("Unknown inventory coverage is only valid for a fatal read error")
        if total is not None and (type(total) is not int or total < 0 or total != len(identifiers)):
            raise ValueError("Candidate coverage does not match results")
        parameters = evidence["statistics_config"]
        config = StatisticsConfig(parameters["lower_percentile"], parameters["upper_percentile"], parameters["min_sample_size"])
        if parameters != config.as_dict():
            raise ValueError("Unsupported statistics method or currency")
        rebuilt = analyze_matching(evidence, config, source_offers=evidence.get("source_offers"),
                                   audit_complete=evidence["audit_complete"])
        for case, expected in zip(evidence["results"], rebuilt["results"]):
            if case["statistics"] != expected["statistics"] or case["offer_audit"] != expected["offer_audit"]:
                raise ValueError("Statistics do not match audited matching evidence")
        if evidence["statistics_trace"] != rebuilt["statistics_trace"]:
            raise ValueError("Statistical trace does not match evidence")
        fingerprint(evidence)  # Reject unsupported or nonfinite JSON values.
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Incomplete or malformed Delivery 03 evidence") from exc


def _basis(evidence_sha256: str, config: DemoCostConfig) -> str:
    return fingerprint({"evidence_sha256": evidence_sha256, "cost_config": asdict(config), "rule_id": RULE_ID})


def _cases(evidence: dict, config: DemoCostConfig, decisions: list[dict], epoch: int, basis: str) -> list[dict]:
    result = []
    evidence_hash = fingerprint(evidence)
    source_ok = (evidence["complete"] is True and evidence["status"] == "complete"
                 and evidence["audit_complete"] is True and all(event["status"] == "ok" for event in evidence["trace"]))
    minimum = evidence["statistics_config"]["min_sample_size"]
    for case in evidence["results"]:
        item, stats = case["inventory"], case["statistics"]
        reasons = []
        if not source_ok or not case["matching_complete"] or stats["coverage_complete"] is not True:
            reasons.append("incomplete_source_coverage")
        if stats["evidence_status"] != "sufficient_sample_demo":
            reasons.append("insufficient_or_unusable_statistical_evidence")
        if (stats["percentile_filter_applied"] is not True or stats["observed_offers_count"] < minimum
                or not _positive_int(stats["filtered_offers_count"]) or not _positive_int(stats["filtered_units"])
                or not _positive_int(stats["filtered_min_price_cents"]) or not _positive_int(stats["filtered_max_price_cents"])):
            reasons.append("missing_filtered_evidence")
        estimate = None
        if not reasons:
            unit = config.handling_cents + config.packaging_cents + config.inspection_cents
            estimate = {"label": "DEMO", "rule_id": RULE_ID, "currency": "EUR",
                        "unit_cost_cents": unit, "demo_units": config.demo_units,
                        "total_cost_cents": unit * config.demo_units,
                        "reference_band_cents": {"min": stats["filtered_min_price_cents"],
                                                 "max": stats["filtered_max_price_cents"]}}
        history = [decision for decision in decisions if decision["inventory_id"] == item["id"]]
        applicable = [decision for decision in history if decision["review_epoch"] == epoch
                      and decision["basis_sha256"] == basis
                      and decision["evidence_sha256"] == evidence_hash]
        state = applicable[-1]["state"] if applicable else "review_required" if history else "pending"
        if state == "approved" and reasons:
            state = "review_required"
        result.append({"inventory": deepcopy(item), "eligible": not reasons, "block_reasons": reasons,
                       "demo_estimate": estimate, "review_state": state,
                       "current_decision_id": applicable[-1]["decision_id"] if applicable else None,
                       "ready_for_demo_use": not reasons and state == "approved"})
    return result


def _seal(dossier: dict) -> dict:
    dossier.pop("revision_sha256", None)
    config = DemoCostConfig(**dossier["cost_config"])
    dossier["cases"] = _cases(dossier["evidence"], config, dossier["decisions"], dossier["review_epoch"], dossier["basis_sha256"])
    dossier["revision_sha256"] = fingerprint(dossier)
    return dossier


def validate_dossier(dossier: dict) -> None:
    try:
        if (dossier["kind"] != "oem_stage4_demo_review" or type(dossier["schema_version"]) is not int
                or dossier["schema_version"] != 1 or dossier["demo_only"] is not True or dossier["rule_id"] != RULE_ID):
            raise ValueError("Unsupported DEMO dossier format")
        _text(dossier["dossier_id"], "Dossier ID")
        if not _positive_int(dossier["revision"]) or not _positive_int(dossier["review_epoch"]):
            raise ValueError("Invalid revision or review epoch")
        if ((dossier["revision"] == 1 and dossier["parent_revision_sha256"] is not None)
                or (dossier["revision"] > 1 and not _is_sha256(dossier["parent_revision_sha256"]))):
            raise ValueError("Invalid parent revision fingerprint")
        if set(dossier["cost_config"]) != set(asdict(DemoCostConfig())):
            raise ValueError("DEMO parameters must be explicit")
        unsigned = {key: value for key, value in dossier.items() if key != "revision_sha256"}
        if fingerprint(unsigned) != dossier["revision_sha256"]:
            raise ValueError("Dossier revision fingerprint mismatch")
        validate_evidence(dossier["evidence"])
        config = DemoCostConfig(**dossier["cost_config"])
        if dossier["evidence_sha256"] != fingerprint(dossier["evidence"]):
            raise ValueError("Evidence fingerprint mismatch")
        if dossier["basis_sha256"] != _basis(dossier["evidence_sha256"], config):
            raise ValueError("Review basis fingerprint mismatch")
        seen, previous, previous_epoch = set(), None, 1
        for sequence, decision in enumerate(dossier["decisions"], 1):
            for field in ("decision_id", "reviewer", "reason"):
                _text(decision[field], field)
            if (decision["decision_id"] in seen or decision["sequence"] != sequence
                    or decision["state"] not in STATES or not _positive_int(decision["inventory_id"])
                    or not _positive_int(decision["review_epoch"])
                    or not previous_epoch <= decision["review_epoch"] <= dossier["review_epoch"]
                    or not _is_sha256(decision["evidence_sha256"]) or not _is_sha256(decision["basis_sha256"])):
                raise ValueError("Invalid or duplicate decision record")
            if decision["review_epoch"] == dossier["review_epoch"] and (
                    decision["basis_sha256"] != dossier["basis_sha256"]
                    or decision["evidence_sha256"] != dossier["evidence_sha256"]
                    or decision["inventory_id"] not in {case["inventory"]["id"] for case in dossier["evidence"]["results"]}):
                raise ValueError("Current decision does not match the review basis")
            if _stamp(decision["recorded_at"]) != decision["recorded_at"]:
                raise ValueError("Decision timestamp must be normalized UTC")
            unsigned_decision = {key: value for key, value in decision.items() if key != "decision_sha256"}
            if (decision["previous_decision_sha256"] != previous
                    or decision["decision_sha256"] != fingerprint(unsigned_decision)):
                raise ValueError("Decision chain fingerprint mismatch")
            seen.add(decision["decision_id"])
            previous, previous_epoch = decision["decision_sha256"], decision["review_epoch"]
        if len(dossier["trace"]) != dossier["revision"]:
            raise ValueError("Revision trace is incomplete")
        if [event["revision"] for event in dossier["trace"]] != list(range(1, dossier["revision"] + 1)):
            raise ValueError("Revision trace order is invalid")
        if dossier["cases"] != _cases(dossier["evidence"], config, dossier["decisions"], dossier["review_epoch"], dossier["basis_sha256"]):
            raise ValueError("Materialized review state does not match decisions and evidence")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Incomplete or malformed review dossier") from exc


def create_dossier(evidence: dict, config: DemoCostConfig | None = None, *, dossier_id: str | None = None,
                    at: str | None = None) -> dict:
    validate_evidence(evidence)
    config = config or DemoCostConfig()
    evidence_hash = fingerprint(evidence)
    dossier = {"kind": "oem_stage4_demo_review", "schema_version": 1, "demo_only": True,
               "dossier_id": _text(uuid4().hex if dossier_id is None else dossier_id, "Dossier ID"), "revision": 1, "review_epoch": 1,
               "parent_revision_sha256": None, "rule_id": RULE_ID, "cost_config": asdict(config),
               "evidence": deepcopy(evidence), "evidence_sha256": evidence_hash,
               "basis_sha256": _basis(evidence_hash, config), "decisions": [], "trace": []}
    dossier["trace"].append({"revision": 1, "action": "created", "recorded_at": _stamp(at),
                             "basis_sha256": dossier["basis_sha256"]})
    return _seal(dossier)


def dossier_view(dossier: dict, current_evidence: dict) -> dict:
    validate_dossier(dossier)
    validate_evidence(current_evidence)
    current_hash = fingerprint(current_evidence)
    stale = current_hash != dossier["evidence_sha256"]
    config = DemoCostConfig(**dossier["cost_config"])
    cases = _cases(current_evidence, config, dossier["decisions"], dossier["review_epoch"] + int(stale), _basis(current_hash, config))
    return {"dossier_id": dossier["dossier_id"], "revision": dossier["revision"], "demo_only": True,
            "evidence_status": current_evidence["status"], "evidence_complete": current_evidence["complete"],
            "total_candidates": current_evidence["total_candidates"],
            "needs_refresh": stale, "evidence_sha256": current_hash, "stored_evidence_sha256": dossier["evidence_sha256"],
            "cases": cases, "eligible_count": sum(case["eligible"] for case in cases),
            "ready_for_demo_use_count": sum(case["ready_for_demo_use"] for case in cases),
            "decisions": deepcopy(dossier["decisions"])}


def _next_revision(dossier: dict) -> dict:
    updated = deepcopy(dossier)
    updated["revision"] += 1
    updated["parent_revision_sha256"] = dossier["revision_sha256"]
    return updated


def record_decision(dossier: dict, current_evidence: dict, *, inventory_id: int, state: str,
                     reviewer: str, reason: str, decision_id: str | None = None, at: str | None = None) -> dict:
    view = dossier_view(dossier, current_evidence)
    if view["needs_refresh"]:
        raise ValueError("Evidence changed; refresh to a new dossier revision before recording a decision")
    if state not in STATES or not _positive_int(inventory_id):
        raise ValueError("Invalid review state or inventory ID")
    case = next((case for case in view["cases"] if case["inventory"]["id"] == inventory_id), None)
    if case is None:
        raise ValueError("Inventory ID is not present in the evidence")
    if state == "approved" and not case["eligible"]:
        raise ValueError("Cannot approve blocked or insufficient evidence")
    identifier = _text(uuid4().hex if decision_id is None else decision_id, "Decision ID")
    if any(decision["decision_id"] == identifier for decision in dossier["decisions"]):
        raise ValueError("Decision ID already exists")
    updated = _next_revision(dossier)
    decision = {"sequence": len(updated["decisions"]) + 1, "decision_id": identifier, "inventory_id": inventory_id,
                "state": state, "reviewer": _text(reviewer, "Reviewer"), "reason": _text(reason, "Reason"),
                "recorded_at": _stamp(at), "review_epoch": updated["review_epoch"],
                "evidence_sha256": updated["evidence_sha256"], "basis_sha256": updated["basis_sha256"],
                "previous_decision_sha256": updated["decisions"][-1]["decision_sha256"] if updated["decisions"] else None}
    decision["decision_sha256"] = fingerprint(decision)
    updated["decisions"].append(decision)
    updated["trace"].append({"revision": updated["revision"], "action": "decision_recorded",
                             "decision_id": identifier, "recorded_at": decision["recorded_at"],
                             "basis_sha256": updated["basis_sha256"]})
    return _seal(updated)


def refresh_dossier(dossier: dict, evidence: dict, config: DemoCostConfig | None = None, *, at: str | None = None) -> dict:
    validate_dossier(dossier)
    validate_evidence(evidence)
    config = config or DemoCostConfig(**dossier["cost_config"])
    updated = _next_revision(dossier)
    new_hash = fingerprint(evidence)
    new_basis = _basis(new_hash, config)
    changed = new_basis != dossier["basis_sha256"]
    updated.update({"evidence": deepcopy(evidence), "evidence_sha256": new_hash,
                    "cost_config": asdict(config), "basis_sha256": new_basis,
                    "review_epoch": dossier["review_epoch"] + int(changed)})
    invalidated = [case["current_decision_id"] for case in dossier["cases"] if case["review_state"] == "approved"] if changed else []
    updated["trace"].append({"revision": updated["revision"], "action": "evidence_refreshed", "recorded_at": _stamp(at),
                             "basis_changed": changed, "previous_basis_sha256": dossier["basis_sha256"],
                             "basis_sha256": new_basis, "invalidated_approval_ids": invalidated})
    return _seal(updated)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    def reject_constant(value):
        raise ValueError("Nonfinite JSON values are not supported")
    result = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def write_dossier(path: Path, dossier: dict) -> None:
    validate_dossier(dossier)
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise ValueError("Review revisions must use a .json extension")
    content = json.dumps(dossier, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
