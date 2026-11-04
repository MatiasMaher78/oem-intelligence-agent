# Stage 2 - Delivery 04: invented DEMO costs and auditable local review

This delivery consumes the structured Delivery 03 JSON after Excel export.
JSON is the authoritative numerical evidence; the workbook is presentation and
is not parsed back into the evidence pipeline. The original demo, SQL, fixtures,
matching, statistics, XLSX export and inventory prices remain unchanged.

## Invented cost rule

Rule `DEMO-COST-001` is newly invented for this lab. It is not an employer formula
or a commercial cost/pricing model. All amounts are integer EUR cents:

| Parameter | Default |
| --- | ---: |
| `handling_cents` | 120 |
| `packaging_cents` | 80 |
| `inspection_cents` | 50 |
| `demo_units` | 1 |

```text
demo_unit_cost = handling_cents + packaging_cents + inspection_cents
demo_total_cost = demo_unit_cost * demo_units
```

Default unit/total cost is 250 cents (EUR 2.50). Components must be non-negative
integers; zero is a valid invented cost. Demo units must be a positive integer.
Quantity is a hypothetical simulation parameter, not a stock allocation or a
sales recommendation. Existing filtered prices are retained only as a contextual
reference band; they are not used in the cost formula. No sale price, margin,
commercial quotation or inventory price update is generated.

## Evidence and eligibility gates

- Accept only `stage2-03`, `demo_only: true`, `data_origin: 100% synthetic` JSON.
- Verify candidate identities, explicit coverage/configuration and SQL statuses.
- Recompute the Delivery 03 statistics/audit from its matching evidence with the
  existing pure analyzer; reject missing or internally inconsistent statistics.
- Require complete global source/audit coverage and no failed SQL events.
- For each piece, require `sufficient_sample_demo`, an applied percentile filter,
  its configured minimum observed sample, nonempty filtered offers/units and
  positive filtered price extrema.

Insufficient, unavailable or failed evidence leaves `demo_estimate: null`, records
block reasons and cannot be approved. Blocked pieces remain in the dossier and
can be rejected or marked for further review. No new SQL/schema is necessary:
the existing SQL evidence/trace is retained inside the JSON snapshot, and this
post-export stage performs no inventory DB access or mutation.

The separate statistical scenario retains all 27 pieces: inventory 10 is eligible
for the EUR 2.50 estimate; the other 26 have no estimate. All start pending and
none is ready for demo use before an explicit attributed approval.

## States and operator decisions

| CLI/JSON state | Meaning |
| --- | --- |
| `pending` | Pendiente; no current approval. |
| `approved` | Aprobado for this evidence/parameter basis and review epoch only. |
| `rejected` | Rechazado by the declared operator. |
| `review_required` | Revisión requerida, including invalidated earlier decisions. |

Every operator decision records an ID, append sequence, timezone-aware date
normalized to UTC, reviewer, reason, inventory ID, evidence/basis fingerprints,
review epoch and a link to the previous decision fingerprint. Reviewer/reason
must be nonempty; IDs must be unique within the dossier history. Approval of a
blocked piece is refused. Rejection, requesting review and returning to pending
are also decisions and retain their metadata. Append sequence determines the
latest decision; dates are declared audit data, not a trusted time service.

`ready_for_demo_use` requires current eligibility and a current approval. It does
not trigger any external action. The software does not manufacture human
approval; automated test records are explicitly labelled as test fixtures.

## Evidence changes and immutable revisions

SHA-256 is calculated over deterministic UTF-8 JSON with sorted object keys,
compact separators and nonfinite numbers rejected. Whitespace/object-key order
does not change the evidence fingerprint. Lists, source provenance, raw offers,
configuration, numerical results and traces are covered. Duplicate JSON keys are
rejected when reading files.

A separate review-basis fingerprint also covers DEMO parameters and the cost
rule version. Any evidence or parameter change invalidates previous approval
for the whole dossier. `refresh` increments a review epoch when that basis
changes. Returning later to older evidence/parameters cannot revive an approval
from a prior epoch; a new decision is required. Refreshing identical evidence
preserves approvals.

Each operation writes a new complete JSON revision with parent-revision
fingerprint, preserved decision history, action trace, evidence snapshot and
materialized cases. Existing files are never overwritten, even if input/output
paths coincide or another writer creates the target concurrently. Revisions,
decision chains and materialized states are verified before use.

