# Stage 5.5: reproducible independent evaluation

Stage 5.4 was accepted and committed selectively as
500f3fdb2a53488a125e7618148b26c86d82f980 after 283 passing tests.
The initial increment added evaluation fixtures, scoring, an offline/opt-in real
runner and tests. Authorized follow-ups then compacted agent-facing tool results,
added pre-validation reservation traces and an exhaustive matching reader summary,
and updated the single runtime skill to **1.0.2**. The evaluator is 5.5.1; the
configuration format remains 5.4.0 and fingerprints the effective skill and limits.
The five contracts, dispatcher permissions, SQL, business math, inventory prices
and human-review decisions are unchanged. No provider, framework or dependency
was added. Publication corrections update documentation and CI only. Stage 5.5
is technically closed and recorded in Git commit
`267a8af6904560c0d4bb509712612235b1054b31`.

## Final technical closure

The initial evaluation was closed on 2026-11-06. Documentation was corrected on
2026-11-07. The following real rows are the latest authorized execution per case;
earlier partial attempts are not erased or counted as additional successful cases.

| Evaluation | Skill | Final result |
| --- | --- | --- |
| Offline | 1.0.2 | **359/359 tests, 5/5 scripted scenarios, 12/12 negative controls** |
| E1 real, Groq Free | 1.0.1 | **Approved, 10/10 structured fields**, complete final answer |
| E2 real, Groq Free | 1.0.2 | **Partial / not approved**, additional exploration and preventive quota block |
| E5 real, Groq Free | 1.0.2 | **Partial / not approved**, preventive block before tool results reached the model |

| Latest real execution | Sent inference POSTs | Read tool calls | Real input + output tokens | Agent latency |
| --- | --- | --- | --- | --- |
| E1 | 3 | 2 | 4815 + 251 = **5066** | 3.483 s |
| E2 | 2 | 3 | 3817 + 129 = **3946** | 2.451 s |
| E5 | 1 | 3 | 1229 + 117 = **1346** | 1.646 s |

These timings exclude initial operator preflight. Across all six recorded 5.5
real attempts (including earlier partial E1/E2 attempts), there were 11 inference
POSTs, 18 read calls and 19068 reported tokens. That historical sum spans separate
quota windows; it is not TPM, an account balance or independently verified billing.

E1 used an earlier skill and does not establish success of every scenario under
1.0.2. E2/E5 produced no final answer. In E5 the local readers preserved five known
B offers/15 units, the A source error and partial coverage, but the next POST was
blocked: those facts are not a correct LLM explanation or demonstrated abstention.
A complete explanation can be valid while evidence coverage remains partial;
absence of a final answer prevented approval of this real E5 execution.

Offline protocol transcripts echo authored facts and validate the evaluator and
harness, not LLM intelligence. The small, mixed-version real sample supports no
statistical reliability percentage. Hash-bound references verify source identity,
not semantic accuracy; free prose still requires human review. All fixtures,
prices and costs are invented. No inventory/price mutation, human approval or
commercial action can be performed automatically by the agent.

E2 and E5 are closed without further inference or optimization. No new real call
is authorized by these instructions. Local ignored evidence is retained at:

- `runs/stage5-5-matching-summary-20261106-27f8901a/`: 359-test log, five offline
  cases, twelve controls and source fingerprints.
- `runs/stage5-5-e1-real-20261106-858eb425/`: approved E1.
- `runs/stage5-5-e2-renewed-real-20261106-26b38ddf/`: final partial E2.
- `runs/stage5-5-e5-real-20261106-b2aa828b/`: partial E5 and consolidated history.

These generated paths are excluded from Git. The public tables record the
reviewed results; future offline outputs can be regenerated without credentials.

## Compact context and preventive reservation trace

From the first tool read, the agent-facing projection omits redundant session
metadata and a repeated evidence hash when that identity is already in its
references. Business data, IDs, references, coverage, errors, warnings and remaining
call count are retained. Full result envelopes and session metadata stay in the
local dispatcher trace. This is a transport projection, not a business summary
that invents facts or a Python tool-selection router.

Skill 1.0.1 added the general instruction to answer once all requested dimensions
have sufficient evidence. Skill 1.0.2 additionally explains when matching
aggregates suffice and when granular offers are needed. No additional permissions
are granted. The exhaustive aggregate is described in the follow-up section below.

