"""Auditable domain rules; no generative model decides numeric truth."""
from __future__ import annotations


def evaluate_case(item: dict, market: dict, *, age_days: int) -> dict:
    # Candidates are selected in SQL. This function explains why and prioritizes.
    reasons = []
    if item["days_in_stock"] >= age_days:
        reasons.append("aged_inventory")
    if not item["oem_verified"]:
        reasons.append("oem_needs_verification")
    flags = []
    if not item["oem_verified"]:
        flags.append("OEM is unverified; market matches are exploratory, not validated")
    if market["offers_count"] == 0:
        flags.append("No exact OEM market offers in the synthetic fixture")
    if market["offers_count"] and item["oem_verified"] and item["our_price_eur"] > market["max_price_eur"]:
        flags.append("Current price exceeds highest observed comparable offer")

    if not item["oem_verified"] or (item["days_in_stock"] >= 180 and item["sales_90d"] == 0):
        priority = "high"
    elif flags or (item["days_in_stock"] >= age_days and item["sales_90d"] == 0):
        priority = "medium"
    else:
        priority = "low"

    return {
        "inventory_id": item["id"],
        "part_name": item["part_name"],
        "oem": item["oem"],
        "vehicle_segment": item["vehicle_segment"],
        "days_in_stock": item["days_in_stock"],
        "stock_qty": item["stock_qty"],
        "sales_90d": item["sales_90d"],
        "our_price_eur": item["our_price_eur"],
        "oem_verified": bool(item["oem_verified"]),
        "reasons": reasons,
        "priority": priority,
        "risk_flags": flags,
        "market": market,
        "recommended_action": "human_review_only",
    }