Both `show` and `decide` require the current Delivery 03 evidence file. `show`
immediately reports obsolete approvals as `review_required`, with no readiness,
even before refresh. `decide` refuses stale evidence until a refreshed revision
exists. Historical files keep their historical state; they are never silently
rewritten or treated as a current approval without checking current evidence.

## CLI example

For a fresh checkout, first create the separate Delivery 03 scenario with
`python -m oem_agent.stage2 seed-stats`. The following PowerShell command saves
its structured evidence as UTF-8 with exclusive creation (avoiding shell-specific
JSON redirection encodings). Use a fresh file path if it already exists:

```powershell
@'
import json
from pathlib import Path
from oem_agent.stage3_statistics import execute_stage2_statistics

evidence = execute_stage2_statistics(Path("data/stage2-stats.sqlite"))
target = Path("runs/delivery04-input/statistics.json")
target.parent.mkdir(parents=True, exist_ok=True)
with target.open("x", encoding="utf-8") as stream:
    json.dump(evidence, stream, ensure_ascii=False, indent=2, allow_nan=False)
'@ | python -
```

Use that new evidence path in the commands below, or use the already verified
Delivery 03 artifact as shown.

Run from the repository root. Choose fresh output paths; all commands are local
and use only standard-library Python:

```powershell
python -m oem_agent.stage4 create --evidence runs/delivery03-verification/statistics.json --out reviews/delivery04-v1.review.json

python -m oem_agent.stage4 show --dossier reviews/delivery04-v1.review.json --evidence runs/delivery03-verification/statistics.json
```

After an operator actually reviews a case, they supply their own identifier and
reason. The following is a test-only example, not an instruction to approve:

```powershell
python -m oem_agent.stage4 decide --dossier reviews/delivery04-v1.review.json --evidence runs/delivery03-verification/statistics.json --inventory-id 10 --state approved --reviewer TEST-REVIEWER --reason "Test-only explicit decision" --decision-id TEST-D1 --out reviews/delivery04-v2.review.json

# Regenerate Delivery 03 JSON when source evidence changes, then bind a new revision:
python -m oem_agent.stage4 refresh --dossier reviews/delivery04-v2.review.json --evidence runs/updated-statistics.json --out reviews/delivery04-v3.review.json
```

`create`/`refresh` accept `--handling-cents`, `--packaging-cents`,
`--inspection-cents` and `--demo-units`. `--at` optionally supplies an ISO 8601
timestamp with timezone; otherwise current UTC is recorded. Dossier/decision IDs
are generated when omitted. Updates use a distinct required `--out` JSON path.

CLI exit 0 means the local operation succeeded, not that a case was approved.
`show` exits 1 when evidence changed, while still emitting the effective states.
Invalid inputs, blocked/stale approval, corrupted dossiers or existing outputs
produce exit 2 without replacing input files. Generated revisions belong in
`reviews/`, `runs/` or filenames ending `.review.json`, all excluded by Git.

## Verification and limitations

The suite adds 20 tests to the prior 84, covering invented math, missing versus
zero costs, all four states, mandatory metadata, UTC dates, approval gates,
changed/unchanged/reverted evidence, parameter changes, history preservation,
decision/revision integrity, malformed JSON, exclusive-file races, CLI lifecycle
and unchanged source evidence/SQLite bytes. Full verification uses 104 tests,
including the existing optional Excel tests.

There is no authentication or digital signature. Reviewer identity and decision
time are operator-declared; checksums detect inconsistency but do not prove who
performed a decision or prevent a person rewriting an entire checksum chain.
Files are local immutable versions, not a shared multiuser workflow: concurrent
outputs from one parent can form explicit branches and are not automatically merged.

No background watcher exists. Every use must supply the current structured
evidence; source DB or workbook changes are only reflected after regenerating
Delivery 03 JSON. The full-snapshot fingerprint is deliberately conservative:
even provenance or unrelated-piece changes require renewed review.

Full revisions retain their evidence snapshots and grow with history. A disk
write failure can leave a newly created partial file; it must be inspected and
replaced by choosing another output path. Existing files are never auto-deleted.

No web UI, authentication system, ERP, external service, LLM, scraping, scheduler
or automatic publication is introduced. This remains an entirely synthetic lab.
