# Stage 5 increment 5.3: local Ollama native tool loop

This increment implements a single advisory agent using `qwen3:4b` through the
native Ollama HTTP API. `ollama_local` supplies the local-only transport;
`stage5_agent` supplies the bounded conversation loop. Existing readers,
dispatcher, five tool contracts, SQL, business math, fixtures and review decisions
are unchanged. There is no SDK/framework dependency, paid provider, scraping,
scheduler, approval tool, general SQL/shell tool or commercial write.

## Native selection, not a simulated agent

The program sends the five existing input schemas as native `tools` definitions.
The model selects names and argument objects in `message.tool_calls`; Python
passes each request to `ReadOnlyDispatcher`. Model-selected requests, including
invalid ones, consume that dispatcher's existing attempt/error budgets. Its
structured results return as `role: tool` messages, with tool names and native
call IDs when supplied. The model may select further tools or return final text.
The same question can produce different tool selections in protocol tests.

There is no business-question keyword router, scripted tool selection or parsing
of assistant prose as executable calls. JSON-looking prose is never executed.
Native arguments must be objects; strings, missing fields and malformed calls
are charged/rejected, not repaired into invented tool requests. An oversized
native batch (more than eight calls) is rejected before execution: all its calls
are counted as observed/rejected in the outer report, distinct from dispatched
attempts. In an accepted batch every entry is dispatched/countable, including
entries denied after a preceding call stops the dispatcher.

For context efficiency, tool messages project the existing validated envelope
to data, coverage, references, warnings, errors and session controls. No business
values change; full original results and the dispatch trace remain in the local
audit. The conversation itself is not persisted.

## Local-only preflight and transport

The only destination is literal `http://127.0.0.1:11434`. The client uses
`http.client.HTTPConnection`, ignoring proxy/`OLLAMA_HOST` environment routing.
It never follows redirects or calls pull/push/create/delete endpoints. There is
no alternate host, provider or automatic provisioning path.

Preflight requires Ollama at least `0.40.14`, the reviewed and installed runtime.
The older `0.34.2` protocol floor is not retained as a security baseline. This
version gate is not certification that a runtime is free of vulnerabilities.
It checks `/api/status` for `cloud.disabled: true`, the exact locally listed model
name/digest/size, absence of remote-model fields, tool capability, Qwen3 family
and declared context capacity. Unknown/older status APIs fail closed. Setting an
environment variable in the client alone is not accepted as proof of server
configuration. Cloud state and the pinned digest are rechecked before inference.

Native chat responses must identify the same local model, finish with `stop` and
provide valid token counts. Missing/remote models, HTTP failures, incomplete
generation, model changes and unexpected protocol shapes cannot produce a
complete agent report. JSON decoding rejects duplicate keys/nonfinite values.
Responses are capped at 64 KiB; error bodies are never logged.

## Limits and completion

| Control | Initial value / maximum |
| --- | --- |
| Model request attempts | 9 / 9 |
| Tool attempts / error threshold | Existing dispatcher: 8 / 2 |
| Whole session | 180 seconds / 180 seconds |
| Individual HTTP operation | 45 seconds / 45 seconds, bounded by session deadline |
| Context | 4096 tokens / 8192 tokens |
| Output per inference | 512 tokens / 1024 tokens |
| Question | 4096 UTF-8 bytes / 4096 bytes |
| Transport response | 65536 bytes |

Every model request attempt is counted before inference. No retries or automatic
new sessions are introduced. Reaching the model-attempt budget cannot be reported
as complete, including a final response on the last slot. Dispatcher errors stay
sticky; its stopped/exhausted/error state cannot be bypassed by subsequent model
text. Source coverage, missing reviews and incomplete pages retain 5.2 semantics.

The adapter also caps encoded message/schema bytes at four times the available
input-token allocation. This is a separate memory bound, **not** an exact token
estimate. Actual token enforcement is delegated to the version-gated server with
`num_ctx`, `num_predict`, `truncate: false` and `shift: false`; observed prompt/output
counts are checked again before accepting a response. No history is silently
pruned, summarized or shifted. Token overflow/incomplete output is an explicit
failure. A future tokenizer integration is not needed for this bounded prototype.

