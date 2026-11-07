# OEM Intelligence Agent — Automotive × Data Engineering × AI

> **Public-safe learning lab**: entirely synthetic automotive inventory and market offers. Independent implementation. **No ex-employer data, no real scraping, no autonomous price/database changes.**

A hands-on repo to learn **SQL / Python ETL / agent anatomy / tools / skills / harness engineering / evaluations / scheduling** on one realistic automotive problem: candidates with old inventory or unverified OEM references, and corresponding price/stock market evidence.

## Modes — know the difference

- `rules`: **deterministic workflow, NOT an LLM agent**. Free, offline, repeatable; mandatory baseline.
- `llm`: optional **real LLM agent**, using the OpenAI Agents SDK with function tools and a bounded loop. Model opinions are saved *separately*, never into the trusted evidence report. May incur API charges; ChatGPT subscription is not an API key.

## Quick start (Windows PowerShell, Python 3.11+)

From a fresh checkout, create an isolated environment and install the existing
Excel extra so all 11 XLSX tests execute instead of being skipped:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[excel]"
.venv\Scripts\python -W error::ResourceWarning -m unittest discover -s tests -v
.venv\Scripts\python -m oem_agent.stage5_eval offline --out runs/eval-5-5-offline-01
.venv\Scripts\python -m oem_agent seed
.venv\Scripts\python -m oem_agent run --mode rules --out runs/first-run
```

No API key, `.env`, running model or paid service is needed for these commands.
Installing the extra may download Python packages; the tests and offline evaluator
use synthetic local fixtures and simulated protocol responses. The core rules
workflow needs only the standard library; Excel remains optional for that workflow.
CI installs `.[excel]`, verifies its import and runs the full suite plus the offline
evaluator. Use a new output directory for each evaluation; do not reset an existing
DB just to repeat verification.

Open `data/demo.sqlite` in **DB Browser for SQLite** to practice SQL. Files generated: `runs/first-run/report.json`, `report.md`, `trace.jsonl`. Reruns need a new `--out` path (no overwrite).

SQLite connections are explicitly closed on success and exceptions so temporary
databases can be removed on Windows. A connection's own `with` block only manages
commit/rollback; callers of `read_only_connection` must use `contextlib.closing`
or `try/finally` with `conn.close()`. This offline fix keeps all data synthetic
and retains the read-only analysis and existing report protections.

Git versions the synthetic fixtures in `oem_agent/db.py`, code, tests,
documentation and the placeholder-only `.env.example`. Generated databases,
SQLite journals, reports, caches, virtual environments and credentials are
ignored. Recreate the data in a fresh checkout with `python -m oem_agent seed`;
the existing database and reports are never overwritten by default. Inspect
files eligible for a first commit with `git ls-files --others --exclude-standard`
and excluded files with `git ls-files --others --ignored --exclude-standard`.

Stage 2 Delivery 01 has a separate synthetic database and opt-in CLI:
`python -m oem_agent.stage2 seed` then `python -m oem_agent.stage2 select`.
The default inclusive 60-90 day selection returns all 27 eligible pieces, with
source rows and a SQL execution trace. See [Delivery 01](docs/DELIVERY_01.md)
for the dataset, exact expected IDs, isolation guarantees and verification steps.

Delivery 02 adds `python -m oem_agent.stage2 match` over the same database and
27-piece selection. It reports independent OEM/compatibility classifications
for both synthetic marketplaces, including empty retrievals and SQL errors.
See [Delivery 02](docs/DELIVERY_02.md) for rules, trace fields and CLI exit codes.

Delivery 03 adds `stats` with demonstrative P10/P90 filtering, a configurable
minimum of ten usable offers, and optional audited Excel export via `.[excel]`.
`seed-stats` provides a separate sufficient-sample scenario; the old seed is
unchanged. See [Delivery 03](docs/DELIVERY_03.md) for commands and expected values.

Delivery 04 adds a separate `python -m oem_agent.stage4` CLI for invented DEMO
costs and local review revisions. Decisions include operator metadata and bind
to evidence/parameter fingerprints; changed evidence requires renewed review.
See [Delivery 04](docs/DELIVERY_04.md) for eligibility gates, states and examples.

Stage 5 increment 5.1 adds immutable evidence snapshots and five structured
read contracts through `stage5_evidence` / `stage5_contracts`. It reuses Delivery
03 JSON and an optional Delivery 04 revision without DB access or a model.
See [increment 5.1](docs/STAGE_5_1.md) for the Python API, verification, hardware
recommendation and the design of five separate future evaluation scenarios.

Increment 5.2 adds `ReadOnlyDispatcher` over those contracts, reusing the existing
harness with charged attempts, restrictive permissions, budgets, error/repetition
controls, page tracking and local audit exports. It remains entirely offline,
without a model. See [increment 5.2](docs/STAGE_5_2.md) for simulated calls,
completion rules, verification and cooperative-timeout limitations.

Increment 5.3 adds a local-only Ollama adapter and native tool-calling loop for
`qwen3:4b`, reusing the dispatcher. It has no paid/cloud fallback and never
provisions its runtime. See [increment 5.3](docs/STAGE_5_3.md) for protocol tests,
resource limits, required cloud-disable checks and the opt-in three-question
smoke test. Runtime/model provisioning requires explicit operator authorization
and remains separate from the agent; offline protocol tests do not prove model quality. The first
three-question real run timed out without native tool calls; functional model
validation remains incomplete (details in the increment documentation).

An explicitly selected Groq Free adapter now reuses that same native tool loop,
dispatcher and five read contracts with `qwen/qwen3.8-27b`. Its smoke CLI loads
`GROQ_API_KEY` and the fixed `GROQ_MODEL` from the ignored local `.env` without
extra dependencies; existing process variables win. Use
`python -m examples.stage5_3_groq_smoke --check-config` for offline booleans only.
Enter the key directly in a local editor, never in Codex chat. It requires recent
operator confirmation of Free access/quota and separate synthetic-data transfer authorization, and never
falls back from Ollama or activates billing. See [Groq preparation](docs/STAGE_5_3_GROQ.md)
for metadata-only checks, limits, offline tests and the opt-in maximum-three-question
smoke test. Stage 5.3 was accepted with three correct real business answers;
see the recorded limits and evidence in that document. Stage 5.4 consolidates the
runtime skill and adds configuration identity and verified source references:
[current context and trace](docs/STAGE_5_4.md). No new real inference is implied.

### Reproducible evaluation (Stage 5.5)

Five independent synthetic scenarios reuse the current agent and five tools,
with authored ground truth and no changes to the existing 27 pieces.

The quick start above runs the complete offline evaluation. The current skill is
**1.0.2**. Stage 5.5 also records the authorized compact tool-result projection,
pre-validation quota-reservation traces and exhaustive matching aggregates; native
model tool selection, the five read contracts and business rules are preserved.

The initial evaluation is closed with the following results. Real rows describe
the latest authorized execution of each scenario, not all historical attempts:

| Evaluation | Skill | Closed result |
| --- | --- | --- |
| Offline | 1.0.2 | **359/359 tests, 5/5 scripted scenarios, 12/12 negative controls** |
| E1 real, Groq Free | 1.0.1 | **Approved: 10/10 structured fields correct**, complete final answer |
| E2 real, Groq Free | 1.0.2 | **Partial / not approved**: additional exploration and preventive quota block |
| E5 real, Groq Free | 1.0.2 | **Partial / not approved**: blocked before tool results could be sent back to the model |

Offline success validates protocols, deterministic facts and harness controls; it
is not measured LLM accuracy. The real sample is small, uses different skill
versions and establishes no statistical reliability percentage. E5 retained its
known offers and source-error evidence locally, but produced no final answer.
Valid references establish source integrity, not semantic correctness. Preventive
Free quota controls may withhold otherwise useful answers. No automatic commercial
operation is authorized. No further E2/E5 run or optimization is part of this close.
See [dataset, final results and limitations](docs/STAGE_5_5.md). Any future real
inference requires separate authorization and fresh Free-plan/quota confirmation.

### Historical original demo: optional SDK reviewer (isolated)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[llm]"
# Configure OPENAI_API_KEY securely in your shell; do NOT commit it.
python -m oem_agent run --mode llm --model gpt-5-mini --out runs/first-llm
```

