# OEM Intelligence Agent — Automotive × Data Engineering × AI

> **Public-safe learning lab**: entirely synthetic automotive inventory and market offers. Independent implementation. **No ex-employer data, no real scraping, no autonomous price/database changes.**

A hands-on repo to learn **SQL / Python ETL / agent anatomy / tools / skills / harness engineering / evaluations / scheduling** on one realistic automotive problem: candidates with old inventory or unverified OEM references, and corresponding price/stock market evidence.

## Modes — know the difference

- `rules`: **deterministic workflow, NOT an LLM agent**. Free, offline, repeatable; mandatory baseline.
- `llm`: optional **real LLM agent**, using the OpenAI Agents SDK with function tools and a bounded loop. Model opinions are saved *separately*, never into the trusted evidence report. May incur API charges; ChatGPT subscription is not an API key.

## Quick start (Windows PowerShell, Python 3.11+)

```powershell
python -m oem_agent seed
python -m oem_agent run --mode rules --out runs/first-run
python -m unittest discover -s tests -v
```

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

### Optional: first LLM tool-calling agent

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
skills/*/SKILL.md       Two portable procedural skills
AGENTS.md               Instructions for your coding assistant
.github/workflows/      Offline CI; schedule disabled until approved
tests/                  Deterministic verification
docs/                   Architecture, learning labs, public-safety checklist
```

## Core exercise

**Do not read the SQL first.** Use DB Browser and predict exactly how many candidates will appear, then execute your own query and explain why. Read `docs/LEARNING_LABS.md` to work progressively from SQL to an LLM agent and harness.

## Technical boundaries

No external scraping, real production data, network connectors or user accounts. No database writes outside explicit `seed --reset` on your selected demo DB. Running `--mode rules` uses standard-library Python only. Run a separate risk assessment before any real integration.

## Developer-oriented sources

- https://openai.github.io/openai-agents-python/
- https://agentskills.io/specification
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

## License

Code in this independently authored learning example is made available under MIT (`LICENSE`). This license never extends to third-party or former-employer materials.