Before each preventive validation, the adapter records the estimated reservation,
its basis, available real usage, observed remote/local bounds and any blocking
reason. Reservations include the unchanged output and safety allowances. A byte
estimate is never labeled real token consumption; an unchanged measured prefix
may reuse reported prompt usage plus a conservative new-byte estimate. Unknown
remote daily balances remain null and bounded local ceilings still apply.

Free caps remain 30 RPM / 1000 RPD / 8000 TPM / 200000 TPD, subject to fresh
operator confirmation before any future authorized run. Availability and headers
do not certify plan/billing. No margin, context/time/call bound, retry policy or
fallback behavior is relaxed. For real E5, the second reservation was 7281 versus
6259 last-observed remaining TPM; local measured usage plus that reservation was
1346 + 7281 = 8627, above 8000. The POST was withheld and execution stayed partial;
7281 is a reservation, not additional consumed tokens.

## Independent dataset and authored ground truth

Dataset version: oem-eval-5.5-v1. Five IDs 1001-1005 do not overlap the original
27 selected pieces. Each case gets its own one-piece temporary SQLite database,
using the existing schema, selector, matching and statistics functions. The same
two synthetic marketplace IDs are required by the unchanged five-tool contracts.
The original seed/constants, persisted databases and reports are never extended.

All OEMs/vehicles/prices are invented. Numeric OEMs are TEXT, never integers.
Ages 60/65/75/85/90 and stock 1/2/3/4/5 satisfy the existing selector. Fixed dates
and one pending DEMO dossier per case make snapshots reproducible. Ephemeral DB
paths are replaced only in source.database by explicit synthetic dataset labels;
SQL results/traces and math are preserved. Temporary databases are closed/deleted.

| Case | Literal OEM | Scenario and manually authored oracle (EUR cents) |
| --- | --- | --- |
| E1 | `0000421001` | Possible price anomaly: 10 exact/compatible ads, 55 units, observed 1000-10000, P10/P90 1900/9100, retained 8 ads/44 units and extrema 2000-9000. Hypothetical question price 19000 exceeds that band. Prefix trap `00004210010` remains excluded/audited. |
| E2 | `EV5A0102B` | One exact/incompatible, one exact/unknown compatibility, one unknown OEM/confirmed compatibility, one different OEM/confirmed compatibility (zero units). No usable comparable offers; extrema null, catalog not assessed. Abstain from validating prices. |
| E3 | `EV5-AB/03.01` | Competitive only in a DEMO band sense: 10 comparable ads/20 units, observed 5000-14000, P10/P90 5900/13100, retained 8 ads/16 units and extrema 6000-13000. Hypothetical question price 6500 is inside that band. Separator trap `EV5AB0301` excluded/audited. |
| E4 | `9990000004` | Both source reads succeed with no offers. Known zero ads/units, absent prices null, catalog not assessed. Abstain from validating prices, retain piece. |
| E5 | `EV5-X/05` | Explicit source A fault; B retains 5 ads/15 units, observed 7000-11000. Incomplete coverage, source_error, no filtered band or DEMO cost. Abstain from validating prices; agent execution must remain partial. |

Price comparisons use values explicitly provided in the question. The inventory
schema has no current sales price: none is added or inferred. These labels are
finite evaluation expectations, not a new commercial classifier, recommendation
or statistical claim about a real market. E2 distinguishes OEM identity from
compatibility; its offers do not prove an inventory catalog defect.

GROUND_TRUTH is a versioned set of authored literals in stage5_eval_data.py;
no LLM or prior model answer generates it. Tests cross-check those literals against
the existing deterministic pipeline and explicit percentile cutoffs/units.
This final E3 design uses ten offers to evaluate a filtered band; the historical
5.1 proposal of nine was only a plan and remains unchanged as history.

The E5 fault is injected explicitly with standard-library unittest.mock at the
existing source-read boundary. All other queries are real local SQL. Existing
_sql_read records the thrown sqlite3.OperationalError. No failed read is relabeled
as an empty success. Per-case snapshots isolate its incomplete global coverage
from E1-E4. Fixture fault injection is not part of the agent/provider path.

## What is scored

Questions request a small JSON facts object and a Spanish explanation. Only field
names, format constraints and hypothetical comparison prices are in the question;
expected numeric results are not supplied. Every real tool choice remains native
LLM tool calling with tool_choice=auto and all five existing schemas. Evaluation
code is operator-side and is never a dispatchable tool or a question router.

Checks are reported separately and all are required for a scenario pass:

