"""Versioned read contracts only; no model adapter or agent execution loop."""
from __future__ import annotations

from copy import deepcopy

from .stage2_db import MARKETPLACES as SOURCE_MARKETPLACES

CONTRACT_VERSION = "5.1"
EVIDENCE_STATES = ("sufficient_sample_demo", "insufficient_sample", "no_comparable_offers",
                   "empty_filtered_sample", "source_error")
MARKETPLACES = tuple(identifier for identifier, _ in SOURCE_MARKETPLACES)


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


TEXT = {"type": "string"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
RAW = {"type": "object"}  # Original, versioned Delivery 01-04 fields; retained without conversion.
REFERENCE = _object({"document": {"enum": ["evidence", "dossier"]}, "sha256": TEXT, "pointer": TEXT})
PAGE_INPUT = {"cursor": {"type": "string", "minLength": 1, "maxLength": 100},
              "limit": {"type": "integer", "minimum": 1, "maximum": 20}}
ID = {"type": "integer", "minimum": 1}
INVENTORY = _object({"id": ID, **{key: {"type": ["string", "null"]}
                    for key in ("oem", "part_name", "brand", "model", "version")},
                    "stock_qty": INTEGER, "days_in_stock": INTEGER})
INVENTORY["additionalProperties"] = True  # Preserve any source metadata alongside the declared fields.


def _page_output(item):
    return _object({"scope": TEXT, "items": {"type": "array", "items": item},
                    "total_rows": INTEGER, "returned_rows": INTEGER,
                    "next_cursor": {"type": ["string", "null"]}})


def _output(data):
    return _object({
        "contract_version": {"const": CONTRACT_VERSION}, "tool": TEXT,
        "status": {"enum": ["ok", "partial", "not_available", "error"]},
        "snapshot_id": TEXT, "evidence_sha256": TEXT,
        "coverage": _object({"status": TEXT, "complete": BOOLEAN, "audit_complete": BOOLEAN,
                             "total_candidates": {"type": ["integer", "null"]}}),
        "data": {"anyOf": [data, {"type": "null"}]},
        "references": {"type": "array", "items": REFERENCE},
        "warnings": {"type": "array", "items": TEXT},
        "error": {"anyOf": [_object({"code": TEXT, "message": TEXT}), {"type": "null"}]},
        "trace": _object({"operation": TEXT, "arguments": RAW, "status": TEXT,
                          "request_sha256": TEXT, "result_sha256": TEXT}),
    })


_CONTRACTS = {
    "summarize_run": ("Summarize selected synthetic evidence and source coverage.", _object({}),
                      _object({"selection": RAW, "marketplaces": {"type": "array", "items": TEXT},
                               "evidence_status_counts": RAW, "marketplace_status_counts": RAW,
                               "source_offers_count": {"type": ["integer", "null"]},
                               "review_available": BOOLEAN})),
    "list_cases": ("List every matching selected piece through explicit pagination; OEM is literal text.",
                   _object({**PAGE_INPUT, "oem": {"type": "string", "minLength": 1},
                            "evidence_status": {"enum": list(EVIDENCE_STATES)}}, []),
                   _page_output(_object({"inventory": INVENTORY, "evidence_status": TEXT,
                                         "matching_complete": BOOLEAN,
                                         "has_confirmed_match": {"type": ["boolean", "null"]}}))),
    "inspect_case": ("Read inventory, matching summaries, statistics or source traces for one selected ID.",
                     _object({"inventory_id": ID, "section": {"enum": ["inventory", "matching", "statistics", "trace"]}},
                             ["inventory_id"]),
                     _object({"inventory_id": ID, "section": TEXT, "content": RAW})),
    "list_offers": ("Read offer audit rows, including excluded rows; rows are not unique advertisements.",
                    _object({**PAGE_INPUT, "inventory_id": ID,
                             "marketplace_id": {"enum": list(MARKETPLACES)}}, []),
                    _page_output(_object({"inventory_id": {"type": ["integer", "null"]}, "audit": RAW}))),
    "review_status": ("Read effective review state against pinned current evidence; cannot record decisions.",
                      _object({"inventory_id": ID}),
                      _object({"dossier_id": TEXT, "revision": INTEGER, "needs_refresh": BOOLEAN,
                               "case": RAW, "current_decision": {"type": ["object", "null"]}})),
}


def tool_contracts() -> list[dict]:
    """Return independent JSON Schema input/output declarations, not executable permissions."""
    return deepcopy([{"name": name, "description": description, "input_schema": inputs,
             "output_schema": _output(deepcopy(output))}
            for name, (description, inputs, output) in _CONTRACTS.items()])


def validate_arguments(tool: str, arguments: dict) -> None:
    """Validate the intentionally flat input contracts without a schema dependency."""
    if tool not in _CONTRACTS:
        raise ValueError("Unknown read contract")
    schema = _CONTRACTS[tool][1]
    if type(arguments) is not dict or set(arguments) - set(schema["properties"]):
        raise ValueError("Arguments must be an object containing only declared fields")
    if set(schema["required"]) - set(arguments):
        raise ValueError("Missing required argument")
    for key, value in arguments.items():
        field = schema["properties"][key]
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"Invalid {key}")
        if field.get("type") == "integer" and (type(value) is not int
                or not field.get("minimum", value) <= value <= field.get("maximum", value)):
            raise ValueError(f"Invalid {key}: expected bounded integer")
        if field.get("type") == "string" and (not isinstance(value, str)
                or not field.get("minLength", 0) <= len(value) <= field.get("maxLength", len(value))):
            raise ValueError(f"Invalid {key}: expected bounded text")
