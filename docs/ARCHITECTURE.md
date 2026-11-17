# Current architecture and system boundaries

OEM Intelligence Agent prepares deterministic evidence for reviewing aging
synthetic automotive stock, then lets one bounded LLM answer questions about that
evidence. The evaluated hosted path uses Groq Free with `qwen/qwen3.8-27b`.
All inventory, vehicle fields, marketplaces, prices and costs are invented.

Start with the [product and Quick Start](../README.md). The
[evaluation reference](EVALUATION.md) separates offline checks, historical real
answers and their limitations. Dated delivery records remain available through
[the history index](history/README.md) and the historical sections below.

## Current system at a glance

```mermaid
flowchart TD
    subgraph Pipeline[Local deterministic pipeline]
        DB[Synthetic SQLite inventory and two marketplaces] --> SQL[SQL stock and age selection]
        SQL --> Matching[Python OEM and compatibility matching]
        Matching --> Statistics[Comparable-offer statistics]
        Statistics --> Evidence[Audited JSON evidence]
        Evidence --> Excel[Optional Excel export]
        Evidence --> Costs[DEMO cost gates and local dossier]
    end
    Evidence --> Snapshot[Immutable evidence snapshot]
    Costs -. optional pinned review revision .-> Snapshot
    Question[Business question] --> Loop[Single agent conversation loop]
    Model[Explicitly selected Groq model] -->|native tool requests or final text| Loop
    Loop -->|question and bounded evidence context| Model
    subgraph Harness[Local read-only harness]
        Dispatcher[Allowlist, strict inputs, budgets and pagination] --> Readers[Five evidence readers]
        Readers --> Dispatcher
    end
    Snapshot --> Readers
    Loop -->|native requests| Dispatcher
    Dispatcher -->|validated results| Loop
    Loop --> Answer[Advisory answer and source references]
    Answer --> Human[Human reviewer]
    Human --> CLI[Separate local operator CLI]
    Costs --> CLI
    CLI -->|new review revision| Costs
```

Excel is a presentation output, not an input to the cost calculation or agent.
Review revisions are local demonstration files, not an integrated enterprise
workflow. The model has no approval or commercial-write tool.

## Deterministic pipeline and evidence inputs

- [Selection](../oem_agent/stage2_db.py) uses parameterized SQL for every row with
  stock greater than zero and an inclusive configurable age interval, default
  60-90 days. The separate Stage 2 fixture selects 27 pieces without a result cap.
- [Matching](../oem_agent/stage2_matching.py) keeps literal OEM identity separate
  from agreement on part name, brand, model and version. Compatibility is declared
  synthetic-field agreement, not real vehicle fitment. Missing offers and failed
  sources never establish incorrect cataloguing.
- [Statistics](../oem_agent/stage3_statistics.py) use only usable exact-OEM,
  confirmed-compatibility offers. They preserve exclusions and identifiable
  duplicates, units versus advertisements, observed versus filtered prices and
  incomplete source coverage. DEMO P10/P90 uses linear interpolation and a default
  minimum of ten offers; details remain in [Delivery 03](DELIVERY_03.md).
- [Excel export](../oem_agent/excel_export.py) is optional through the existing
  `excel` extra. JSON remains the authoritative numerical evidence.
- [DEMO costs and review](../oem_agent/stage4_review.py) consume that JSON through
  separate operator actions. Invented calculations are gated by sufficient,
  complete evidence and never update inventory prices. The
  [Delivery 04 CLI guide](DELIVERY_04.md) documents the local dossier lifecycle.

The original six-case demo is isolated from this pipeline. Its 90-day/unverified
OEM selector, HIGH/MEDIUM/LOW priorities and two-tool SDK path are historical
interfaces, not the current 27-piece selector or five-tool agent.

## Immutable snapshots and five read-only tools

[EvidenceSnapshot](../oem_agent/stage5_evidence.py) validates and pins structured
statistics evidence plus an explicitly selected optional review revision. It
holds canonical immutable JSON bytes and exposes independent copies. Readers do
not query SQLite, discover files, refresh evidence or record human decisions.
There is no watcher: a new evidence revision must be loaded explicitly.

| Tool | Current read scope |
| --- | --- |
| `summarize_run` | Selection, source coverage, evidence-state counts and review availability. |
| `list_cases` | Paginated inventory, or `view=costs` for existing DEMO eligibility, block reasons and review state. |
| `inspect_case` | One piece's inventory, matching aggregate, statistics or source trace. Matching groups retain audited OEM/compatibility labels, unique offer identities, coverage and references. |
| `list_offers` | Paginated granular offer audit, including excluded, duplicate and unassociated rows. Audit rows are not unique advertisements or units. |
| `review_status` | Effective review state against pinned evidence; never creates a decision. |

