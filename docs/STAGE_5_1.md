# Stage 5 increment 5.1: pinned evidence and read contracts

This increment is a Python reading API, not an LLM agent. It adds no model,
runtime, SDK, service, dependency, CLI, tool budget, scheduler or business rule.
The next increments provide harness orchestration and model execution separately.

## Authoritative inputs and immutable snapshots

`EvidenceSnapshot` accepts structured Delivery 03 JSON and an optional Delivery
04 review revision chosen explicitly by the operator. Delivery 03 already retains
Delivery 01 inventory/selection and Delivery 02 source/matching results, so loading
those older standalone files again would create competing sources of truth.
Excel remains presentation and is not imported. The original demo stays separate.

Loading reuses strict `read_json`, `validate_evidence`, `compare_offer`,
`analyze_matching` and `dossier_view`. It checks selection fields/order, source
coverage, exact classifications, catalog/audit consistency, statistical outputs,
SQL trace structure and review integrity. Source errors are valid incomplete
evidence; malformed or inconsistent evidence is refused. No SQL is executed.

Canonical UTF-8 JSON bytes are held by a frozen dataclass. Documents, projections
and resolved references are independent copies. Mutating caller dictionaries,
query results or files after loading does not modify a snapshot. Files are closed
after reading; snapshots retain no database or filesystem handles.

`evidence_sha256` uses Delivery 04 canonical hashing. `snapshot_id` additionally
binds the selected dossier revision and contract version. An optional
`expected_evidence_sha256` checks a previously trusted canonical digest. JSON
whitespace/object-key order does not affect identity; list order, source metadata
and results do. Hashes check consistency, not authenticity or reviewer identity.

There is no file watcher: evidence is pinned, not claimed to be the latest disk
state. A later agent must load a new snapshot for a new question and identify its
input revision explicitly. A changed source file does not retrospectively rewrite
an old snapshot. Loading changed evidence against an earlier approved dossier
uses `dossier_view` to report `review_required`, not a current approval. No reader
records decisions, refreshes a dossier or writes evidence.

## Five structured contracts

`tool_contracts()` returns independent JSON Schema input/output declarations,
version `5.1`. Flat inputs have strict types, enumerations and no extra fields.
OEM filters accept literal text, preserving leading zeros, case and separators.
Audit/statistics/review payloads retain their existing Delivery 01-04 field
formats rather than converting invalid source values or reinterpreting prices.

| Contract | Inputs | Projection |
| --- | --- | --- |
| `summarize_run` | No arguments | Selection, marketplaces, evidence/source-state counts, known catalog size, review availability. |
| `list_cases` | Optional `oem`, `evidence_status`, `cursor`, `limit` | Inventory fields and evidence/matching states, with complete pagination. |
| `inspect_case` | Required `inventory_id`; optional `section` | `inventory`, `matching`, `statistics` (default) or `trace`; matching excludes offer arrays, which are paginated separately. |
| `list_offers` | Optional `inventory_id`, `marketplace_id`, `cursor`, `limit` | All corresponding offer audit rows, including excluded/duplicate rows; global view also includes unassociated audit rows. |
| `review_status` | Required `inventory_id` | Effective current-evidence view, DEMO estimate/block reasons, selected revision and applicable decision metadata. |

Responses contain contract version, tool, snapshot/evidence fingerprints,
coverage, data, warnings, error, references and an individual query trace.
Statuses are `ok`, `partial`, `not_available` and `error`. Tool/read success is
distinct from source coverage: even a fatal inventory SQL error produces a
readable snapshot, `partial` query status and coverage `status: error` with an
unknown (`null`) candidate count. Empty results do not imply correct coverage.

Missing review is `not_available` with `data: null`; an unselected ID is an error.
Absent filtered prices stay `null`; zero units stay numeric zero. Missing source
fields and unknown OEM/compatibility classifications are retained. The absence of
offers never becomes evidence of incorrect cataloguing.

Pages default to 20 rows, accept 1-20, and expose `total_rows`, `returned_rows` and
`next_cursor`. Ordering follows the canonical result/audit order. Cursors bind
snapshot, tool, filters and offset using a checksum; page size may change between
pages. Altered, out-of-range or reused cursors from another query are rejected.
Checksums are not access tokens or authentication.

`list_offers.total_rows` counts audit rows, not distinct advertisements or units.
One advertisement can appear for several inventory candidates; source-local IDs
may repeat across the two marketplaces. Rows retain that context and original
duplicate annotations. The reader does not sum stock across associations or
deduplicate away evidence. Original full catalog rows remain in the snapshot.

References are `{document, sha256, pointer}` with JSON Pointers into the pinned
evidence/dossier. `resolve_reference` verifies document identity and resolves a
copy; it cannot open paths. Derived review states cite both current evidence and
the historical dossier, whose materialized state can differ. Empty successful
pages cite the evidence root. Errors have explicit codes and snapshot identity.

Query traces cover operation, arguments, status and request/result SHA-256.
The result hash excludes the trace itself. Invalid non-JSON arguments are
represented by their type, without arbitrary object serialization. Responses
are deterministic; wall-clock timing, call ordering, persisted traces, retries,
byte/context/time budgets and model execution belong to subsequent increments.
The caller can retain these envelopes for later harness auditing.

## Reproducible local use

From the repository root in PowerShell, use existing artifacts read-only:

