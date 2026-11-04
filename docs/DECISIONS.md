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

# Architecture Decision Record 005

Decision: Delivery 04 uses invented cost components and immutable, evidence-bound
local review JSON rather than changing inventory or re-importing Excel values.

Reasons:
- Reuse and verify structured Delivery 03 evidence, recomputing its statistics
  with the existing analyzer; preserve its complete SQL/matching/audit trace.
- Invent a small integer-cent sum and hypothetical quantity rule, without using
  enterprise formulas, recommending sale prices or changing source data.
- Block estimates/approval for insufficient or incomplete evidence.
- Record explicit attributed decisions and all four review states in new revisions.
- Bind decisions to the whole evidence snapshot, DEMO configuration and review
  epoch. Changes invalidate approvals; reverting evidence does not revive them.
- Require current evidence on every view/decision and preserve historical records.
- Use standard-library JSON/SHA-256 and exclusive file creation; add no services,
  authentication, inventory SQL writes or new dependencies.

Status: Delivery 04 only. Reviewer identity is self-declared and checksums are
integrity aids, not signatures. See `DELIVERY_04.md` for limitations and verification.

# Architecture Decision Record 006

Decision: Stage 5 starts with immutable evidence readers and strict read contracts,
before adding harness orchestration or a real zero-paid-API model adapter.

Reasons:
- Delivery 03 already retains the earlier selection/matching evidence; use that
  authoritative JSON plus an explicitly selected optional Delivery 04 revision.
- Reuse deterministic validators/calculations and check matching/source coverage;
  never let a reader reinterpret OEMs, statistical gates or human decisions.
- Keep canonical bytes in frozen snapshots and expose copies, hash-bound JSON
  Pointers, explicit row pagination and deterministic per-query trace envelopes.
- Retain incomplete evidence, raw exclusions and absent/zero distinctions. A
  successful read is separate from successful coverage at the original sources.
- Do not create a model loop, install a runtime, alter fixtures, add dependencies
  or grant SQL/filesystem/write capabilities to these contracts.

Status: increment 5.1 only. Windows hardware favors evaluating qwen3:4b first;
actual latency/quality and acceleration remain unmeasured. The five-scenario
independent evaluation dataset is documented only. See `STAGE_5_1.md`.

# Architecture Decision Record 007

Decision: add a separate bounded read dispatcher around the existing harness and
5.1 contracts, preserving all earlier output/count semantics.

Reasons:
- Count every attempt before validation while retaining a separate execution
  counter; the original harness counts only calls past its policy gate.
- Reuse the fixed read allowlist and strict schemas; operators can narrow rights
  but tools cannot add callbacks, writes, SQL, file access or review decisions.
- Keep errors sticky, stop exhausted/forbidden/repeated calls and track issued
  page cursors. Only explicit finalization of a clean session can be complete.
- Validate result schema, source identity, hashes and references; withhold late,
  oversized or malformed results without rewriting the underlying evidence.
- Serialize dispatches and retain rejected attempts in a hash-linked local trace.
- Use cooperative time checks for these fixed in-memory readers. Do not introduce
  process orchestration, model/token handling or external services prematurely.

Status: 5.2 only. Model integration and hard cancellation are not implemented.
See `STAGE_5_2.md` for exact completion semantics, limits and verification.