The [contracts](../oem_agent/stage5_contracts.py) define strict structured inputs
and outputs. Matching aggregates do not recalculate eligibility or replace the
complete granular audit. Hash-bound JSON Pointer references resolve only within
the selected snapshot and dossier. Hashes verify consistency, not authenticity.

## Native agent loop and local harness

[stage5_agent](../oem_agent/stage5_agent.py) sends native tool definitions to the
selected model. The LLM chooses requests; Python validates and dispatches them.
There is no keyword router, prose-to-command interpreter or simulated business
routing in the real-agent path. Offline protocol transcripts are explicitly
scripted evaluation inputs, not real model decisions.

[ReadOnlyDispatcher](../oem_agent/stage5_dispatcher.py) wraps the existing
[Harness](../oem_agent/harness.py). It records every attempted request before
validation, including invalid attempts, separately from executed reads. It
checks the allowlist, strict arguments, result integrity, references, repeated
reads, page progression and bounded errors/time/size. Defaults cap tool attempts
at eight, errors at two, session time at 180 seconds, reader time at two seconds,
arguments at 4096 bytes and result envelopes at 8192 bytes. Reader deadlines are
cooperative, not process isolation or hard CPU cancellation.

Unfinished pages, errors, missing requested evidence and exhausted budgets cannot
be finalized as a complete execution. Source coverage and final-answer correctness
remain separate: a useful explanation of partial evidence does not establish
complete source coverage or verified semantics. `answer_verified` stays false.

The [Groq adapter](../oem_agent/groq_hosted.py) normalizes native arguments and IDs
and uses one fixed HTTPS destination. Tools and full evidence stay local;
authorized synthetic questions, instructions, schemas and consulted projections
are sent to the provider. There is no paid-tier, model or provider fallback.
Fresh operator confirmation of Free access and applicable quotas is required;
model availability does not certify billing status. Unknown balances remain
unknown and local ceilings still apply. See the [Groq guide](STAGE_5_3_GROQ.md).

## Context, configuration and observable evidence

The sole runtime instruction source is the packaged
[OEM evidence skill](../oem_agent/skills/oem-evidence-review/SKILL.md), version
**1.0.2**. AGENTS.md, historical documents and skill frontmatter do not enter model
context; the skill grants no permissions. [stage5_context](../oem_agent/stage5_context.py)
loads its body once and binds the effective skill, prompt, schemas, provider/model
and limits to configuration fingerprints.

Contract version **5.1**, configuration format **5.4.0** and evaluator **5.5.1**
version different components. Model-facing tool results omit redundant metadata
from the first read while retaining facts, IDs, coverage, errors and references.
Full tool envelopes and hash-linked traces remain in the local audit.

Traces retain attempts/results, timing, errors, configuration identity and valid
provider-reported token usage separately from preventive reservations. Byte-based
estimates are not real token consumption. Reservations can block a POST that
might fit by actual tokens; the existing Free and harness limits are preserved.
Authentication secrets and private model reasoning are not recorded.

References are attached from accepted reads and checked against pinned evidence.
`references_verified` means resolvable sources, not semantic accuracy or automatic
validation of every claim. [Stage 5.4](STAGE_5_4.md) retains the base reference
semantics; [Stage 5.5](STAGE_5_5.md) records compact context, reservation logging,
matching aggregates and their evaluations.

## Separate human review and retained historical paths

A person records decisions through `oem_agent.stage4`, with a responsible person,
reason, timestamp and evidence fingerprint. Changes to the review basis invalidate
prior approvals. Local records do not authenticate identity or integrate an ERP;
no state triggers an automatic commercial operation. Eligibility is not approval.

The original rules/optional SDK demo and the local Ollama adapter remain available
as historical paths. Ollama experiments did not demonstrate complete real answers
on the recorded hardware; protocol-test success is not hardware/model acceptance.
Neither path is an automatic fallback. Their full records and commands remain at
[the original local experiment](STAGE_5_3.md) and [the history index](history/README.md).

## Historical architecture and delivery milestones

The sections below preserve the earlier architecture record. Increment-specific
phrases such as "deferred", "current" or "now" refer to the milestone being
recorded. The first diagram is the isolated original demo, not the current
five-tool architecture above. The final upgrade list preserves early proposals:
evaluation and several limits/metrics now exist; remaining ideas grant no
permission to add retries, scheduling, integrations or commercial actions.

