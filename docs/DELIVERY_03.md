# Stage 2 - Delivery 03: comparable-offer statistics and Excel

All evidence remains synthetic and advisory. The original process and the
Delivery 01/02 seed, matching classifications and selection are preserved.
Only the Excel writer requires the optional `excel` dependency; SQL/matching/
statistics use the standard library.

## Initial sample check and isolated scenarios

Before implementation, the existing fixtures had at most two comparable offers
per piece: ID 2 has two, ID 3 has one, and ID 7 has one identity match with zero
availability. None meets the default minimum of ten usable offers.

The old seed remains unchanged. `seed-stats` creates a separate database with
the same 32 inventory rows and 27 selected pieces, plus ten invented comparable
offers for inventory ID 10. Five offers belong to each synthetic marketplace.
The old five offers remain intact, for 15 source offers in this separate scenario.
No existing database can be reset or overwritten.

```powershell
# Needed only for Excel, in your own virtual environment:
python -m pip install -e ".[excel]"

# A fresh, distinct database; choose another --db if this path already exists:
python -m oem_agent.stage2 seed-stats --db data/stage2-stats.sqlite
python -m oem_agent.stage2 stats --db data/stage2-stats.sqlite --p-low 10 --p-high 90 --min-sample 10 --excel runs/delivery03-review/report.xlsx

# The old dataset is also valid input; insufficient samples remain explicit:
python -m oem_agent.stage2 stats --db data/stage2.sqlite
python -m unittest discover -s tests -v
```

JSON is printed to stdout. XLSX is optional and written only when `--excel` is
provided. An existing output is refused. Use a fresh path for another export.

## Observed sample, offer identity and units

Price calculations use only offers with OEM `exact`, compatibility `confirmed`,
positive integer unit prices in EUR cents, and positive integer available units.
Zero-availability offers remain in the matching/audit evidence but do not enter
the available-price sample. Invalid or uncertain data is retained with reasons.

The observed sample is deduplicated per inventory piece by the source identity
`(marketplace_id, offer_id)`. Identical repetitions count once and retain a link
to the first audit row. Conflicting copies of one identity are all excluded;
the analyzer does not choose an unverified price or quantity. The same local ID
in independent marketplaces is not a duplicate. Cross-posted physical stock
cannot be inferred without explicit shared identities and is not estimated.

`observed_offers_count` counts unique usable announcements, while
`observed_units` sums their advertised units. Summaries are per inventory piece;
the workbook does not claim a unique physical-stock total across all pieces.

Observed minimum/maximum are actual extrema before percentile filtering.
Filtered minimum/maximum are actual extrema of retained offers, not the
interpolated percentile cutoffs. Original prices are never replaced or winsorized.

## Exact percentile method and configurable minimum

Defaults: lower P10, upper P90, minimum sample 10. Require finite percentiles
`0 <= lower < upper <= 100` and a positive integer minimum sample. The minimum
applies to the unique usable sample before trimming, not to the retained count.

For sorted integer-cent prices `x[0] ... x[n-1]`, calculate:

```text
h = (n - 1) * p / 100
i = floor(h)
quantile = x[i] + (h - i) * (x[min(i+1, n-1)] - x[i])
```

Each announcement has one observation; units do not weight the percentile.
Python `Fraction` and decimal-config parsing preserve exact arithmetic, including
fractional cents. JSON cutoffs store exact numerator/denominator pairs. Retain
offers whose original price falls inclusively between both exact cutoffs.
Rounding is only for Excel display; it cannot change filter membership.

For ID 10 in the separate scenario:

| Quantity | Observed | Filtered |
| --- | --- | --- |
| Announcements | 10 | 8 |
| Advertised units | 55 | 44 |
| Minimum price | EUR 10 | EUR 20 |
| Maximum price | EUR 100 | EUR 90 |

The interpolated cutoffs are EUR 19 and EUR 91. Offers at EUR 10 and EUR 100
remain auditable with `outside_percentile_band` and their original prices.

## Evidence states and absent values

