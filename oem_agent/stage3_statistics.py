"""Delivery 03: exact, demonstrative statistics over audited matching evidence."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
import sqlite3
from contextlib import closing

from .db import read_only_connection
from .harness import Harness
from .stage2_matching import _sql_read, execute_stage2_matching

AUDIT_OFFERS_SQL = """
    SELECT marketplace_id, offer_id, oem, part_name, brand, model, version,
           available_units, price_cents, observed_on
    FROM stage2_market_offers
    ORDER BY marketplace_id ASC, offer_id ASC
"""


def _list_source_offers(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(AUDIT_OFFERS_SQL).fetchall()]


def _percent(value) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError("Percentiles must be finite numbers")
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Percentiles must be finite numbers") from exc
    if not result.is_finite() or not 0 <= result <= 100:
        raise ValueError("Percentiles must be between 0 and 100")
    return result


@dataclass(frozen=True)
class StatisticsConfig:
    lower_percentile: Decimal = Decimal("10")
    upper_percentile: Decimal = Decimal("90")
    min_sample_size: int = 10

    def __post_init__(self):
        object.__setattr__(self, "lower_percentile", _percent(self.lower_percentile))
        object.__setattr__(self, "upper_percentile", _percent(self.upper_percentile))
        if self.lower_percentile >= self.upper_percentile:
            raise ValueError("Lower percentile must be smaller than upper percentile")
        if type(self.min_sample_size) is not int or self.min_sample_size < 1:
            raise ValueError("Minimum sample size must be a positive integer")

    def as_dict(self) -> dict:
        return {"lower_percentile": format(self.lower_percentile, "f"),
                "upper_percentile": format(self.upper_percentile, "f"),
                "min_sample_size": self.min_sample_size,
                "method": "linear_h=(n-1)*p/100", "band_inclusive": True,
                "sample_weighting": "one_per_unique_offer", "currency": "EUR"}


def linear_percentile(prices_cents: list[int], percentile) -> Fraction:
    """Sorted x: h=(n-1)*p/100; x[floor(h)] + frac(h)*(x[ceil(h)]-x[floor(h)])."""
    if not prices_cents or any(type(value) is not int or value < 0 for value in prices_cents):
        raise ValueError("Percentiles require a nonempty list of non-negative integer cents")
    prices = sorted(prices_cents)
    position = (len(prices) - 1) * Fraction(_percent(percentile)) / 100
    lower = position.numerator // position.denominator
    upper = min(lower + 1, len(prices) - 1)
    return Fraction(prices[lower]) + (position - lower) * (prices[upper] - prices[lower])


def _fraction_json(value: Fraction) -> dict:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _analyze_case(case: dict, config: StatisticsConfig, audit_complete: bool) -> tuple[dict, list[dict]]:
    audit = []
    identities = defaultdict(list)
    for market in case["marketplaces"]:
        for comparison in market["comparisons"]:
            row = deepcopy(comparison)
            row.update({"audit_row": len(audit) + 1, "source_marketplace_id": market["marketplace_id"],
                        "observed_included": False, "filtered_included": False,
                        "exclusion_reasons": [], "filter_reason": "excluded_from_observed", "duplicate_of": None})
            offer = row["offer"]
            identifier = (offer.get("marketplace_id"), offer.get("offer_id"))
            if (all(isinstance(value, str) and value.strip() for value in identifier)
                    and identifier[0] == market["marketplace_id"]):
                identities[identifier].append(len(audit))
            else:
                row["exclusion_reasons"].append("invalid_offer_identity")
            if row["oem_match"] != "exact":
                row["exclusion_reasons"].append("oem_not_exact")
            if row["compatibility"] != "confirmed":
                row["exclusion_reasons"].append("compatibility_not_confirmed")
            if type(offer.get("price_cents")) is not int or offer["price_cents"] <= 0:
                row["exclusion_reasons"].append("invalid_price")
            units = offer.get("available_units")
            if type(units) is not int or units < 0:
                row["exclusion_reasons"].append("invalid_units")
            elif units == 0:
                row["exclusion_reasons"].append("no_available_units")
            audit.append(row)
    for indices in identities.values():
        first = audit[indices[0]]
        conflict = any((audit[index]["offer"], audit[index]["oem_match"], audit[index]["compatibility"])
                       != (first["offer"], first["oem_match"], first["compatibility"]) for index in indices[1:])
        if conflict:
            for index in indices:
                audit[index]["exclusion_reasons"].append("conflicting_duplicate_identity")
        else:
            for index in indices[1:]:
                audit[index]["exclusion_reasons"].append("duplicate_identity")
                audit[index]["duplicate_of"] = first["audit_row"]
    observed = [row for row in audit if not row["exclusion_reasons"]]
    prices = [row["offer"]["price_cents"] for row in observed]
    coverage = (audit_complete and case["matching_complete"]
                and all(market["status"] != "error" for market in case["marketplaces"]))
    state = ("source_error" if not coverage else "no_comparable_offers" if not observed
             else "insufficient_sample" if len(observed) < config.min_sample_size else "sufficient_sample_demo")
    stats = {"evidence_status": state, "coverage_complete": coverage,
             "retrieved_offer_rows": len(audit), "observed_offers_count": len(observed),
             "observed_units": sum(row["offer"]["available_units"] for row in observed),
             "observed_min_price_cents": min(prices, default=None),
             "observed_max_price_cents": max(prices, default=None),
             "lower_cutoff_cents": None, "upper_cutoff_cents": None,
             "percentile_filter_applied": False, "filtered_offers_count": None, "filtered_units": None,
             "filtered_min_price_cents": None, "filtered_max_price_cents": None}
    for row in observed:
        row.update({"observed_included": True, "filtered_included": None, "filter_reason": state})
    if state == "sufficient_sample_demo":
        lower = linear_percentile(prices, config.lower_percentile)
        upper = linear_percentile(prices, config.upper_percentile)
        retained = []
        for row in observed:
            included = lower <= row["offer"]["price_cents"] <= upper
            row.update({"filtered_included": included,
                        "filter_reason": "inside_percentile_band" if included else "outside_percentile_band"})
            if included:
                retained.append(row)
        kept_prices = [row["offer"]["price_cents"] for row in retained]
        stats.update({"lower_cutoff_cents": _fraction_json(lower), "upper_cutoff_cents": _fraction_json(upper),
                      "percentile_filter_applied": True, "filtered_offers_count": len(retained),
                      "filtered_units": sum(row["offer"]["available_units"] for row in retained),
                      "filtered_min_price_cents": min(kept_prices, default=None),
                      "filtered_max_price_cents": max(kept_prices, default=None)})
        if not retained:
            stats["evidence_status"] = "empty_filtered_sample"
    return stats, audit


def analyze_matching(matching: dict, config: StatisticsConfig | None = None, *,
                     source_offers: list[dict] | None = None, audit_complete: bool = True) -> dict:
    """Preserve matching evidence; add observed/filtered statistics and every audit row."""
    config = config or StatisticsConfig()
    result = deepcopy(matching)
    result.update({"delivery": "stage2-03", "statistics_config": config.as_dict(), "statistics_trace": [],
                   "audit_scope": "all_source_offers" if source_offers is not None else "provided_matching",
                   "audit_complete": audit_complete, "unassociated_offer_audit": []})
    for case in result["results"]:
        stats, audit = _analyze_case(case, config, audit_complete)
        case.update({"statistics": stats, "offer_audit": audit})
        result["statistics_trace"].append({"step": len(result["statistics_trace"]) + 1,
                                           "inventory_id": case["inventory"]["id"], "operation": "percentile_filter",
                                           "input_rows": len(audit), "observed_offers_count": stats["observed_offers_count"],
                                           "filtered_offers_count": stats["filtered_offers_count"],
                                           "evidence_status": stats["evidence_status"]})
    if source_offers is not None:
        result["source_offers"] = deepcopy(source_offers)
        associated = {(row["offer"].get("marketplace_id"), row["offer"].get("offer_id"))
                      for case in result["results"] for row in case["offer_audit"]}
        for offer in source_offers:
            if (offer.get("marketplace_id"), offer.get("offer_id")) not in associated:
                result["unassociated_offer_audit"].append({
                    "offer": deepcopy(offer), "observed_included": False, "filtered_included": False,
                    "oem_match": "unknown", "compatibility": "unknown", "audit_row": None,
                    "source_marketplace_id": offer.get("marketplace_id"), "duplicate_of": None,
                    "exclusion_reasons": ["not_retrieved_for_selected_inventory"],
                    "filter_reason": "not_retrieved_for_selected_inventory", "field_evidence": {},
                })
    return result


def execute_stage2_statistics(db_path: Path, *, min_days: int = 60, max_days: int = 90,
                              config: StatisticsConfig | None = None) -> dict:
    matching = execute_stage2_matching(db_path, min_days=min_days, max_days=max_days)
    source_offers = None
    audit_complete = False
    if matching["status"] != "error":
        harness = Harness(max_tool_calls=1)
        with closing(read_only_connection(db_path)) as conn:
            try:
                source_offers = _sql_read(harness, "market_evidence", _list_source_offers,
                                          sql=AUDIT_OFFERS_SQL, parameters=[], conn=conn)
                audit_complete = True
            except sqlite3.Error:
                matching.update({"status": "partial", "complete": False})
        for event in harness.events:
            event.update({"step": len(matching["trace"]) + 1, "scope": "all_source_offers"})
            matching["trace"].append(event)
    result = analyze_matching(matching, config, source_offers=source_offers, audit_complete=audit_complete)
    result["audit_scope"] = "all_source_offers"
    return result
