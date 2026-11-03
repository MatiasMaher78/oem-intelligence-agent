"""Delivery 02: deterministic synthetic OEM/fitment evidence, without DB writes."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .db import read_only_connection
from .harness import Harness
from .stage2_db import (
    CANDIDATE_SQL, MARKETPLACES, OFFER_SQL, list_stage2_candidates,
    list_stage2_offers, validate_day_range,
)

COMPATIBILITY_FIELDS = ("part_name", "brand", "model", "version")


def _compare_text(left, right) -> str:
    if not isinstance(left, str) or not isinstance(right, str) or not left.strip() or not right.strip():
        return "unknown"
    return "equal" if left == right else "different"


def compare_offer(inventory: dict, offer: dict) -> dict:
    """Classify OEM and declared synthetic compatibility on independent axes."""
    evidence = {}
    for field in ("oem", *COMPATIBILITY_FIELDS):
        left, right = inventory.get(field), offer.get(field)
        evidence[field] = {"inventory": left, "offer": right, "comparison": _compare_text(left, right)}
    oem_comparison = evidence["oem"]["comparison"]
    oem_match = "exact" if oem_comparison == "equal" else oem_comparison
    different = [field for field in COMPATIBILITY_FIELDS if evidence[field]["comparison"] == "different"]
    unknown = [field for field in COMPATIBILITY_FIELDS if evidence[field]["comparison"] == "unknown"]
    compatibility = "incompatible" if different else "unknown" if unknown else "confirmed"
    return {
        "offer": dict(offer),
        "oem_match": oem_match,
        "compatibility": compatibility,
        "confirmed_match": oem_match == "exact" and compatibility == "confirmed",
        "field_evidence": evidence,
        "incompatible_fields": different,
        "unknown_fields": unknown,
    }


def _sql_read(harness: Harness, tool: str, fn, *, sql: str, parameters: list, **kwargs):
    """Extend this workflow's trace with SQL metadata and failed read attempts."""
    metadata = {"sql": sql.strip(), "parameters": parameters}
    try:
        result = harness.call(tool, fn, **kwargs)
    except sqlite3.Error as exc:
        harness.events.append({
            "step": harness.calls, "tool": tool,
            "args": {key: value for key, value in kwargs.items() if key != "conn"},
            "result_count": None, "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)}, **metadata,
        })
        raise
    harness.events[-1].update({"status": "ok", **metadata})
    return result


def execute_stage2_matching(db_path: Path, *, min_days: int = 60,
                            max_days: int = 90) -> dict:
    validate_day_range(min_days, max_days)
    identifiers = [identifier for identifier, _ in MARKETPLACES]
    harness = Harness(max_tool_calls=1)
    payload = {
        "demo_only": True, "data_origin": "100% synthetic", "delivery": "stage2-02",
        "source": {"database": str(Path(db_path).resolve()),
                   "inventory_table": "stage2_inventory", "offer_table": "stage2_market_offers"},
        "selection": {"stock_qty_gt": 0, "min_days": min_days, "max_days": max_days, "inclusive": True},
        "marketplaces": identifiers,
        "status": "complete", "complete": True, "total_candidates": None,
        "results": [], "trace": harness.events,
    }
    with closing(read_only_connection(db_path)) as conn:
        try:
            candidates = _sql_read(harness, "list_candidates", list_stage2_candidates,
                                   sql=CANDIDATE_SQL, parameters=[min_days, max_days],
                                   conn=conn, min_days=min_days, max_days=max_days)
        except sqlite3.Error:
            # Unknown inventory coverage is distinct from an empty successful query.
            payload.update({"status": "error", "complete": False})
            return payload
        payload["total_candidates"] = len(candidates)
        harness.max_tool_calls = 1 + len(identifiers) * len(candidates)
        for item in candidates:
            markets = []
            for identifier in identifiers:
                try:
                    offers = _sql_read(harness, "market_evidence", list_stage2_offers,
                                       sql=OFFER_SQL, parameters=[identifier, item["oem"], item["part_name"]],
                                       conn=conn, marketplace_id=identifier,
                                       oem=item["oem"], part_name=item["part_name"])
                except sqlite3.Error as exc:
                    markets.append({"marketplace_id": identifier, "status": "error",
                                    "offers_count": None, "comparisons": [], "confirmed_matches_count": None,
                                    "error": {"type": type(exc).__name__, "message": str(exc)}})
                    payload.update({"status": "partial", "complete": False})
                    continue
                comparisons = [compare_offer(item, offer) for offer in offers]
                markets.append({"marketplace_id": identifier, "status": "ok" if offers else "no_results",
                                "offers_count": len(offers), "comparisons": comparisons,
                                "confirmed_matches_count": sum(row["confirmed_match"] for row in comparisons)})
            matching_complete = all(market["status"] != "error" for market in markets)
            found = any(market["confirmed_matches_count"] for market in markets)
            payload["results"].append({
                "inventory": item, "marketplaces": markets, "matching_complete": matching_complete,
                "has_confirmed_match": True if found else False if matching_complete else None,
                "catalog_status": "not_assessed",
            })
    return payload
