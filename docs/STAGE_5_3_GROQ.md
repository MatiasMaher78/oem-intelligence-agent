# Stage 5.3: explicitly selected Groq Free adapter

Historical 5.3 implementation/verification record, accepted in commit 20fbc6b.
Current runtime instructions and provenance are maintained in [5.4](STAGE_5_4.md)
and its sole packaged skill; this history is not sent to the LLM.

This increment adds `groq_hosted.GroqClient` with standard-library HTTPS. Both
`run_local_agent` and `run_groq_agent` use the same private conversation loop in
`stage5_agent`. The five tool names, original default results, Harness permissions,
SQL, matching, percentiles, DEMO cost and human decisions are preserved. The
third-query follow-up adds the optional list_cases cost view described below;
the dispatcher only adds missing-dossier handling for that view.
No dependencies, datasets, local models, billing configuration or automatic
fallback are added. The older optional paid SDK demo remains isolated.

## Transport and native tool selection

The hosted adapter connects only to `https://api.groq.com:443`, with default TLS
certificate/hostname verification. Only GET `/openai/v1/models` and POST
`/openai/v1/chat/completions` are allowed. It ignores proxy/base-URL environment
routing, follows no redirects and never calls billing, file, batch, remote MCP or
provider tool-execution endpoints. Only the operator selects this provider.

The fixed initial model is `qwen/qwen3.8-27b`; it must be present, active and have
enough declared context in the authenticated model catalog. This is rechecked
before each inference; an unavailable model produces an error, not a replacement.
Requests use native `tools`, `tool_choice: auto`, `reasoning_effort: none`,
`reasoning_format: hidden`, `stream: false`, one choice and `service_tier: on_demand`.
There is no service-tier `auto` routing. `on_demand` is not proof of a Free account.

Groq function arguments arrive as JSON strings. The adapter strictly decodes
objects, rejecting duplicate keys, nonfinite numbers and invalid Unicode. Invalid
argument text is discarded and passed as an invalid value to the dispatcher so
the attempt is counted. Invalid/repeated native IDs also invalidate the attempt.
Tool names, shapes and schemas remain the dispatcher's responsibility. Its
validated results return with the native call ID; final text remains advisory.
No prose/keyword router or string-to-command interpretation is introduced.

## Free access and the limits of verification

The public documentation currently lists Free caps of 30 requests/minute,
1000/day, 8000 tokens/minute and 200000/day for this Preview model. These are
reference caps, **not verified remaining quota for this user's organization**.
The authenticated models endpoint checks availability; it does not certify the
account plan, enabled billing or current remaining daily allowance. No documented
read-only plan/remaining-quota endpoint was identified for this implementation.

Before inference the operator must verify the organization is Free in Console
and supply a `FreeQuota` confirmation, no more than 15 minutes old, with its
limits and remaining daily requests/tokens when available. Unknown remaining
values must be explicit JSON `null`, never zero, the total allowance or an
invented balance. Only `plan: free` and values no higher than the reference Free
caps are accepted. This is explicitly labeled operator
confirmation; reports keep `free_plan_api_verified: false`. It is not a security
certificate or independent proof that the key belongs to that organization.
Missing/stale confirmation, known exhausted balances and unconfirmed Free access
block inference. Unknown balances require the separate local budget described below.

Example confirmation structure, with real current observations substituted:

```json
{
  "checked_at": "CURRENT_ISO_DATETIME_WITH_TIMEZONE",
  "requests_per_minute": 30,
  "requests_per_day": 1000,
  "tokens_per_minute": 8000,
  "tokens_per_day": 200000,
  "remaining_requests_day": null,
  "remaining_tokens_day": null,
  "plan": "free"
}
```

The null values above explicitly mean unknown; they are not an account balance
or permission without the other confirmations and transfer authorization. Known
zero balances block. Do not invent remaining quota or infer Free from a key
prefix. Never put a key in this file.
No code upgrades a plan, adds payment details or enables billing. Staying on Free
and confirming the correct organization are operator prerequisites.