A socket timeout plus a per-request watchdog closes the client socket at its
deadline, including while waiting for headers. This bounds client transport wait;
it does not guarantee instantaneous cancellation/freeing of server CPU/model RAM.
Existing in-memory dispatcher reader deadlines remain cooperative.

`complete` describes an error-free, fully finalized execution with accepted tool
evidence and final text. It does not validate the correctness of natural-language
claims. Every answer is `advisory_only: true`, `answer_verified: false`; numerical
truth remains the structured deterministic evidence. Text without accepted tool
evidence is withheld/abstained, including JSON-looking fake calls. Error-only
reads cannot ground a business answer. Semantic answer validation is later work.

## Observable audit without private reasoning

Requests set `think: false`. The adapter discards `message.thinking` and all
intermediate assistant content; neither is forwarded or persisted. Final content
containing explicit thinking/analysis tags is withheld. Model events retain
round/dispatch-attempt correlation, elapsed time, token/duration metrics and a
boolean indicating an omitted thinking field. HTTP events contain only method,
path, status, byte count, time and sanitized error code. No raw response bodies,
full outbound request bodies, private reasoning hashes or exception messages are
written. Final user-facing advisory text and validated tool calls/results remain
auditable. Arbitrary unmarked prose cannot be semantically classified perfectly.

Reports include source/model identities, resource observations from `/api/ps`,
budgets, failures, dispatcher history and a report fingerprint. Export uses
exclusive JSON creation with integrity validation; existing outputs are never
overwritten. On cancellation, the sanitized report is attached to the propagated
exception as `agent_report` for an operator to save. Hashes are not signatures.

## Operator provisioning and provenance

Ollama 0.40.14 was installed per user after explicit authorization. Its server
reports cloud disabled and listens only on 127.0.0.1:11434. The persistent user
settings are OLLAMA_NO_CLOUD=1 and OLLAMA_HOST=127.0.0.1:11434. Model provisioning
requires its own authorization and never occurs inside the adapter or tools.

The separately approved official qwen3:4b Q4_K_M manifest identifies 2,497,293,931
bytes of blobs (about 2.50 GB), including weights of 2,497,280,480 bytes. Its digest is
359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7.
An operator-only POST /api/pull to the local server is equivalent to
ollama pull qwen3:4b. Downloading uses the official registry over the internet;
this is distinct from inference. The adapter forbids that endpoint.

Before inference, verify the local catalog digest, every blob SHA-256 and the
model's tools capability, family and context capacity. Record the provisioning
result without putting weights in Git. Existing synthetic fixtures are reused.

Save durable verification in a new ignored runs/ directory. The Windows installer
cleans temporary directories matching %TEMP%\ollama*, so temporary helper/audit
folders use an unrelated oem-stage53- prefix. Never overwrite existing reports.

