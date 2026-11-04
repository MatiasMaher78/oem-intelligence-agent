# Stage 5 increment 5.2: bounded read-only dispatcher

This increment adds `ReadOnlyDispatcher` and `DispatchLimits`. It is an offline
execution boundary for future tool calls, not an agent or model adapter. The five
5.1 contracts, original `Harness`, snapshots, fixtures, business calculations,
review decisions and existing 131 tests are unchanged. No runtime/dependency,
network service, CLI or evaluation dataset is added.

## Fixed execution boundary

Construct one dispatcher for one question using an already validated
`EvidenceSnapshot`. `dispatch(name, arguments)` accepts only the existing five
read names and flat dictionary arguments. `contracts()` returns independent
copies of declarations permitted in this session. An operator may narrow the
allowlist, including disabling every tool, but cannot add names or callbacks.
No `eval`, dynamic import, arbitrary SQL, shell, filesystem, review-decision,
inventory mutation or commercial operation is dispatchable.

The implementation reuses `Harness.call`, its allowlist and execution budget,
and the existing strict argument validator. A separate attempt counter is
necessary because the original harness counts executions after its policy gate
and does not record all failures. Changing it would alter the original demo's
trace/count semantics. The new outer trace is authoritative for every attempted
dispatch, including rejected and post-stop calls. A lock serializes calls and
finalization so simultaneous callers cannot race past the budgets.

Arguments must be flat JSON-compatible primitives of exactly the declared types.
Explicit null, serialized JSON strings, nested objects/arrays, booleans used as
IDs, extra fields and invalid enum values are rejected. Omitting arguments means
`{}`; supplying `None` is not omission. Decoding a provider's raw argument string
with duplicate-key checks belongs to the later provider adapter.

## Limits and error policy

| Control | Default and hard maximum |
| --- | ---: |
| Attempt budget | 8 |
| Error threshold | 2 |
| Session elapsed time | 180 seconds |
| Reader/validation elapsed time | 2 seconds |
| Encoded argument object | 4096 UTF-8 bytes |
| Encoded Delivery 5.1 result envelope | 8192 UTF-8 bytes |

Operators may lower positive limits, never raise them beyond these maxima.
Every `dispatch` increments attempts before any validation, including forbidden
tools, malformed inputs and attempts after stop/finalization. `budget_used` is
capped at the budget; attempts after that cap remain counted/audited but never
execute. `executions` is the separate underlying harness count and includes a
reader that fails after it starts.

Argument mistakes, invalid cursor sequences, unknown inventory IDs and oversized
results consume an attempt and error. The second error stops the session. Tool
denials, repeated reads, invalid result integrity, unexpected exceptions and
timeouts stop immediately. There are no automatic retries or provider fallbacks.
Errors stay recorded: a later valid read cannot restore a complete session.

The final permitted attempt may return validated data, but reaching zero budget
also stops the session and makes its outcome partial. A subsequent call is
blocked. No new session is created implicitly and no reset tool exists.

Source SQL failure is distinct from a dispatcher execution error: a valid partial
snapshot remains readable with its original unknown counts and coverage flags,
but the session cannot become complete. A missing requested review likewise
remains `not_available`, without manufacturing a pending state or incrementing
the operational error counter. It prevents a complete report.

Time checks use a monotonic clock before execution, after the reader and after
result validation, and at finalization. Late results are discarded and the
session stops. These are **cooperative deadlines**, not CPU preemption: the
dispatcher does not kill a running Python function. Its fixed readers perform
no external I/O. A genuinely hung reader can delay returning to the caller;
hard process isolation/cancellation is deliberately not introduced here.

Byte limits cover arguments and the original tool envelope; dispatcher metadata
and audit wrappers add overhead. An oversized result is withheld, not truncated.
Its digest/size are recorded when available, so an operator can reproduce the
read. Request a smaller page or narrower projection. The 5.1 default 20-case
page exceeds this default byte budget on the current fixture; `limit: 10` works.
Recovering with a smaller page still retains the earlier error. Token accounting,
context assembly and model-request budgets require the later model adapter.

## Repetitions, pagination and completion

The same read cannot execute twice in one pinned session, even nonconsecutively.
Equivalent defaults, such as omitted versus explicit `section: statistics`, are
the same request. This blocks repetition without progress, while new inventory
IDs, sections, filters and genuinely subsequent pages remain available.