`LocalBudget` is a separate immutable batch ceiling: at most 9 inference requests
and 24000 tokens, configurable downward. It spans all questions on one client and
is reported separately from `operator_confirmed_free_quota`, whose unknown values
stay null. Account daily totals, known remaining balances and observed headers
all further restrict it; unknown does not mean unlimited. Local accounting cannot
know other clients' use or guarantee the unknown account allowance is sufficient.
The provider remains authoritative and any 429/authentication/unexpected limit
stops the batch without retries. POST limit headers differing from the operator's
RPD/TPM confirmation cause `unexpected_account_limits`, without accepting the
response body. Limits on metadata GET are not treated as model-specific proof.

The client reserves a conservative allowance, applies rolling minute and local/daily
budgets, then reconciles valid reported usage. The first request uses encoded
bytes as an explicitly labeled estimate; subsequent append-only requests reuse
the measured prompt count plus new encoded bytes and output/framing margin.
Changed prefixes or tool schemas fall back to the full-byte estimate. Unmeasured
failed requests retain their estimated reservation; valid reported counts replace
it even if a later protocol/finish check fails. Actual usage and outstanding
estimates are recorded separately. Observed quota headers also restrict subsequent
requests; metadata responses without headers do not erase the last observation.
The smoke batch reuses one client across questions. Other organization users can
consume quota concurrently: local counters cannot account for unseen activity.
429/errors stop without retry, waiting for capacity, paid tier or alternate model.

## Existing execution controls

The existing validated `OllamaLimits` value object is reused as execution limits
to avoid a second independent budget implementation; its name is retained for
compatibility. Defaults remain 9 model attempts, 45 seconds per HTTP request,
180 seconds per session, context 4096, output 512, question 4096 UTF-8 bytes and
64 KiB wire responses. Existing dispatcher limits remain 8 attempts / 2 errors;
every invalid model-selected attempt is counted. Exhaustion and sticky errors
cannot become a complete report. No business tool gets additional permissions.

Groq does not expose Ollama's `num_ctx` control. The existing encoded-input byte
cap is preserved without pruning history; actual prompt/completion counts must
sum correctly and remain within the local context/output limits. **The byte cap
and quota reservation are not exact tokenizers.** Without a tokenizer/preflight
token-count API, the local context limit can only be checked exactly after the
provider reports usage; an overflow is withheld as an error. This implementation
does not claim a hard pre-inference token count or silently truncate evidence.
Strict pre-inference token accounting would require separately reviewed work.

Timeout/watchdog/connection cleanup follow the existing local transport pattern.
Socket waiting is bounded after connection establishment; DNS/OS scheduling and
provider-side cancellation/freeing are not guaranteed instantaneous. Provider
errors are sanitized. The adapter records status, elapsed time, byte count and
only numeric allowlisted quota headers, never HTTP authorization headers/bodies.

## Credentials, evidence and accuracy

The smoke CLI explicitly loads the project's root `.env` with the standard-library
`groq_config.load_local_env` before constructing a client. Only `GROQ_API_KEY` and
`GROQ_MODEL` are imported into this process; existing process variables (including
empty values) win. Direct Python callers explicitly call this loader first or
supply process variables. The transport reads the key at request time, never
stores it on the client and never serializes it to reports/traces. There are no
import-time file reads, dotenv dependency or search through parent directories.

The loader accepts UTF-8/BOM, blank lines, full-line comments, `NAME=value` and
matching single/double quotes. It does not execute commands, interpolate values,
handle escapes/`export` or export unrelated names. Duplicate selected names,
malformed files, files larger than 16 KiB and invalid effective keys/models fail
with fixed redacted codes, before changing the environment. An existing `.env`
is never written by the loader. The only supported model is
`GROQ_MODEL=qwen/qwen3.8-27b`; any other value blocks, without fallback.

Edit `C:\dev\oem-intelligence-agent\.env` directly with a local editor and fill
`GROQ_API_KEY=` there. Never paste the key into Codex chat, a command argument,
quota confirmation or report. `.env.example` contains empty credential fields
and the public model name only. `.gitignore` excludes `.env` and `.env.*`, with
only the root `.env.example` exception. Ordinary Git add respects these rules;
explicit `git add -f` can bypass any ignore rule and must not be used for secrets.
This local file is plaintext, not an encrypted secret store; protect access to
your Windows account. No key is printed, hashed or recorded during verification.

