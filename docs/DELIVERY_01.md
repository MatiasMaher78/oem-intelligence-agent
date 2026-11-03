# Stage 2 - Delivery 01: synthetic data and SQL selection

This delivery is entirely synthetic, offline and deterministic. It adds a separate
database and CLI. The original `python -m oem_agent seed` and `run --mode rules`
commands, original schema, fixtures, rules and reports remain unchanged.

## Reproduce

Run from the project root with Python 3.11+; no additional dependencies:

```powershell
python -m oem_agent.stage2 seed
python -m oem_agent.stage2 select
python -m unittest discover -s tests -v
```

The new default database is `data/stage2.sqlite`. Seeding exclusively creates a
new file and refuses every existing path, including `data/demo.sqlite`; there is
no reset option. For another run, choose a fresh path explicitly:

```powershell
python -m oem_agent.stage2 seed --db data/stage2-second.sqlite
python -m oem_agent.stage2 select --db data/stage2-second.sqlite --min-days 60 --max-days 90
```

## Data and expected evidence

- `stage2_inventory`: 32 pieces with invented OEM, name, brand, model, version,
  integer stock and integer age. The fixed seed has no random or clock inputs.
- `stage2_marketplaces`: `DEMO-MARKET-A` and `DEMO-MARKET-B`.
- `stage2_market_offers`: three offers from A and two from B, with source-local
  offer IDs. Their units and unit EUR-cent prices are independent fixture values.
  Integer cents are stored as evidence only; this delivery performs no price math.
- All generated databases remain excluded by `.gitignore`.

The query is parameterized and includes both age boundaries:

```sql
SELECT id, oem, part_name, brand, model, version, stock_qty, days_in_stock
FROM stage2_inventory
WHERE stock_qty > 0 AND days_in_stock BETWEEN ? AND ?
ORDER BY days_in_stock ASC, id ASC;
```

With parameters `[60, 90]`, the result contains **27 pieces**, exceeding both
original demo caps. There is no `LIMIT`, no marketplace join and no OEM-verification
exception. Pieces remain eligible even if neither marketplace has an offer.

The exact expected IDs, in order, are:

```text
2, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
7, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 3
```

Controls: ID 1 has 59 days, ID 4 has 91 days, and IDs 5, 6 and 8 have zero stock.
IDs 2 and 3 prove the inclusive 60- and 90-day boundaries. Equal day bounds are
valid. Bounds must be non-negative integers in ascending order; booleans are
rejected by the Python API.

`select` prints JSON containing every selected source row, the source database
and table, selection parameters, and one harness event with the actual SQL,
bound parameters and result count. It opens SQLite with `mode=ro` and
`PRAGMA query_only=ON`, and closes the connection on success and exceptions.
It creates no report files. A successful empty selection is distinct from a
query error, which fails the command and produces no successful result payload.

## Verification and scope

Tests cover boundaries, stock exclusion, all 27 expected rows, configurable and
invalid ranges, identical dumps from fresh seeds, marketplace source identity,
schema constraints, refusal to overwrite, read-only operation, exceptional
connection cleanup, CLI execution and isolation from the original six-case,
seven-tool-call demo and its reports.

When a seed fails, its new file may contain a partial schema; it is not silently
removed or reused. Inspect it and choose a fresh output path for a retry.

Marketplace matching, compatibility decisions, percentiles, Excel, LLM changes,
cost simulation and enforced human review belong to later deliveries. Absence
of an offer does not establish incorrect cataloguing; no such inference is made.