- Strict finite facts against the authored oracle: exact fields/types, units vs
  ads, integer cents, literal OEM including zeros, null vs zero, DEMO assessment.
- Abstention from validating prices for E2/E4/E5; this differs from refusing to
  explain known facts. E4 can be a complete answer about absence; E5 cannot claim
  complete source coverage even with a correct explanation.
- Relevant accepted native reads: precomputed statistics for E1/E3; matching with
  an exhaustive, verified aggregate or a complete granular audit for E2;
  statistics plus matching for E4/E5.
  The rubric allows extra permitted reads and does not demand a fixed sequence.
- Hash-bound references actually returned by reads, exact attached reference list
  and footer, resolvable snapshot/dossier pointers. No unrelated source substitution.
- Report/dispatcher/event chain integrity and deterministic tool-result replay.
- Coverage, pending pages, sanitized errors and unchanged/narrower enforced limits.
- Configuration version/skill/prompt/schema identity and trace/timing presence.

The rubric intentionally checks precomputed statistics rather than rewarding a
model that tries to reconstruct percentiles from raw pages. It does not accept
correct-looking answers without those necessary reads. The accompanying mutation
tests demonstrate rejection of wrong prices/counts/types/OEMs/abstentions,
foreign/unread references, tampered traces/configuration and incomplete pages.

Free-form explanation accuracy always remains pending_manual_review, and the
agent report keeps answer_verified=false. Exact structured facts and valid sources
do not prove every sentence, implication, citation-to-claim mapping or business
judgment. No LLM judge, semantic keyword grader or approval action is introduced.

## Offline run