Only the selected synthetic question, system instructions, five input schemas
and consulted tool projections leave the machine after authorization. The full
snapshot and review revision stay local. No remote party gets a database handle,
shell, free SQL, inventory/pricing write or review-decision capability. The model
cannot execute the tools; the local dispatcher always decides permission.

Reasoning fields and intermediate assistant text are discarded, without hashes
or raw-response logging. Explicit reasoning tags in final content are blocked;
unmarked prose cannot be perfectly classified. Final advisory text, normalized
tool calls/results, model rounds, latency, token/timing metrics, source hashes,
errors and local audit fingerprints are retained. Hosted model RAM is unknown.

Execution completion is distinct from semantic accuracy. The smoke test writes
`accuracy-review.json` with deterministic reference reads and evidence identity
for each question. These operator reference reads are not LLM tool selections.
Review the final prose against exact counts, prices, units/ads, evidence status,
DEMO cost and effective review state. Accuracy stays `pending_manual_review`;
`answer_verified` remains false until separately reviewed, never inferred from a
successful HTTP response or a substring. No additional evaluation dataset or
120-run evaluation is included.

## Operator smoke procedure

Offline tests always precede real calls:

```powershell
python -m unittest discover -s tests -v
```

Check local readiness without any network call (boolean output only; exit 1
when the key is missing or configuration is invalid):

```powershell
python -m examples.stage5_3_groq_smoke --check-config
```

The following metadata check **does contact Groq**. Do not run it until real
network calls have been authorized (it sends no business question or evidence):

```powershell
python -m examples.stage5_3_groq_smoke
```

After the operator authorizes transfer of synthetic data, verifies current Free
access/quota and configures the local key, the prepared opt-in command is:

```powershell
python -m examples.stage5_3_groq_smoke --allow-synthetic-transfer --free-quota runs/current-groq-free-quota.json --evidence runs/delivery03-verification/statistics.json --dossier reviews/delivery04-v1.review.json --out runs/groq-first-three --questions 3
```

That command is documentation, not authorization. The output directory must be
new; all reports use exclusive creation. The three questions are reused from the
existing Ollama smoke example. The batch stops at the first incomplete/error
result and never exceeds three questions. Each question retains the existing
model/tool/time budgets; the shared provider quota spans the batch. Cancellation
exports the attached sanitized report. The initial implementation was offline; controlled real results are recorded below.

## Sources and verification status

