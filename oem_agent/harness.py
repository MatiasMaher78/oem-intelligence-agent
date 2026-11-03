"""A simple, explicit harness around deterministic, read-only tools."""
from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import list_candidates, market_evidence, read_only_connection
from .rules import evaluate_case


class PolicyViolation(RuntimeError):
    pass


@dataclass
class Harness:
    max_tool_calls: int = 30
    allowed_tools: frozenset[str] = frozenset({"list_candidates", "market_evidence"})
    calls: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def call(self, tool: str, fn, **kwargs):
        if tool not in self.allowed_tools:
            raise PolicyViolation(f"Blocked tool: {tool}")
        if self.calls >= self.max_tool_calls:
            raise PolicyViolation(f"Tool budget exceeded: {self.max_tool_calls}")
        self.calls += 1
        result = fn(**kwargs)
        self.events.append({"step": self.calls, "tool": tool, "args": _safe_args(kwargs),
                            "result_count": len(result) if isinstance(result, list) else result.get("offers_count")})
        return result


def _safe_args(kwargs: dict) -> dict:
    # Do not serialize the SQLite connection into traces.
    return {key: value for key, value in kwargs.items() if key != "conn"}


def execute_readonly_workflow(db_path: Path, *, threshold_days: int = 90,
                              max_candidates: int = 10) -> tuple[list[dict], Harness]:
    if not 1 <= threshold_days <= 3650:
        raise ValueError("threshold_days must be between 1 and 3650")
    if not 1 <= max_candidates <= 20:
        raise ValueError("max_candidates must be between 1 and 20")
    harness = Harness(max_tool_calls=max_candidates + 1)
    with closing(read_only_connection(db_path)) as conn:
        candidates = harness.call("list_candidates", list_candidates, conn=conn,
                                  days=threshold_days, limit=max_candidates)
        records = []
        for item in candidates:
            market = harness.call("market_evidence", market_evidence,
                                  conn=conn, oem=item["oem"])
            records.append(evaluate_case(item, market, age_days=threshold_days))
    return records, harness


def write_outputs(directory: Path, *, records: list[dict], harness: Harness,
                  threshold_days: int, mode: str, llm_notes: str | None = None) -> None:
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"Output dir not empty (no overwrite): {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "demo_only": True,
        "data_origin": "100% synthetic",
        "mode": mode,
        "threshold_days": threshold_days,
        "total_cases": len(records),
        "cases": records,
    }
    (directory / "report.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    headings = ["# OEM Intelligence Agent — demo report", "", "Data: entirely synthetic. No live market lookup.",
                f"Cases: {len(records)} | Aged threshold: {threshold_days} days | Mode: {mode}", ""]
    for case in records:
        m = case["market"]
        headings.extend([
            f"## #{case['inventory_id']} · {case['part_name']} — {case['priority'].upper()}",
            f"OEM: `{case['oem']}` | Stock: {case['stock_qty']} | Age: {case['days_in_stock']} days",
            f"Reasons: {', '.join(case['reasons'])}",
            f"Market: {m['offers_count']} listings; {m['total_listed_units']} listed units; "
            f"min EUR {m['min_price_eur'] if m['min_price_eur'] is not None else 'sin datos'}; "
            f"max EUR {m['max_price_eur'] if m['max_price_eur'] is not None else 'sin datos'}",
            f"Flags: {'; '.join(case['risk_flags']) if case['risk_flags'] else 'none'}",
            "Action: human review only.", ""
        ])
    (directory / "report.md").write_text("\n".join(headings), encoding="utf-8")
    (directory / "trace.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in harness.events),
        encoding="utf-8",
    )
    if llm_notes is not None:
        (directory / "llm_notes_UNVERIFIED.md").write_text(
            "# LLM advisory notes (NOT factual ground truth)\n\n" + llm_notes + "\n",
            encoding="utf-8",
        )
