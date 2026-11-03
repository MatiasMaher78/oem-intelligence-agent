"""Opt-in Stage 2 CLI; leaves the original seed/run commands unchanged."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .db import read_only_connection
from .harness import Harness
from .stage2_db import CANDIDATE_SQL, list_stage2_candidates, seed_stage2, validate_day_range
from .stage2_matching import execute_stage2_matching
from .stage3_fixtures import seed_statistics_demo
from .stage3_statistics import StatisticsConfig, execute_stage2_statistics
from .excel_export import ExcelDependencyError, export_statistics_excel


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
    parser = argparse.ArgumentParser(description="Stage 2 (entirely synthetic, offline)")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="Create a separate synthetic Stage 2 database")
    seed.add_argument("--db", type=Path, default=Path("data/stage2.sqlite"))
    seed_stats = sub.add_parser("seed-stats", help="Create a separate ten-comparable-offer statistics scenario")
    seed_stats.add_argument("--db", type=Path, default=Path("data/stage2-stats.sqlite"))
    select = sub.add_parser("select", help="Print all eligible inventory rows, source and trace as JSON")
    match = sub.add_parser("match", help="Compare synthetic OEM and declared compatibility in both sources")
    stats = sub.add_parser("stats", help="Analyze comparable offers; optionally export an audited Excel workbook")
    for command in (select, match, stats):
        command.add_argument("--db", type=Path, default=Path("data/stage2.sqlite"))
        command.add_argument("--min-days", type=int, default=60)
        command.add_argument("--max-days", type=int, default=90)
    stats.add_argument("--p-low", default="10")
    stats.add_argument("--p-high", default="90")
    stats.add_argument("--min-sample", type=int, default=10)
    stats.add_argument("--excel", type=Path, default=None)
    args = parser.parse_args()
    try:
        if args.command in ("seed", "seed-stats"):
            seed_workflow = seed_stage2 if args.command == "seed" else seed_statistics_demo
            seed_workflow(args.db)
            print(f"Created synthetic Stage 2 database: {args.db}")
        else:
            if args.command == "stats":
                config = StatisticsConfig(args.p_low, args.p_high, args.min_sample)
                result = execute_stage2_statistics(args.db, min_days=args.min_days, max_days=args.max_days, config=config)
                if args.excel is not None:
                    export_statistics_excel(args.excel, result)
            else:
                workflow = execute_stage2_selection if args.command == "select" else execute_stage2_matching
                result = workflow(args.db, min_days=args.min_days, max_days=args.max_days)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if args.command in ("match", "stats") and not result["complete"]:
                label = "Matching" if args.command == "match" else "Statistics"
                parser.exit(1, f"{label} incomplete; inspect the JSON result and error trace.\n")
    except FileNotFoundError:
        parser.error(f"No Stage 2 DB: {args.db}. Run: python -m oem_agent.stage2 seed --db {args.db}")
    except (FileExistsError, ValueError, sqlite3.DatabaseError, ExcelDependencyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