- [Groq native tools](https://console.groq.com/docs/tool-use/local-tool-calling).
- [API and model metadata](https://console.groq.com/docs/api-reference).
- [Model, reasoning controls and Preview status](https://console.groq.com/docs/model/qwen/qwen3.8-27b).
- [Free quotas and headers](https://console.groq.com/docs/rate-limits).
- [Billing prerequisites](https://console.groq.com/docs/billing-faqs).
- [Provider data retention](https://console.groq.com/docs/your-data).

At initial implementation no GROQ_API_KEY was present in the environment, so
real availability, quota, latency and accuracy were initially unverified. Offline
simulations alone never authorize or validate hosted inference. The subsequent
explicitly authorized controlled run below records the actual observations and
the distinction between operator confirmation and API evidence.


## Local configuration verification, 2026-11-05

The full offline suite passes **247 tests**: all 199 original tests, 32 existing
Groq protocol tests and 16 credential-configuration tests. The full command ran
with `PYTHONWARNINGS=error::ResourceWarning`; no open-database warnings occurred.
Protocol CLI tests mock the loader so they never load operator credentials.
The offline configuration check reported availability booleans only; the key was
missing at that check. The ignored local .env was created with an empty key field.

Git checks confirm .env is untracked and common variants are ignored, with only
the root template allowed. The template has empty credentials. Pattern scans of
46 tracked index files and 183 working text/Excel files (126 reports/traces) found
no credentials; .env and credential stores were excluded. These checks cannot
prove absence of every possible secret format. Original databases/reports and
original 199 test files remain unchanged. Fresh temporary fixtures still produce
6 original cases, 7 reads and 27 Stage 2 selections.

Evidence: `runs/groq-env-verification-20261105-5d3cfc7f/` (ignored, new directory).
No Groq network calls or inferences, commits, publishing, dependencies or system
configuration changes were performed in that offline preparation. Subsequent
model availability and controlled real results are recorded below. No automatic
paid fallback exists.


## Controlled real verification, 2026-11-05

The authorized technical adjustment changes only the Groq adapter, its tests and
these documents. Nullable daily balances stay unknown in the trace, separately
from LocalBudget (9 inference requests / 24000 tokens maximum). Eight new tests
cover nullable validation, local call/token ceilings across questions, known
account limits, unexpected POST headers and failed-request accounting. The full
suite passes **255 tests** in 30.314 seconds, retaining all earlier 247 tests and
original 199 tests, with ResourceWarning promoted to errors and no open databases.

The operator confirmed Free access and 30 RPM / 1000 RPD / 8000 TPM / 200000 TPD.
Exact daily remaining balances were initially unavailable and recorded as null.
An authenticated catalog GET confirmed the model active with context 131072.
All six real POST responses independently exposed RPD 1000 and TPM 8000; the last
observed daily request remainder was 994. Daily token remainder remains unknown.
Free billing status, RPM and TPD are operator-confirmed, not API-certified.

Three exact user questions were executed sequentially with one shared client,
manual evidence review before continuation, and proactive pacing between questions.
Per-question limits were reduced to 4 model attempts, 30 seconds/request and 90
seconds/session; context 4096, output 512 and dispatcher permissions stayed unchanged.

| Question | Native selections | Result | Session latency |
| --- | --- | --- | --- |
| Analyzed pieces and sufficient evidence | summarize_run | Correct: 27 analyzed, 1 sufficient, 2 insufficient, 24 without comparable offers | 2.044 s |
| Piece 10 market statistics | inspect_case(id=10, statistics) | Correct: observed 10 ads/55 units/EUR 10?100; filtered 8 ads/44 units/EUR 20?90; P10/P90 cutoffs EUR 19/91 | 4.818 s |
| Cases blocked for DEMO costs and reasons | list_cases(limit=5), then issued cursor | Partial: 10/27 pieces read, unfinished pagination; no final answer | 3.736 s |

The third question stopped with the existing local `free_quota_exhausted` code
before a third POST. The previous context was 5844 encoded bytes; the next tool
projection alone added 2564 bytes. Even a lower bound for the next reservation
was 9432 (bytes + output/framing), above 8000 TPM. This is conservative local
reservation exhaustion, **not a provider 429 or proven exhausted daily balance**.
No retry, bypass, budget increase or additional question followed. This code uses
bytes as a token bound, not an exact tokenizer, so paginated history can block
requests that might fit by actual tokens. No context/accounting improvement was
introduced in that quota-only adjustment. The separately authorized follow-up
below closes the third-query problem.

The six successful inferences used 7644 prompt + 632 completion = 8276 total
tokens across paced windows; four native tools executed. All HTTP responses were
200, with no provider/authentication/429 error. Two complete answers passed an
operator comparison to deterministic evidence. The partial third answer cannot
be scored correct; local reference data contains 26 cost-blocked pieces (all
selected except 10), and all 27 human reviews remain pending. That reference is
not an LLM answer. Audit references include evidence SHA-256 and JSON pointers;
individual reports retain advisory/answer_verified=false, with separate evaluation
records. No human business decision was recorded.

Conclusion: real native tool calling and evidence-grounded answers are demonstrated
for the first two questions. Full three-question acceptance remains unmet because
the third exceeds the current context/quota reservation workflow. Error and
incomplete-page controls correctly withhold a complete result. This small test is
not a general accuracy benchmark; hosted model memory/resources were not exposed.

Evidence: `runs/groq-real-validation-20261105-f1a5bca3/`: tests.txt,
test-summary.json, preflight.json, question-1/2/3.json, accuracy-review-1/2/3.json,
metrics-and-diagnosis.json, batch-status.json, preservation/security checks and
report.md. No additional dataset, dependencies, business rules, inventory/prices,
review decisions, billing settings, commits or publications were changed.


## Third-query closure, 2026-11-05

The previous trace contained 5 full inventory rows per page (1383/1386 encoded
bytes), 616 bytes of repeated reference hashes and 383 bytes of outer envelope.
Neither page contained cost block reasons. The two real inferences consumed
2943 prompt + 119 completion = 3062 tokens; 9432 was only a lower bound for a
future reservation, not consumption. Reading cost review individually for 27
pieces would exceed the unchanged 8-attempt dispatcher budget.

Minimal additive read-contract adjustment: `list_cases(view="costs", limit=20)`
uses the already pinned `dossier_view` to return per-piece inventory_id, eligible,
block_reasons and review_state, plus needs_refresh and normal pagination. It does
not recalculate commercial rules. The ordinary inventory view, other four tools,
5.1 contract version, snapshot IDs, read permissions and 20-row/8 KiB limits are
preserved. Cursors bind the view and filters. The two necessary dispatcher changes
allow a missing dossier to be not_available for this view and avoid treating it
as a successful page; missing/partial/error evidence still cannot finish complete.

The model projection losslessly groups identical eligibility/reason/review facts
and factors each reference hash once with all original pointers. Full per-piece
rows and references stay in the local audit. Grouping chooses no tool or business
action; Qwen selects every call natively with tool_choice=auto. Guidance and the
tool schema advertise the cost view and efficient pages, without keyword routing.
Two 20/7-row results measured 6432/2892 bytes (both below 8192), while the model
projections measured 1382/1021 bytes. All 27 cases and evidence pointers plus the
dossier reference round-trip without loss.

Prompt reservations now use actual prompt_tokens for an identical previous
transport-message prefix and unchanged tool schemas, plus the newly appended
encoded bytes, max output and existing framing margin. Hashes of prepared
messages are cached only after successful validation; no private reasoning is
cached or logged. New questions, changed prefixes or changed schemas fall back
to the full-byte estimate. This remains an estimate, not an exact tokenizer or
strict preflight token guarantee. The byte/context guard and reported usage caps
remain enforced. Reported tokens, estimated outstanding charges, reservation basis
and per-round usage are exposed separately. Valid actual usage is counted even
when a later finish/limit check fails; a provider error with no usage keeps only
an estimated charge. No published quota or safety budget was raised.

Thirteen new offline tests cover complete two-page reads, every row/reference,
default compatibility, bound cursors, absent/changed/partial evidence, invalid
arguments, native selection, early completion, provider failures, measured-prefix
reuse/reset and real usage on errors. Full suite: **268/268**, including all prior
255, in 30.032 seconds, ResourceWarning treated as errors, no open-database warnings.
The earlier 20-row full-inventory view still hits the original response-size guard;
its normal smaller pages remain compatible. No existing test was weakened.

Only the third question was repeated after tests passed, using the unchanged
synthetic statistics and dossier. The latest user authorization reviewed the
existing operator-confirmed Free configuration; no new Console screenshot or
independent billing verification was claimed. Daily remaining values stayed null.
Conservative run ceilings: 4 inference requests / 8000 local total tokens,
4 model attempts, context 4096, output 512, 30 seconds/request, 90 seconds/session.

Actual result: complete and correct. Qwen selected list_cases(view=costs, limit=20)
and then the issued cursor with the same view/limit. It read 20+7 pieces, retained
all references and answered after three real inferences / two native tool calls.
Time: **4.785 seconds**; real tokens: **4862 input + 402 output = 5264**.
Per-round prompt/output counts: 1004/39, 1658/110, 2200/253. Reservations:
3990, 3783, 4121; largest prior actual usage + next reservation: **6932 <= 8000**.
Every HTTP response was 200, with RPD 1000 / TPM 8000 confirmed in POST headers;
zero errors, 429s, retries or extra questions. No billing or fallback was enabled.

The answer enumerated exactly IDs 2, 3, 7 and 11 through 33 (26 total), with
insufficient_or_unusable_statistical_evidence and missing_filtered_evidence;
ID 10 was the only simulation-eligible piece and all human reviews stayed pending.
Every ID/reason/state was checked against the existing per-piece review_status
reader. The agent's answer_verified=false/advisory-only report was preserved;
a separate operator accuracy record passed. No human decision was recorded.

Evidence: `runs/groq-third-fix-20261105-a807fd34/`, including original-trace analysis,
full test log, real question/metrics, exact reference comparison, preservation and
secret-pattern checks. This closes the single third-query failure on the existing
synthetic snapshot; it is not a broad model-quality benchmark. No other real
question, dataset, dependency, business feature, commit or publication was added.
