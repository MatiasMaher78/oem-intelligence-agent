---
name: oem-market-check
description: Validate exact OEM reference matches in a synthetic market-offers dataset. Use when researching min/max prices, number of offers, available units, missing matches or market comparison uncertainty.
---
# OEM market check

1. Work only with synthetic `market_offers`. Never scrape real marketplaces in this lab.
2. Match `oem = ?` (exact equality), NEVER wildcard or substring matching.
3. Exclude nonexistent matches; if no offers exist, return null min/max, zero listings and zero units.
4. Define separately `offers_count` (number of listings) and `total_listed_units` (sum of listed stock).
5. If OEM is unverified, mark market matches exploratory, not proof of reference validity.
6. Keep list of underlying offers for auditability.
7. Never make an automated price update based on the output.