Sources: [official catalog](https://ollama.com/library/qwen3:4b),
[official manifest](https://registry.ollama.ai/v2/library/qwen3/manifests/4b),
[cloud settings](https://docs.ollama.com/faq).

## Small real smoke test

`examples/stage5_3_smoke.py` asks only three questions on the existing synthetic
statistical scenario: selected-piece count/coverage; observed versus filtered
prices/ads/units for piece 10; and its DEMO cost/current review state. It does not
implement the five independent evaluation scenarios or the 120-run evaluation.

After provisioning is authorized and preflight succeeds, from the project root:

```powershell
python -m examples.stage5_3_smoke --evidence runs/delivery03-verification/statistics.json --dossier reviews/delivery04-v1.review.json --out runs/stage5-3-real-smoke
```

`--questions 1` limits it to the first question. Each question gets a fresh bounded
session; the output directory must be new. The script never starts, installs or
downloads Ollama and stops if setup is unavailable/unsafe. Operator expectations
for manual review are 27 pieces; piece 10 observed 10 ads/55 units, EUR 10-100,
filtered 8 ads/44 units, EUR 20-90; DEMO cost EUR 2.50 and pending review. These
expectations are separate from the model-selected tool sequence and its prose.

## Resources and verification limits

The inspected Windows machine has about 7.70 GiB physical RAM, i5-1135G7 (4 cores,
8 threads) and integrated Iris Xe. The adapter starts with four CPU threads,
`num_gpu: 0`, 4096-token context and 30-second model keep-alive. This avoids assuming
verified GPU acceleration and reduces context memory relative to 8192. Qwen3:4b
model files are about 2.5 GB; runtime/cache/Windows require additional RAM. Allow
space for the Windows runtime as well. Observed timeout durations and API allocation are recorded below. The transient
runner resident-memory peak was not captured; it is not inferred from server RSS. Temperature zero/seed zero do not
guarantee identical text across runtime/hardware versions.

The default offline suite adds 36 protocol/transport tests to the existing 163:
199 tests. It checks native selection, result return/IDs, pagination, invalid and
forbidden calls, budgets, cloud/model checks, local transport, deadlines, context,
strict JSON, private-field omission, failure/cancellation audit and unchanged
source data. It never requires Ollama, downloads weights or contacts a model.

Sources: [native tool calling](https://docs.ollama.com/capabilities/tool-calling),
[chat API](https://docs.ollama.com/api/chat),
[versioned protocol](https://github.com/ollama/ollama/blob/v0.40.14/api/types.go),
[resource API](https://docs.ollama.com/api/ps),
[qwen3:4b](https://ollama.com/library/qwen3:4b).

## Observed verification

The approved 5.2 baseline remains commit 7f23f671b59c358ee86f884889f02212057f2767.
The current offline run passed all **199 tests** (the original 163 plus 36 local
protocol/transport tests), without ResourceWarning. The original deterministic
DB still yields **6 cases / 7 reads**, and the existing Stage 2-4 snapshot has
**27 selected pieces**. No fixture or business rule was changed.

Operator provisioning completed in 470.012 seconds. The local model digest
matches the official manifest above; all five blobs were independently checked
for their declared byte sizes and SHA-256, including the weight file. These
checks establish the downloaded artifact identity, not model-answer quality.

Exactly three real business questions were attempted, in the order in the
example. No further inference was issued after that approved limit.

| Question | Wall time | Error | Native tool calls | Complete answer |
| --- | --- | --- | --- | --- |
| Selected-piece count / coverage | 45.149 s | request_timeout | 0 | No |
| Piece 10 statistics / units | 45.652 s | request_timeout | 0 | No |
| Piece 10 DEMO cost / review | 45.669 s | request_timeout | 0 | No |

The native schemas were sent to /api/chat, but no model tool call or final answer
arrived within the 45-second request limit. All three reports remain error /
complete=false, with absent answers. There was no provider fallback, generated
business result or retry of a question. **Successful real native tool selection
has not yet been demonstrated.** Protocol simulation success must not be read as
functional acceptance of this model/hardware combination.

The observer sampled /api/ps and Windows process memory. Ollama listed the model
from 14.762 seconds onward and reported 3,186,108,660 bytes (about 2.97 GiB) of
model allocation, zero VRAM and context length 4096 in 49 samples. This is an API
allocation observation, not the peak physical RAM used by inference. The process
sampler captured the server/application but missed the transient runner, so its
52,334,592-byte server working-set peak and one CPU second cannot represent the
model's full resident memory or CPU cost. PowerShell also returned no process
exit code; the three structured error reports determine the smoke outcome.

The machine had about 7.70 GiB physical RAM, about 1.34 GiB available before the
batch and 0.64 GiB afterward. Memory pressure or CPU load may affect latency, but
the timeout cause has not been isolated. Do not claim a measured runner peak or
silently increase budgets. Further real testing requires review of RAM/loading
and explicit approval for another bounded batch.

All agent HTTP events use the fixed local endpoint. Sampled agent/server TCP
peers were loopback only. This is scoped observation, not a machine-wide packet
capture; it excludes unrelated GUI updater traffic and the unsampled runner.
Server cloud disablement was checked before each inference, and the verified
model is local. The optional paid SDK demo was not used.

Local, ignored evidence: runs/stage5-3-session-08525409f4/ contains tests.txt,
model-provisioning.json, real/question-1.json through question-3.json,
resource-samples.jsonl, model-memory-observation.json, real-assessment.json and
conservation checks. The five independent evaluation scenarios remain deferred.
