---
name: oem-stock-review
description: Analyze synthetic automotive inventory using SQLite and Python. Use when the request concerns aged stock, OEM verification candidates, sell-through, or inventory prioritization.
---
# OEM stock review

1. Treat `data/demo.sqlite` as invented fixture only. Open it in read-only mode.
2. Find items with stock_qty > 0 and (days_in_stock >= 90 OR oem_verified = 0).
3. Explain why each record was selected. A candidate is not a confirmed bad price.
4. Do not mutate inventory or set verified flags.
5. Invoke deterministic code for SQL and numeric aggregates; reserve LLM for explaining evidence.
6. Report unknown or missing values explicitly.
7. Verify with `python -m unittest discover -s tests -v`.