### Original demo

```mermaid
flowchart TD
  A[Pure synthetic SQLite inventory] --> B[SQL: candidates]
  B --> C[Python: exact OEM market evidence]
  C --> D[Rules: flags and human-review priority]
  D --> E[Canonical JSON and Markdown reports]
  D --> F[Optional OpenAI Agents SDK]
  F --> G[Agent tools: list_cases and inspect_case]
  G --> F
  F --> H[Unverified advisory memo]
  B --> I[Local harness trace]
  C --> I
```

The offline `rules` mode is a **deterministic workflow, not an LLM agent**.
The `llm` mode introduces a model with tools and an execution loop managed by the Agents SDK.

### Tool interfaces
- `list_candidates(conn, days, limit)`: selects with parameterized SQL and hard cap.
- `market_evidence(conn, oem)`: exact matching; returns evidence and min/max/units.
- `list_cases()`: LLM-facing, precomputed summaries only.
- `inspect_case(inventory_id)`: LLM-facing, restricted to selected candidate IDs.

### Harness safety
- DB connection uses URI `mode=ro` plus `PRAGMA query_only`.
- Local explicit allowlist and tool-call budget: max candidates + one lookup.
- Optional SDK max_turns=8; tool access restricted to precomputed evidence.
- Reports never overwrite nonempty output directory.
- Synthetic fixture only. No network market lookup. No database write tool.
- Tracing to external SDK provider disabled in LLM demo by default to avoid exporting data; local workflow writes `trace.jsonl`.
- A scheduler *triggers* workflow, it is not the same as an agent.

### Stage 2 Delivery 01

Stage 2 [Delivery 01](DELIVERY_01.md) adds `oem_agent.stage2` and `stage2_db`
without altering the original pipeline. Its separate database contains inventory
identity/vehicle fields and two identifiable marketplace fixtures. A single
allowlisted read selects all in-stock pieces in an inclusive age range and
emits JSON source evidence plus SQL/parameter trace. It reuses the existing
read-only connection and harness; marketplace analysis is deferred.

### Stage 2 Delivery 02

[Delivery 02](DELIVERY_02.md) adds `stage2_matching` and the `stage2 match` CLI.
It reuses the same inventory selector, schema and fixtures, querying each source
by exact OEM or exact piece name. Python evaluates independent OEM and declared
compatibility axes and preserves every selected piece. Each query has SQL,
parameters and success/error trace evidence; failed source reads produce partial
coverage rather than empty-success claims. Cataloguing remains unassessed.

### Stage 2 Delivery 03

[Delivery 03](DELIVERY_03.md) reuses matching in `stage3_statistics`, adding exact
percentiles and a complete source-offer audit read. `stage3_fixtures` supplies a
separate sufficient-sample database. `excel_export` is an optional presentation
adapter with exclusive file creation; the numerical pipeline has no new runtime
dependency. Source/matching evidence is preserved alongside calculation traces.

### Stage 2 Delivery 04

Delivery 04 [local review](DELIVERY_04.md) consumes the structured Delivery 03
artifact in `stage4_review`, without further inventory SQL or mutations. Pure
DEMO cost calculations are gated by audited evidence; `stage4` writes new JSON
review revisions with operator decisions, evidence/parameter hashes, review
epochs and parent/history links. Every effective approval checks current evidence.

### Stage 5 increment 5.1

`stage5_evidence.EvidenceSnapshot` pins validated Delivery 03 evidence and an
optional Delivery 04 revision as immutable canonical JSON bytes. It retains
Delivery 01 selection, Delivery 02 matching and subsequent audit/history rather
than querying DBs again. `stage5_contracts` describes five strict read contracts;
direct projections have explicit pagination, source references and query traces.
No agent loop, model adapter, CLI or harness budget changes are part of 5.1.
See [increment documentation](STAGE_5_1.md).

### Stage 5 increment 5.2

`stage5_dispatcher.ReadOnlyDispatcher` wraps the five read projections with the
unchanged `Harness`. It records attempts before validation, restricts permissions,
tracks errors and pagination, blocks repeated reads and validates bounded results.
Only explicit finalization can mark a clean, nonexhausted session complete.
Outer traces retain rejected calls and errors; operator-only JSON exports use
exclusive creation. Calls are serialized; time limits are cooperative and late
results are withheld. No model adapter, CLI or new dependency is introduced.
See [increment documentation](STAGE_5_2.md).

### Stage 5 increment 5.3

