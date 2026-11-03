# Stage 2 - Delivery 02: OEM matching and declared compatibility

Entirely synthetic, offline, deterministic and read-only. The Delivery 01 seed,
schema, five offers and 27 selected inventory rows are unchanged. No migrations,
dependencies, price statistics, Excel, cost simulation or LLM additions.

## Reproduce

Use the existing Delivery 01 database:

```powershell
python -m oem_agent.stage2 select
python -m oem_agent.stage2 match
python -m unittest discover -s tests -v
```

To verify from a fresh seed, choose a new path:

```powershell
python -m oem_agent.stage2 seed --db data/stage2-delivery02.sqlite
python -m oem_agent.stage2 match --db data/stage2-delivery02.sqlite
```

Both `select` and `match` default to inclusive 60-90 days and retain all eligible
pieces without a cap. Matching preserves the selection order and includes both
sources for every piece, even when no offer matches or a source query fails.

## Retrieval SQL

The existing inventory selection is reused. For each selected piece and each
of the two configured synthetic marketplaces, retrieval uses:

```sql
SELECT marketplace_id, offer_id, oem, part_name, brand, model, version,
       available_units, price_cents, observed_on
FROM stage2_market_offers
WHERE marketplace_id = ?
  AND (oem COLLATE BINARY = ? OR part_name COLLATE BINARY = ?)
ORDER BY offer_id ASC;
```

All values are bound parameters. The OR retains exact-OEM offers whose other
fields conflict, as well as same-name offers with a different or missing OEM.
This is candidate retrieval, not a statement that every retrieved offer matches.
There is no prefix, substring, fuzzy, case-folded or alias-based matching.

Offer identity is `(marketplace_id, offer_id)`. Units, prices and observation
dates remain raw source evidence; even zero-availability offers are retained for
identity comparison. Their availability is not a compatibility decision.

## Two independent classifications per offer

| Axis | States | Demonstration rule |
| --- | --- | --- |
| OEM | `exact`, `different`, `unknown` | Literal, case-sensitive equality; missing/blank/non-text input is unknown. Leading zeros, suffixes, punctuation and whitespace are not removed. |
| Compatibility | `confirmed`, `incompatible`, `unknown` | Compare piece name, brand, model and version independently of OEM. All four known and equal confirms declared compatibility. Any known conflict is incompatible. Otherwise missing data means unknown. |

A known conflict takes precedence over a missing field; both are listed in the
evidence. `confirmed_match` requires OEM `exact` and compatibility `confirmed`.
Different OEM with matching vehicle/piece fields is not established equivalence.
Exact OEM cannot override a conflicting version or piece type.

Each comparison includes the original offer, both classifications and the source
values/status for every compared field. Neither source dictionary is mutated.

Compatibility is a teaching rule over declared synthetic metadata, not mechanical
fitment verification. No real catalogues, vehicle taxonomies or OEM equivalence
tables are used.

## Results, missing evidence and errors

- Each result retains its full inventory row and one entry per marketplace.
- Source `ok`: retrieved offers and comparisons, including rejected/unknown pairs.
- Source `no_results`: a successful empty retrieval, with count zero and no
  fabricated offer comparisons.
- Source `error`: a failed SQL read, with counts `null`, error type/message and a
  failed trace event. Other source/piece queries continue.
- `catalog_status` always remains `not_assessed`. No offer, a rejected pair or a
  read failure establishes that the inventory was catalogued incorrectly.
- `has_confirmed_match` is true for any confirmed pair; false only after complete
  retrieval without one; null when no pair is confirmed and source coverage failed.

The top-level `status` is `complete`, `partial` or `error`. A failed inventory
selection has unknown candidate coverage (`total_candidates: null`), distinct
from a successful zero-row interval. An unreadable/missing DB fails before SQL.
Unexpected programming errors and harness policy violations propagate; they are
not converted into successful empty results.

The CLI prints JSON and exits 0 for complete matching, 1 for partial/fatal SQL
coverage errors, or 2 for invalid arguments, missing databases and other handled
setup errors. Partial/error JSON remains available on stdout for inspection;
stderr explains the nonzero exit. The command writes no report files.

## Trace and regression evidence

The original harness and read-only connection are reused without modifications.
The matching workflow enriches its own trace with success/error status, actual
SQL, bound parameters, result counts and source/piece arguments. Connections are
excluded from traces and explicitly closed on every exit path.

The tool budget is derived from the actual selection: `1 + 2 * N`. For the default
27 pieces this is 55 read attempts: one inventory query and two offer queries per
piece. Error attempts consume budget too. An empty interval uses just one query.

Default fixture examples:
- Inventory 2, A/001 and B/001: exact OEM and confirmed declared compatibility.
- Inventory 2, A/003: different OEM despite the prefix and matching metadata;
  this pair is not a confirmed match.
- Inventory 3, A/002: confirmed match; B has no retrieved offer.
- Inventory 7, B/002: confirmed identity with zero advertised availability.
- Bulk inventory rows remain present without confirmed matches.

Additional test-only synthetic offers cover blank OEM/version, contradictory
piece names and incomplete evidence. Tests also cover both unknown axes, prefix
traps, source isolation, parameter binding, one/both source failures, fatal
inventory errors, CLI exit codes, exceptional cleanup, reproducibility, unchanged
selection and unchanged database bytes. All prior 30 tests remain in the suite.

Limitations: retrieval requires exact OEM or exact piece name; aliases, equivalent
references, multi-vehicle fitment, real-market coverage and enforced human review
remain future work. Results are advisory synthetic evidence, not pricing actions.
