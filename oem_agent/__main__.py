"""Entry point: python -m oem_agent {seed|run}."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .db import seed_demo
from .harness import execute_readonly_workflow, write_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="OEM Intelligence Agent (synthetic demo)")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="Create the demo SQLite DB")
    seed.add_argument("--db", type=Path, default=Path("data/demo.sqlite"))
    seed.add_argument("--reset", action="store_true", help="Explicitly replace chosen demo DB")
    run = sub.add_parser("run", help="Read-only review + reproducible reports")
    run.add_argument("--db", type=Path, default=Path("data/demo.sqlite"))
    run.add_argument("--out", type=Path, default=None)
    run.add_argument("--days", type=int, default=90)
    run.add_argument("--max-candidates", type=int, default=10)
    run.add_argument("--mode", choices=("rules", "llm"), default="rules")
    run.add_argument("--model", default="gpt-5-mini", help="API model for optional LLM mode")
    args = parser.parse_args()
    if args.command == "seed":
        seed_demo(args.db, reset=args.reset)
        print(f"Created synthetic database: {args.db}")
        return
    records, harness = execute_readonly_workflow(
        args.db, threshold_days=args.days, max_candidates=args.max_candidates)
    notes = None
    if args.mode == "llm":
        from .llm_agent import analyze_with_llm
        notes = analyze_with_llm(records, model_name=args.model)
    out = args.out or Path("runs") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write_outputs(out, records=records, harness=harness, threshold_days=args.days,
                  mode=args.mode, llm_notes=notes)
    print(f"Reviewed {len(records)} synthetic cases; {harness.calls} read-only tool calls")
    print(f"Reports saved to: {out}")


if __name__ == "__main__":
    main()