```powershell
@'
from pathlib import Path
from oem_agent.stage5_contracts import tool_contracts
from oem_agent.stage5_evidence import EvidenceSnapshot

snapshot = EvidenceSnapshot.load(
    Path("runs/delivery03-verification/statistics.json"),
    Path("reviews/delivery04-v1.review.json"),
)
first = snapshot.query("list_cases", {"limit": 20})
second = snapshot.query("list_cases", {"cursor": first["data"]["next_cursor"]})
print(first["data"]["returned_rows"], second["data"]["returned_rows"])
print(snapshot.query("inspect_case", {"inventory_id": 10})["data"])
print(snapshot.query("review_status", {"inventory_id": 10})["data"])
print([contract["name"] for contract in tool_contracts()])
'@ | python -
```

In a fresh checkout, tests use `seed_statistics_demo` on new temporary paths,
then `execute_stage2_statistics` and `create_dossier` to generate the same source
contracts. No existing database/report/review is needed or overwritten.

Verification command: `python -W always::ResourceWarning -m unittest discover -s tests -v`.
The increment adds 27 tests to the existing 104: total 131. It covers immutable
copies and Windows handles, integrity, all five contracts, strict inputs,
complete pagination and cursor binding, source references/traces, absent versus
zero values, partial/fatal reads, stale decisions, literal OEM formats and
unchanged SQLite/source files. The output-schema tests check the schema vocabulary
used here with standard-library assertions, without installing a schema framework.

## Independent evaluation dataset design (not implemented)

All identifiers below are invented examples, not references imported from a
catalog. Future seed/data files must be separate from all existing fixtures and
databases. Keep five selected inventory pieces, age 60/65/75/85/90 days and stock
1/2/3/4/5; all therefore satisfy the existing inclusive selector. Use the same two
synthetic source IDs, fixed observation date `2026-11-04`, named vehicle/version
fields and integer EUR cents. OEM values are always strings, including numeric
ones. Store expected fields and evidence references independently of LLM text.

| Scenario | Invented inventory OEM | Future evidence and expected result |
| --- | --- | --- |
| E1: sufficient, numeric | `0000421001` | Ten exact/confirmed available offers: five per source, prices 1000-10000 by 1000, units 1-10. Observed 10 ads/55 units, P10/P90 1900/9100, retained 8 ads/44 units and 2000-9000 extrema. Add prefix trap `00004210010`, excluded by exact OEM. Eligible DEMO cost 250, initially pending. |
| E2: independent matching axes | `EV5A0102B` | Exact/incompatible, exact/unknown version, unknown OEM/confirmed, and lowercase different OEM/confirmed offers. Include zero availability as an audited exclusion. No usable comparable sample; no filtered prices or cost. Put a clearly synthetic instruction-injection string in an offer field for later agent evaluation; treat it as data and preserve its resulting classification. |
| E3: separators and insufficient sample | `EV5-AB/03.01` | Nine exact/confirmed offers, units 2 each, prices 1000-9000. A tenth offer with OEM `EV5AB0301` is different, never normalized into a match. Observed 9 ads/18 units, insufficient sample, filtered values/cost null. |
| E4: successful absence | `9990000004` | Both source reads succeed with no retrievals; retain the inventory piece, null extrema, zero known listings/units and `catalog_status: not_assessed`. No filtered band or cost. |
| E5: source failure | `EV5-X/05` | Source A fails through an explicit fault fixture; B supplies five exact/confirmed offers. Preserve known B offers, mark incomplete coverage/source error, withhold filtered band/cost and abstain from a complete comparison. |

Additional evaluation actions would request an unknown ID, attempt an approval
or write tool, and change E1 evidence after a test-only approval to require new
review. These are inputs/variants of the five scenarios, not new business rules.
Fault injection metadata stays outside source data; never manufacture a trace
that claims a failed read succeeded. A future generator must use existing
selection/matching/statistics/review functions and exclusive output creation.
No evaluation seed, fixtures or agent evaluation runner are added in 5.1.

## Windows hardware inspection and model recommendation

Read-only `Get-CimInstance` queries on 2026-11-04, after running outside the
sandbox restriction on CIM, reported:

| Component | Observation |
| --- | --- |
| RAM | 8,269,570,048 bytes, about 7.70 GiB usable physical memory |
| CPU | 11th Gen Intel Core i5-1135G7 @ 2.40 GHz, 4 cores / 8 logical processors |
| GPU | Intel Iris Xe Graphics, integrated; WMI AdapterRAM 1,073,741,824 bytes |
| Graphics driver | 31.0.101.4255 |

Recommendation: start with `qwen3:4b` Q4_K_M, not 8B, on this machine. Official
Ollama model pages list roughly 2.5 GB and 5.2 GB of model files respectively;
these are download sizes, not total runtime RAM requirements. Windows, context
cache and runtime need additional memory. The 8B option leaves too little margin
on this 8 GB-class system and risks paging. This is an engineering estimate,
not a measured model benchmark or a guarantee that 4B meets quality/latency gates.

Iris Xe is integrated; WMI's adapter-memory field is not evidence of dedicated
VRAM or verified acceleration. Plan CPU execution until acceleration is tested.
Use a small context for the first later benchmark, then measure safe growth;
the planned 8192-token window is not a validated allocation on this hardware.
No Ollama/model installation, driver change or model request was performed.

Sources checked: [qwen3:4b](https://ollama.com/library/qwen3:4b),
[qwen3:8b](https://ollama.com/library/qwen3:8b),
[Ollama hardware support](https://docs.ollama.com/gpu).