From a fresh repository checkout, use an isolated environment with the existing
Excel extra. No API key, `.env`, local model or paid service is needed:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[excel]"
.venv\Scripts\python -W error::ResourceWarning -m unittest discover -s tests -v
.venv\Scripts\python -m oem_agent.stage5_eval offline --out runs/eval-5-5-offline-01
```

Without `.[excel]`, 11 XLSX tests are intentionally skipped; a run with those skips
is not the recorded 359/359 no-skip verification. CI now installs the extra,
verifies the openpyxl import and runs both the complete suite and offline evaluator.
Package installation may use the Python package index; evaluation itself uses
only local synthetic evidence and simulated provider frames. Existing generated
outputs are protected: repeat with a new `--out` directory, without overwriting.

The default covers all five cases. Outputs require a new directory; every file
uses exclusive creation. No .env load, credential, socket, model or service is
needed. Scripted native protocol frames exercise the real adapter normalization,
agent loop and dispatcher. They are explicit test transcripts that echo the
oracle, not a substitute model or evidence of real LLM quality.

Twelve additional offline negative controls cover forbidden shell/approval,
malformed arguments, unknown inventory, repeated reads, reduced call budget,
unfinished pages, missing review and simulated 429/auth/context/time failures.
Invalid attempts count. No error/budget exhaustion is presented as complete;
failed requests are not retried. Existing tests retain broader original coverage.

Exports include dataset identity (version, ground-truth/question/snapshot hashes),
each evidence/dossier, full agent traces, evaluation results, controls and summary.
Repeated inputs produce identical snapshots/ground-truth identity and check results;
wall-clock timings, timestamps and complete report hashes can legitimately vary.

## Opt-in real runner: initial evaluation closed

The authorized initial E1/E2/E5 runs are closed as recorded above. The command
below documents the existing opt-in interface, not a request to repeat them.
Any future real evaluation requires new explicit authorization and fresh account
confirmation, with at most three questions per run. Never repeat a failed question
automatically, replace a provider/model or activate billing. This publication
correction performs no inference or model-availability request.

```powershell
# Do not execute until separately authorized. Quota file contains no API key.
python -m oem_agent.stage5_eval real --cases E1 --allow-synthetic-transfer --free-quota runs/free-quota-confirmation.json --out runs/eval-5-5-real-01
```

Use the existing Groq qwen/qwen3.8-27b, .env loader and FreeQuota format. Console
confirmation must be at most 15 minutes old with plan free and the actual caps;
unknown remaining daily balances are null, not invented. The existing client
checks active model before inference and enforces 30 RPM / 1000 RPD / 8000 TPM /
200000 TPD (or lower confirmed limits), plus one shared batch ceiling of nine
requests / 24000 tokens. Existing request/session/context/output/tool limits remain
in force; per-request 45 s and per-question 180 s. No sleeps, retries or budget reset
are introduced. A new run requires a new explicit operator action/authorization.

One question runs at a time. Any failed check or incomplete execution stops the
real batch, including the deliberately partial E5; already correct E5 facts can
pass their rubric without relabeling that execution complete. Attempting to run
later questions then reports all_requested_evaluated=false. A 429/auth/unexpected
quota limit stops via the existing adapter. Output paths/case counts are validated
before loading credentials or preflight. Credentials are never printed or exported.

Metrics retain actual provider usage separately from reservations/unreported
estimates. Successful-completion input/output tokens are totaled; incomplete usage
is explicitly flagged, while provider_budget retains known total usage including
failed completions when available. Offline metrics are marked simulated_protocol
and cannot be claimed as real token use or model performance. Hosted hardware is
not observable. A structured-output failure stays a failure, never a corrective
second inference. Human review of explanations is still required.

## Verification record

Detailed final test counts, timings, hashes, preservation/security checks and an
operator review are under the ignored directory:
`runs/stage5-5-verification-20261105-f368bf9f/`.

The verification records below describe pre-commit checks; no publication was
performed. The existing 27 pieces, six-case original demo, statistical/cost/review
results and prior real traces are preserved.

## Historical initial offline results (2026-11-05)

The counts, zero real calls and source/Git statements in this section describe
only the initial 5.5 milestone before the authorized follow-ups. They are not the
current closure totals or a claim that the agent/skill remained unchanged in the
completed increment. See the final closure and follow-up verification above/below.

- Full suite: **315/315 passed in 21.693 s**, including all 283 unchanged earlier
  tests and 32 new evaluation tests. ResourceWarning treated as error; no open
  resource warning occurred. No environment credentials were available to tests.
- Five scripted scenarios: **5/5**, all ten check categories **5/5**. Deliberate
  E5 source failure remains complete=false while its correctly scoped facts pass.
- Negative controls: **12/12**. Scenario transcripts used 11 simulated model rounds,
  9 native read calls, zero harness errors and one explicit source error.
- Final offline process: 0.810 s including fixture generation/export. Per-case
  observed harness latencies are in offline-reviewed-metrics.json; they are not
  LLM inference benchmarks. Protocol tokens are synthetic, never actual usage.
- Dataset SHA-256:
  `9b81e56398055d431c9cd3a1c67e9951bb779ed1c56e1b185923a9bae84bc548`.
  Repeated builds retained identical snapshot/dossier/question/dataset identities.
- Fresh original pipeline: 6 cases/7 reads, unchanged DB. Existing statistics:
  all 27 results equal prior evidence, 26 DEMO-blocked, every human review pending.
- SHA checks preserved 124 earlier generated artifacts, 17 latest-real-run files
  and 14 Stage 5.4 verification files. All existing agent/provider/skill/SQL/math
  and test source hashes remained unchanged. Only evaluation and docs were added.
- Secret-pattern scan: 355 text source/report files, zero findings. .env was never
  read, printed or hashed; ignored/untracked, with a blank .env.example key.
- HEAD remains 500f3fd, Git index empty, no remotes or publication. Seven pending
  paths constitute 5.5; no commit. Real inferences executed: **0**.

The verification directory contains stage54-commit.json,
stage55-final-tests.txt, stage55-final-tests-summary.json,
offline-reviewed/summary.json, offline-reviewed/controls.json,
offline-reviewed-metrics.json, preservation-security-review.json and review.md.
Earlier preliminary runs are retained with separate names, never overwritten.


## Matching aggregate follow-up: offline verification on 2026-11-06

`inspect_case` with `section=matching` now retains its earlier fields and adds the
literal inventory `oem` and `offer_summary`. No input/output contract or permission
changes are required: the existing version 5.1 content object already carries
versioned read projections. Five tools, the dispatcher, provider budgets, SQL and
business math remain unchanged. The runtime skill is 1.0.2 and evaluator 5.5.1;
configuration hashes record the new skill/prompt identity.

The summary groups the already audited `oem_match` and `compatibility` labels; it
does not rerun matching, infer eligibility, sum units or calculate new prices.
`count` counts unique `(source_marketplace_id, offer_id)` identities. `offer_ids`
keeps literal IDs under their marketplace, so equal IDs from two sources stay
independent. Every group includes `scope=case`, `coverage_complete` and full
hash-bound JSON Pointer references for every original audit row. The summary
scope is `case_unique_offer_ids`; `audit_rows_count` includes duplicate source
rows while `offers_count` and group counts count an identity once. Repeated rows
with the same classification retain all references. Their duplicate/conflict
flags remain unchanged in `list_offers` and the pinned evidence.

Coverage requires both a complete source audit and complete matching for the
case. Unknown/incompatible/different labels remain separate. Empty known evidence
can have zero groups with complete coverage; an error can also leave zero known
groups, but coverage remains false and original marketplace errors are retained.
A classification is not proof that an offer is usable: the existing statistics
still account for invalid prices, stock, duplicate identities and other exclusions.

A missing/invalid source identity produces `unidentifiable_offer`; one identity
with contradictory audited classifications produces `ambiguous_offer_identity`.
These are explicit reader errors, not zero counts or a choice of classification.
The unchanged granular tool still exposes the full original rows. Large summaries
can hit the existing 8,192-byte response guard and fail without truncation; this
increment does not add another pagination contract or raise any bound.

E2 keeps its authored ground truth. Its tool-use check accepts an exhaustive
aggregate identical to the pinned source projection, including counts, typed
values, IDs, scope, coverage and references; alternatively it accepts the exact
multiset of all granular audit rows plus the matching read. Open cursors still
fail both the coverage check and E2 completion criterion, even if an aggregate is
available. Correct-looking final facts alone are insufficient. All existing
integrity, exact-answer, abstention, source-reference and error checks remain.
The default offline E2 transcript demonstrates matching plus statistics followed
by its scripted final answer; a separate regression preserves the complete
paginated granular path. This changes no real model selection or agent routing.

The new tests cover all nine label combinations, unrelated inventory IDs,
marketplace-scoped identities, repeated/conflicting IDs, missing IDs, empty and
partial sources, complete reference coverage, unchanged granular output, strict
rubric mutations and the existing size guard. Full test/context measurements are
recorded in the new ignored verification directory for this follow-up. Offline
transcripts do not measure real model quality or real token savings; a new
explicit authorization is required for any hosted inference.

Verification on 2026-11-06: all 359 tests passed (337 existing + 22 new), with
ResourceWarning treated as an error. All five offline cases and twelve negative
controls passed; dataset and ground-truth hashes were unchanged. For equivalent
exhaustive E2 evidence, serialized tool content fell from 8,454 bytes (five granular
reads) to 3,566 (matching plus statistics), a 57.819% reduction. Reconstructed final
contexts with fixed native call IDs fell from 13,494 to 8,069 bytes, including the
new skill. The baseline full granular path includes the formerly unread second
page and is a reconstruction, not a successful prior real execution. These are
byte measurements, not measured model tokens or a guarantee of admission under
8,000 TPM. This verification was offline; the subsequent authorized real E2/E5
outcomes are recorded in the final technical closure above, without approval.


## Publication correction verification (2026-11-07)

Only README, architecture, decisions, this document, the historical Groq guide
and CI were edited in this correction. Runtime Python, tests, fixtures, skill
1.0.2, five tool contracts, harness limits and ground truth were not changed.
The historical absolute .env path was replaced by a repository-relative path.

A fresh temporary source copy contained no .env and used a new virtual environment.
Installing the existing `.[excel]` extra succeeded without the paid SDK. With
provider credentials removed from the child environment, Python 3.13.4 and
openpyxl 3.1.5 produced:

- **359/359 tests, zero skips**, including all **11 ExcelExportTests**; full-suite
  process time 22.727 s. ResourceWarning was treated as an error; none occurred.
- **5/5 offline scenarios and 12/12 negative controls**, process time 0.660 s.
  Dataset SHA-256 remained
  `9b81e56398055d431c9cd3a1c67e9951bb779ed1c56e1b185923a9bae84bc548`.
- No new real inference or model-availability request. E1/E2/E5 results above remain
  historical and closed, not replaced by this offline verification.

CI now installs `.[excel]`, requires a successful openpyxl import before unittest,
runs with ResourceWarning as an error and then executes the offline evaluator.
The dependency prevents the optional Excel skip condition in this configuration.
Those steps were reproduced locally in the clean Windows environment; the hosted
Ubuntu/Python 3.11 GitHub Actions job has not been run or claimed as passed.

Detailed local logs and integrity/security checks are retained only in the ignored
`runs/stage5-5-publication-close-20261107-3ee19604/` directory. During that
verification no .env was opened or printed, and the verification itself created
no commit or publication. Secret-pattern scanning is a bounded check, not a
guarantee of absence of every secret format.
