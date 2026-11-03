# Guided learning labs (SQL -> tools -> agents -> harness)

## Lab 1 — SQL (independent reasoning)
In DB Browser, open a **copy** of the generated `data/demo.sqlite`.
Predict which records qualify: `stock_qty > 0 AND (days_in_stock >= 90 OR oem_verified = 0)`.
Write your own query before reading `oem_agent/db.py`. Explain precedence of AND/OR.
Compare the output with `python -m oem_agent run --mode rules`.

## Lab 2 — Matching integrity
Study `DEMO-ALT-1001` vs `DEMO-ALT-10010`.
Write SQL to show how `LIKE '%...'` causes a false positive and why exact `=` prevents it.
Define the difference between 3 offers and 6 available units.

## Lab 3 — Tools
Implement and test a third read-only tool: `sales_velocity(inventory_id)`.
Document input/output, validation and when it must not be used.

## Lab 4 — Harness engineering
Remove `market_evidence` from `allowed_tools` in an isolated test. Assert `PolicyViolation`.
Set call budget too low and assert fail-closed behavior. Restore before commit.

## Lab 5 — First real LLM agent (requires separate API key and may incur cost)
Install optional SDK; run `python -m oem_agent run --mode llm`.
Compare `report.json` (evidence) to `llm_notes_UNVERIFIED.md` (model opinion).
Check whether the model cites a real case ID and uses exact values; count hallucinations.

## Lab 6 — Evaluation
Create 10 synthetic cases: valid reference, partial-match trap, zero offers, unverified OEM,
missing/empty stock, old stock with sales, old stock with no sales, competing offers, duplicates, and negative input rejection.
Score correctness of decisions; do not judge only answer fluency.

## Lab 7 — Scheduler
Manually run GitHub Actions synthetic demo (`workflow_dispatch`).
After verifying scope/cost/logs, intentionally enable a schedule in a separate commit.
Confirm GitHub hosted runners are ephemeral; each run seeds fixture independently.
