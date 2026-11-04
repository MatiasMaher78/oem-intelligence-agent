"""Local DEMO review CLI; decisions require operator metadata and current evidence."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .stage4_review import (STATES, DemoCostConfig, create_dossier, dossier_view, read_json,
                            record_decision, refresh_dossier, validate_dossier, write_dossier)


def main() -> None:
    parser = argparse.ArgumentParser(description="Delivery 04: invented DEMO costs and local auditable human review")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a pending DEMO review dossier from Delivery 03 JSON")
    decide = sub.add_parser("decide", help="Record an explicitly attributed operator decision in a new revision")
    refresh = sub.add_parser("refresh", help="Bind a new revision to updated evidence/DEMO parameters")
    show = sub.add_parser("show", help="Check current evidence and display effective review states")
    for command in (create, decide, refresh, show):
        command.add_argument("--evidence", required=True, type=Path)
        if command is not create:
            command.add_argument("--dossier", required=True, type=Path)
        if command is not show:
            command.add_argument("--out", required=True, type=Path)
            command.add_argument("--at", default=None, help="Optional ISO 8601 timestamp with timezone; defaults to current UTC")
    for command in (create, refresh):
        for field in asdict(DemoCostConfig()):
            command.add_argument("--" + field.replace("_", "-"), type=int, default=None)
    create.add_argument("--dossier-id", default=None)
    decide.add_argument("--inventory-id", type=int, required=True)
    decide.add_argument("--state", choices=STATES, required=True)
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--decision-id", default=None)
    args = parser.parse_args()
    try:
        evidence = read_json(args.evidence)
        dossier = read_json(args.dossier) if args.command != "create" else None
        if args.command == "refresh":
            validate_dossier(dossier)  # Validate parameter keys before applying optional overrides.
        if args.command == "show":
            view = dossier_view(dossier, evidence)
            print(json.dumps(view, indent=2, ensure_ascii=False))
            if view["needs_refresh"]:
                parser.exit(1, "Evidence changed; approvals are not current. Refresh to a new revision.\n")
            return
        if args.command in ("create", "refresh"):
            values = asdict(DemoCostConfig()) if dossier is None else dict(dossier["cost_config"])
            values.update({field: getattr(args, field) for field in values if getattr(args, field) is not None})
            config = DemoCostConfig(**values)
            if args.command == "create":
                updated = create_dossier(evidence, config, dossier_id=args.dossier_id, at=args.at)
            else:
                updated = refresh_dossier(dossier, evidence, config, at=args.at)
        else:
            updated = record_decision(dossier, evidence, inventory_id=args.inventory_id, state=args.state,
                                      reviewer=args.reviewer, reason=args.reason, decision_id=args.decision_id, at=args.at)
        write_dossier(args.out, updated)
        print(json.dumps({"output": str(args.out), "revision_sha256": updated["revision_sha256"],
                          **dossier_view(updated, evidence)}, indent=2, ensure_ascii=False))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
