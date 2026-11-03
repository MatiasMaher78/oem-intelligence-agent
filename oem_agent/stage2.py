"""Opt-in Delivery 01 CLI; leaves the original seed/run commands unchanged."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .db import read_only_connection
from .harness import Harness
from .stage2_db import CANDIDATE_SQL, list_stage2_candidates, seed_stage2, validate_day_range


def execute_stage2_selection(db_path: Path, *, min_days: int = 60,
                             max_days: int = 90) -> dict:
    validate_day_range(min_days, max_days)
    harness = Harness(max_tool_calls=1, allowed_tools=frozenset({"list_candidates"}))
    with closing(read_only_connection(db_path)) as conn:
        candidates = harness.call("list_candidates", list_stage2_candidates, conn=conn,
                                  min_days=min_days, max_days=max_days)
    harness.events[0].update({"sql": CANDIDATE_SQL.strip(), "parameters": [min_days, max_days]})
    return {
        "demo_only": True,
        "data_origin": "100% synthetic",
        "delivery": "stage2-01",
        "source": {"database": str(Path(db_path).resolve()), "table": "stage2_inventory"},
        "selection": {"stock_qty_gt": 0, "min_days": min_days, "max_days": max_days, "inclusive": True},
        "total_candidates": len(candidates),
        "candidates": candidates,
        "trace": harness.events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 Delivery 01 (entirely synthetic, offline)")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="Create a separate synthetic Stage 2 database")
    seed.add_argument("--db", type=Path, default=Path("data/stage2.sqlite"))
    select = sub.add_parser("select", help="Print all eligible inventory rows, source and trace as JSON")
    select.add_argument("--db", type=Path, default=Path("data/stage2.sqlite"))
    select.add_argument("--min-days", type=int, default=60)
    select.add_argument("--max-days", type=int, default=90)
    args = parser.parse_args()
    try:
        if args.command == "seed":
            seed_stage2(args.db)
            print(f"Created synthetic Stage 2 database: {args.db}")
        else:
            result = execute_stage2_selection(args.db, min_days=args.min_days, max_days=args.max_days)
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except FileNotFoundError:
        parser.error(f"No Stage 2 DB: {args.db}. Run: python -m oem_agent.stage2 seed --db {args.db}")
    except (FileExistsError, ValueError, sqlite3.DatabaseError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
