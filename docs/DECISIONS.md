# Architecture Decision Record 001

Decision: deterministic evidence pipeline first, optional single LLM reviewer second.

Reasons:
- SQL/Python handle joins, exact OEM matching and price statistics reproducibly.
- LLM adds language interpretation and prioritization, not numeric truth.
- One model with two bounded read-only tools is sufficient for learning tool calling.
- Multi-agent orchestration would introduce cost, latency, non-determinism and debugging overhead prematurely.
- Scheduling is deliberately opt-in to avoid surprise automated calls.

Status: initial lab, not a production app. Next ADR should discuss evaluation gates before real integrations.

# Architecture Decision Record 002

Decision: isolate Stage 2 Delivery 01 in a separate database and opt-in module CLI.

Reasons:
- Preserve the original fixture, schema, commands, rules and six-case baseline.
- Exclusively create a fresh Stage 2 file; do not migrate, reset or overwrite databases.
- Use parameterized SQL for `stock_qty > 0` and inclusive age bounds, with stable
  ordering and no candidate cap or dependency on marketplace offer coverage.
- Seed two sources explicitly, with offer identity scoped by marketplace.
- Store invented offer prices in integer EUR cents without implementing price math.
- Reuse read-only connections and a one-query harness with auditable source rows,
  SQL and parameters; add no dependencies or external services.

Status: Delivery 01 only. Matching, compatibility, percentiles, Excel, cost simulation
and enforced human review remain subsequent increments. See `DELIVERY_01.md`.

# Architecture Decision Record 003

Decision: Delivery 02 separates offer retrieval, OEM identity and declared compatibility.

Reasons:
- Preserve Delivery 01 selection, seed/schema, source identities and all 27 results.
- Retrieve by exact OEM OR exact name within one source, then classify every pair.
- Compare OEM literally; do not infer equivalence from prefixes or vehicle metadata.
- Confirm compatibility only when synthetic name/brand/model/version are known
  and equal. Known contradictions take precedence over missing fields.
- Preserve source rows and field-level reasons. Empty retrievals and failed reads
  remain distinct, and neither determines incorrect inventory cataloguing.
- Reuse the existing read-only connection and harness. Enrich only this workflow's
  trace, retaining partial results and nonzero CLI exits on SQL coverage errors.
- Do not add migrations, dependencies or price calculations.

Status: Delivery 02 only; percentile bands, Excel, cost simulation, LLM changes and
enforced human review remain deferred. See `DELIVERY_02.md`.

# Architecture Decision Record 004

Decision: Delivery 03 computes exact demonstrative percentile filtering and
exports its audited results through an optional Excel adapter.

Reasons:
- The original dataset has at most two usable comparable offers per piece, so a
  separate seed supplies ten for one existing piece without changing old fixtures.
- Require exact OEM, confirmed compatibility and available, valid source offers;
  keep all rejected evidence, including zero-stock and unrelated listings.
- Deduplicate per piece by source-local identity; exclude all conflicting copies.
- Use unweighted linear interpolation at `(n-1)*p/100` with exact rational cents.
- Distinguish observed extrema, quantile cutoffs and filtered actual extrema.
- Default to P10/P90 and minimum 10; withhold filtering for insufficient samples
  or incomplete coverage. Meeting a demonstrative threshold is not real validation.
- Add a full-source audit read, preserve matching traces, and separate calculation
  traces from SQL calls. Never overwrite source evidence or output files.
- Isolate openpyxl in the optional `excel` extra and load it only for export.

Status: Delivery 03 only. Cost simulation, HITL, real sources and LLM additions
remain deferred. See `DELIVERY_03.md` for sample values, states and verification.