`ollama_local` restricts transport to the loopback native API, checks disabled
cloud/local-model identity and enforces request/context limits. `stage5_agent`
lets one Qwen3:4b model select native tools, returning dispatcher projections to
the conversation; no business-question router exists. Observable audits exclude
private thinking/intermediate content. The opt-in example prepares three real
questions with operator-provisioned weights. Protocol simulations precede real
inference; the original optional SDK demo remains isolated and is never a fallback.
See [increment documentation](STAGE_5_3.md).

The explicitly selected `groq_hosted` adapter shares that conversation loop via
`run_groq_agent`. It translates native Groq argument strings/IDs to the existing
dispatcher representation and returns the same local evidence projections. Its
fixed HTTPS destination is independent of the unchanged loopback-only client;
neither adapter falls back to the other. Recent operator-confirmed Free quota
and synthetic-data transfer authorization are required before inference. The
account plan cannot be certified by the model catalog API. The third-query fix
adds an optional paginated cost-review view to list_cases, reusing dossier_view,
with lossless grouped model projections and measured-prefix prompt estimates.
Original defaults and five tool names remain; the dispatcher adds only missing-
dossier handling for this view. No SQL, business-rule, review-decision or permission
changes. The smoke CLI explicitly loads only
GROQ_API_KEY/GROQ_MODEL from the ignored root .env via the small standard-library
`groq_config` loader; an offline mode exposes availability booleans without network.
See [Groq preparation](STAGE_5_3_GROQ.md).

### Stage 5 increment 5.4

The sole runtime instruction source is the packaged, versioned
`oem_agent/skills/oem-evidence-review/SKILL.md`. `stage5_context` loads its body
once; AGENTS.md, history documents, frontmatter and retired skills never enter
model context. The existing shared native loop and five contracts are unchanged.

Every report binds the actual skill/prompt, schemas, selected provider/model and
safe effective limits to a configuration version/hash. The existing detailed
traces retain tools, real/estimated tokens, timing and sanitized failures.
Accepted tool references are deduplicated and resolved in the immutable snapshot,
attached as answer_references and identified by local R labels in the answer.
Missing/invalid sources prevent a complete answer. Reference integrity does not
validate model claims; advisory_only/answer_verified=false and partial coverage
semantics are retained. See [5.4 details](STAGE_5_4.md).

### Stage 5 increment 5.5

The independent evaluation fixture builds five one-piece synthetic databases with
existing SQL, matching and statistics, then pins snapshots/dossiers. Its explicit
source fault is confined to fixture construction. SQL, matching classifications,
prices, percentiles, DEMO costs and human-review rules remain unchanged.

Authorized follow-ups changed read projections and observability: tool responses
omit redundant session metadata from the first read while retaining business
facts, IDs, coverage, errors and resolvable references. The full dispatcher audit
is preserved locally. `inspect_case(section=matching)` adds an exhaustive summary
of unique source/offer identities by the already audited OEM/compatibility labels,
with counts, IDs, scope, coverage and references; `list_offers` retains every
original granular row. No new tool or permission is introduced.

The single runtime skill is now 1.0.2. It explains summary versus granular reads
and asks the model to finish when all requested dimensions have sufficient
evidence. Tool choice remains native LLM selection, with no Python question
routing. Reservation events are recorded before preventive validation, separating
estimated prompt/output/safety reservations from real provider usage and observed
remaining quota. The existing 8000 TPM and other Free/harness bounds are preserved;
a block cannot be presented as a complete execution.

The operator-side evaluator scores authored finite facts, necessary native reads,
references, source coverage, errors/limits and trace/configuration integrity.
Closure: 359/359 tests, 5/5 scripted scenarios and 12/12 negative controls; E1 real
approved (10/10 fields, skill 1.0.1), E2/E5 real partial/not approved (skill 1.0.2).
Offline success does not measure LLM accuracy. Source references do not establish
semantic correctness, and the small real sample supports no reliability estimate.
An E5 final answer can explain partial source coverage, but this real run stopped
before the model received tool results. No automatic commercial operation exists.
See [evaluation details and historical records](STAGE_5_5.md).

### Upgrade paths (do not implement prematurely)

1. Evals: expected candidate IDs, price extrema, error scenarios and LLM grounding rubric.
2. Persistence and idempotency for multi-run schedulers.
3. Data ingestion from authorized public/owned sources, with rate limits and Terms review.
4. HITL approval gateway for changes, entirely separate from analysis tools.
5. Tool-level retry, timeout, circuit breaker, cost/token budget, metrics.
6. Anomaly-based escalation with observability and alerts.