| State | Interpretation |
| --- | --- |
| `no_comparable_offers` | No unique usable offers; observed counts/units are zero and price extrema are absent. |
| `insufficient_sample` | Observed sample is below the configured minimum. Observed evidence remains; cutoffs, filtered counts/units and filtered prices are absent. |
| `sufficient_sample_demo` | Complete coverage and minimum sample met; the demonstrative percentile filter was applied. This is not real statistical validation. |
| `empty_filtered_sample` | Filter was applied but retained no offers; filtered counts/units are zero and price extrema are absent. |
| `source_error` | Matching or source-audit coverage failed; observed evidence is partial and filtering is withheld even if ten known offers exist. |

`None` becomes an empty Excel cell; numeric zero remains zero. In particular,
uncomputed filtered counts are absent, not zero. Source coverage flags and states
qualify observed counts/units from partial input. No state determines incorrect
cataloguing or authorizes a pricing action.

## SQL, complete audit and trace

Matching reuses its existing 55 read attempts for 27 pieces. Statistics adds one
read of the full `stage2_market_offers` table, ordered by marketplace and offer ID.
This preserves audit evidence even for offers never retrieved for selected pieces.
Both reads use the existing read-only connection and harness SQL/error tracing.
The normal statistical report therefore contains 56 SQL read events and 27
separate deterministic statistical trace events.

Every matching comparison survives, including rejected, unknown, duplicate,
invalid and percentile-excluded offers. Source offers with no selected-piece
association are audited separately with that reason. Per-piece inclusion flags,
duplicate links, exclusion reasons, raw source values and field-level matching
evidence are retained in JSON and the workbook. An offer can appear in several
piece associations; this does not multiply the per-piece sample.

SQL coverage errors retain partial results and return CLI exit 1. Invalid
parameters, missing dependencies and existing outputs return nonzero errors.
Insufficient samples are successful analyses with explicit evidence states.
The original `select` and `match` outputs and read budgets remain unchanged.

## Workbook and optional dependency

The `excel` extra declares `openpyxl>=3.1.5,<4`; it is imported only when exporting.
Core analysis works when it is unavailable. The workbook contains:

- `Resumen`: all selected pieces, identity/vehicle fields, internal stock,
  announcement and unit counts, observed/filtered prices, configuration, cutoffs
  and evidence/coverage states.
- `Ofertas`: each piece-offer association and unassociated source offer, original
  values, classifications, inclusion flags, reasons and raw evidence JSON.
- `Traza`: SQL reads/errors and statistical calculation events.
- `Configuracion`: source, selection, percentile method, sample minimum,
  synthetic-data label and coverage/deduplication interpretation.

OEM and all other strings are explicitly typed as text, preserving leading zeros
and preventing formula-like source values from becoming executable formulas.
Monetary cells are numeric EUR for presentation; exact integer/rational evidence
is retained in JSON and raw-cent audit columns. Workbook cell contents/types are
reproducible; binary XLSX metadata is not used as the reproducibility criterion.

The complete workbook is serialized in memory before exclusively creating the
output file with `xb`, including protection against concurrent creation. Serialization
errors create no output. A later filesystem write failure may leave the newly
created partial file; it is never silently reused or overwritten.

Reference: [openpyxl saving documentation](https://openpyxl.readthedocs.io/en/stable/tutorial.html#saving-to-a-file)
notes that a direct `Workbook.save(path)` overwrites existing paths. The exporter
instead saves to memory and uses explicit exclusive file creation.

## Verification

The suite includes all 53 previous tests and 31 additional tests for interpolation,
sample thresholds, units versus announcements, inclusivity, empty filtered sets,
duplicate/conflicting identities, unavailable/invalid offers, full source audit,
coverage errors, reproducibility and scenario isolation. Workbook tests reopen
files and verify values/types, every audit row, unknown versus zero, literal OEM,
formula-like strings, repeated export content, CLI operation and no-overwrite races.

Excel tests are explicitly skipped if the optional dependency is absent; install
the `excel` extra to run the complete suite. Delivery verification used all 84
tests with openpyxl 3.1.5 and Python 3.13.4 on Windows, with no skips.

All generated SQLite databases, XLSX files and `runs/` evidence stay outside Git.
No LLM, external scraping, enterprise cost formulas or HITL feature is added.