Paginated reads must start without a cursor and continue using the exact next
cursor issued for that tool/filter scope in this session. Foreign/skipped/stale
cursors are rejected. Restarting an unfinished scope, or rereading a finished
scope with a different page size, is repetition. Page size may change on the next
page. Rejected oversized pages are not committed as progress, so a smaller retry
can still start/continue the sequence within remaining limits.
Reported row counts, stable totals and continuation flags must agree with page
progress; inconsistent pages are terminal errors even if their hashes match.

Each dispatch outcome has `complete: false`: a single call does not finalize the
session. Its nested `result` is the unchanged 5.1 envelope, whose coverage flags
refer only to the original source evidence. They are not the dispatcher's claim
of overall completion. The wrapper status is `ok`, `partial` or `error`.

Only explicit `finish()` can produce `complete: true`, requiring at least one
attempt, complete source coverage, no error, no exhausted/stopped control, no
missing requested evidence and no outstanding pages. This means that the
requested reads finished cleanly; it does not establish that a future natural
language answer is correct or that every piece was individually inspected.

`finish()` is idempotent. Calls after it are denied, counted and change the
current report to partial; previously returned historical reports remain copies
of their earlier state. Closing an unfinished page sequence cannot claim that
all 27 pieces or all offers were processed.

## Results and audit

Before returning data, the dispatcher checks the JSON Schema vocabulary used by
the fixed 5.1 outputs, matching tool/contract/snapshot identifiers, source coverage,
status consistency, request/result hashes and resolvable evidence references.
It withholds malformed, non-JSON, mismatched or late results. It does not invent
or recalculate business outputs inside the dispatcher.

Every attempt records sequence, tool, validated arguments (or argument type when
rejected), execution flag, elapsed time, result digest/size when available,
outcome, and linked event hashes. Invalid raw objects/extra-field values and
exception messages are not serialized into the trace. Unexpected exceptions are
sanitized; `KeyboardInterrupt`/`SystemExit` are audited, stop the session and
propagate so cancellation is honored. A whole-report hash covers configuration,
source identity, counters, state and the complete outer trace.

`report()` returns independent copies; it cannot claim completeness while open.
`write_report(path)` is an operator-only Python method, not a tool, and exclusively
creates a new JSON file. Existing files, including a concurrent writer's file,
are never overwritten. A disk error may leave a new partial file; choose a new
path after inspection. Hashes are integrity checks, not authentication/signatures.

The boundary constrains tool requests, not arbitrary hostile Python code with
access to the process or repository. Snapshot freshness remains explicit: load
the selected current files for a new question. There is no watcher, UI, identity
service, external action, autonomous price change or dossier approval.

## Reproducible simulated example

From the repository root in PowerShell, with the existing synthetic artifacts:

```powershell
@'
from pathlib import Path
from oem_agent.stage5_evidence import EvidenceSnapshot
from oem_agent.stage5_dispatcher import ReadOnlyDispatcher

snapshot = EvidenceSnapshot.load(
    Path("runs/delivery03-verification/statistics.json"),
    Path("reviews/delivery04-v1.review.json"),
)
dispatcher = ReadOnlyDispatcher(snapshot)
cursor, count = None, 0
while True:
    arguments = {"limit": 10}
    if cursor is not None:
        arguments["cursor"] = cursor
    outcome = dispatcher.dispatch("list_cases", arguments)
    if outcome["error"] is not None:
        break
    page = outcome["result"]["data"]
    count += page["returned_rows"]
    cursor = page["next_cursor"]
    if cursor is None:
        break
report = dispatcher.finish()
print(count, report["attempts"], report["complete"])  # 27 3 True
dispatcher.write_report(Path("runs/stage5-2-example/pagination.json"))
'@ | python -
```

Use a fresh report path on rerun. This is a scripted read demonstration, not
LLM tool selection. Tests seed a fresh temporary statistical DB and create a
test-only pending dossier; the repository's generated artifacts are not needed.

## Verification

`python -W always::ResourceWarning -m unittest discover -s tests -v`

The increment adds 32 tests to the preserved 131, total 163. Coverage includes
five-tool parity, restrictive allowlists, malformed arguments, charged invalid
attempts, hard budget caps, concurrent calls, sticky failures, repetitions,
complete/incomplete pagination, excluded offers, absent reviews, source/fatal
errors, corrupt outputs/references, sanitized exceptions, interruption, fake-clock
deadlines, trace integrity/copies and exclusive audit exports including races.
No actual waiting, model calls, Ollama installation or external service is used.