Your API account must have access to the model selected by `--model`; you can choose another available model. The LLM does not visit any marketplace, and only receives synthetic audited cases through two read-only tools. SDK external tracing is disabled for this optional demo; a canonical local trace is still written for the deterministic pipeline. The generated `llm_notes_UNVERIFIED.md` is **not an evaluated fact source**.

## Rule example

```sql
SELECT id, oem, part_name, days_in_stock, oem_verified
FROM inventory
WHERE stock_qty > 0
  AND (days_in_stock >= 90 OR oem_verified = 0)
ORDER BY (oem_verified = 0) DESC, days_in_stock DESC;
```

The market lookup is based on exact OEM equality `WHERE oem = ?`. `offers_count` means the number of listings; `total_listed_units` is the sum of those listings' stock. They are intentionally different metrics. No offer means min/max `null`, never invented prices.

## Repository map

```text
oem_agent/db.py          SQLite schema, synthetic fixtures, readonly SQL
oem_agent/rules.py       Business gates and priority
oem_agent/harness.py     Tool allowlist, budget, local audit trail, output
oem_agent/llm_agent.py   Optional real SDK agent with restricted tools
oem_agent/skills/oem-evidence-review/SKILL.md  Single versioned runtime skill
AGENTS.md               Instructions for your coding assistant
.github/workflows/      Offline CI; schedule disabled until approved
tests/                  Deterministic verification
docs/                   Architecture, learning labs, public-safety checklist
```

## Core exercise

**Do not read the SQL first.** Use DB Browser and predict exactly how many candidates will appear, then execute your own query and explain why. Read `docs/LEARNING_LABS.md` to work progressively from SQL to an LLM agent and harness.

## Technical boundaries

All inventory, OEM references, offers, prices and costs are synthetic or DEMO.
There is no live marketplace scraping or connection to business systems. Fixture
creation is an explicit operator action; the original demo permits replacement
only with `seed --reset` on the selected demo DB. Agent tools read pinned evidence
and cannot change inventory/prices, record human decisions or execute commercial
actions. The separate local DEMO review CLI requires explicit operator input.

The rules workflow and offline evaluator make no provider request. Groq inference
is hosted and opt-in, sends only authorized synthetic context, and has no paid
fallback; account plan/billing confirmation remains the operator's responsibility.
The historical paid SDK example is isolated from this path. Run a separate risk
assessment before any real-data integration.

## Developer-oriented sources

- https://openai.github.io/openai-agents-python/
- https://agentskills.io/specification
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

## License

Code in this independently authored learning example is made available under MIT (`LICENSE`). This license never extends to third-party or former-employer materials.
