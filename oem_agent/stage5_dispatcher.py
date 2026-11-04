"""Stage 5.2: bounded, audited dispatch of the five existing read contracts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import partial
import json
import math
from pathlib import Path
from threading import Lock
from time import monotonic

from .harness import Harness, PolicyViolation
from .stage4_review import fingerprint
from .stage5_contracts import CONTRACT_VERSION, tool_contracts, validate_arguments
from .stage5_evidence import EvidenceError, EvidenceSnapshot

_OMITTED = object()


@dataclass(frozen=True)
class DispatchLimits:
    max_calls: int = 8
    max_errors: int = 2
    session_seconds: float = 180
    tool_seconds: float = 2
    request_bytes: int = 4096
    response_bytes: int = 8192

    def __post_init__(self):
        for name, maximum in (("max_calls", 8), ("max_errors", 2),
                              ("request_bytes", 4096), ("response_bytes", 8192)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be a positive integer no greater than {maximum}")
        for name, maximum in (("session_seconds", 180), ("tool_seconds", 2)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} must be finite, positive and no greater than {maximum}")


class _DispatchFault(PolicyViolation):
    def __init__(self, code, message, *, terminal=False):
        self.code, self.terminal = code, terminal
        super().__init__(message)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _matches(value, schema):
    """The small JSON Schema vocabulary used by the fixed 5.1 output contracts."""
    if "anyOf" in schema:
        return any(_matches(value, choice) for choice in schema["anyOf"])
    if "const" in schema and value != schema["const"] or "enum" in schema and value not in schema["enum"]:
        return False
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    types = {"object": dict, "array": list, "integer": int, "string": str, "boolean": bool, "null": type(None)}
    if kinds and not any(type(value) is types[kind] for kind in kinds):
        return False
    if type(value) is dict:
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(_matches(item, properties[key]) for key, item in value.items() if key in properties)
    if type(value) is list:
        return all(_matches(item, schema.get("items", {})) for item in value)
    if type(value) is int and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
        return False
    return True


class ReadOnlyDispatcher:
    """One serialized session over an immutable snapshot; callers cannot supply callbacks."""

    def __init__(self, snapshot: EvidenceSnapshot, *, limits: DispatchLimits | None = None, allowed_tools=None):
        if type(snapshot) is not EvidenceSnapshot:
            raise TypeError("An already validated EvidenceSnapshot is required")
        if limits is not None and type(limits) is not DispatchLimits:
            raise TypeError("Expected DispatchLimits")
        self._snapshot = snapshot
        self._limits = limits or DispatchLimits()
        self._contracts = {entry["name"]: entry for entry in tool_contracts()}
        if allowed_tools is None:
            allowed = frozenset(self._contracts)
        elif (type(allowed_tools) not in (tuple, list, set, frozenset)
              or any(type(name) is not str for name in allowed_tools)):
            raise ValueError("Allowlist must contain only existing read-contract names")
        else:
            allowed = frozenset(allowed_tools)
        if not allowed <= self._contracts.keys():
            raise ValueError("The dispatcher cannot grant additional tools")
        self._harness = Harness(max_tool_calls=self._limits.max_calls, allowed_tools=allowed)
        evidence = snapshot.evidence
        self._coverage = {key: evidence[key] for key in ("status", "complete", "audit_complete", "total_candidates")}
        self._attempts = self._errors = 0
        self._stop_reason = None
        self._missing = self._finalized = False
        self._seen, self._completed_pages, self._pending = set(), set(), {}
        self._events = []
        self._started = monotonic()
        self._finished = None
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._lock = Lock()

    def contracts(self):
        return deepcopy([entry for name, entry in self._contracts.items() if name in self._harness.allowed_tools])

    def _state(self):
        return {"attempts": self._attempts, "budget_used": min(self._attempts, self._limits.max_calls),
                "calls_remaining": max(0, self._limits.max_calls - self._attempts),
                "executions": self._harness.calls, "errors": self._errors,
                "stopped": self._stop_reason is not None or self._finalized,
                "stop_reason": self._stop_reason, "finalized": self._finalized,
                "pending_pages": len(self._pending)}

    def _time_guard(self, now):
        if now - self._started >= self._limits.session_seconds:
            raise _DispatchFault("session_timeout", "Session deadline exceeded", terminal=True)

    def _arguments(self, tool, arguments):
        if type(arguments) is not dict or any(type(key) is not str or type(value) not in (str, int, float, bool, type(None))
                                              for key, value in arguments.items()):
            raise _DispatchFault("invalid_arguments", "Expected the flat JSON object declared by the read contract")
        try:
            validate_arguments(tool, arguments)
        except ValueError as exc:
            raise _DispatchFault("invalid_arguments", "Arguments do not match the declared read contract") from exc
        if any(type(value) is str and len(value) > self._limits.request_bytes for value in arguments.values()):
            raise _DispatchFault("request_too_large", "Request exceeds its byte limit")
        try:
            encoded = _encode(arguments)
        except (ValueError, UnicodeError) as exc:
            raise _DispatchFault("invalid_arguments", "Arguments must be finite UTF-8 JSON values") from exc
        if len(encoded) > self._limits.request_bytes:
            raise _DispatchFault("request_too_large", "Request exceeds its byte limit")
        return json.loads(encoded)

    def _keys(self, tool, arguments):
        normalized = dict(arguments)
        if tool in ("list_cases", "list_offers"):
            normalized.setdefault("limit", 20)
        if tool == "inspect_case":
            normalized.setdefault("section", "statistics")
        request_key = fingerprint({"tool": tool, "arguments": normalized})
        filters = {key: value for key, value in arguments.items() if key not in ("limit", "cursor")}
        return request_key, fingerprint({"tool": tool, "filters": filters})

    def _check_result(self, tool, arguments, result):
        if (not _matches(result, self._contracts[tool]["output_schema"])
                or result["contract_version"] != CONTRACT_VERSION or result["tool"] != tool
                or result["snapshot_id"] != self._snapshot.snapshot_id
                or result["evidence_sha256"] != self._snapshot.evidence_sha256
                or result["coverage"] != self._coverage
                or (result["status"] == "ok" and not self._coverage["complete"])
                or (result["status"] == "partial" and self._coverage["complete"])
                or (result["status"] == "not_available" and tool != "review_status")):
            raise _DispatchFault("invalid_tool_result", "Reader returned an invalid or unrelated result", terminal=True)
        trace = result["trace"]
        if (trace["operation"] != tool or trace["arguments"] != arguments or trace["status"] != result["status"]
                or trace["request_sha256"] != fingerprint({"tool": tool, "arguments": arguments})
                or trace["result_sha256"] != fingerprint({key: value for key, value in result.items() if key != "trace"})
                or (result["status"] in ("ok", "partial") and (type(result["data"]) is not dict or result["error"] is not None))
                or (result["status"] == "error" and (result["error"] is None or result["data"] is not None))
                or (result["status"] == "not_available" and (result["error"] is not None or result["data"] is not None))):
            raise _DispatchFault("invalid_tool_result", "Reader result integrity or status is inconsistent", terminal=True)
        try:
            for reference in result["references"]:
                self._snapshot.resolve_reference(reference)
        except EvidenceError as exc:
            raise _DispatchFault("invalid_tool_result", "Reader supplied an invalid evidence reference", terminal=True) from exc
        if tool in ("list_cases", "list_offers") and result["status"] in ("ok", "partial"):
            scope = self._keys(tool, arguments)[1]
            previous = self._pending.get(scope)
            start = previous["seen"] if previous else 0
            page = result["data"]
            total, count, cursor = page["total_rows"], page["returned_rows"], page["next_cursor"]
            if (total < start or (previous and previous["total_rows"] != total)
                    or count != min(arguments.get("limit", 20), total - start) or count != len(page["items"])
                    or ((cursor is None) != (start + count == total))
                    or (cursor is not None and (not 1 <= len(cursor) <= 100 or cursor == arguments.get("cursor")))):
                raise _DispatchFault("invalid_tool_result", "Page totals, progress or continuation are inconsistent", terminal=True)

    def _invoke(self, tool, arguments):
        result = self._snapshot.query(tool, arguments)
        if type(result) is not dict:
            raise _DispatchFault("invalid_tool_result", "Reader did not return a structured envelope", terminal=True)
        return result

    def dispatch(self, tool, arguments=_OMITTED):
        """Count every attempt, then permit only a fixed read over the pinned snapshot."""
        with self._lock:
            return self._dispatch(tool, arguments)

    def _dispatch(self, tool, arguments):
        self._attempts += 1
        started = monotonic()
        name = tool if type(tool) is str and len(tool) <= 128 else "<invalid-tool-name>"
        raw = {} if arguments is _OMITTED else arguments
        request, result, result_digest, result_bytes = None, None, None, None
        error, interrupted = None, None
        before = self._harness.calls
        try:
            if self._finalized:
                raise _DispatchFault("session_closed", "Session has already been finalized", terminal=True)
            if self._stop_reason:
                raise _DispatchFault(self._stop_reason, "Session is stopped; no reader was called", terminal=True)
            if self._attempts > self._limits.max_calls:
                raise _DispatchFault("call_budget_exhausted", "No tool-call budget remains", terminal=True)
            self._time_guard(started)
            if type(tool) is not str:
                raise _DispatchFault("invalid_tool_name", "Tool name must be text")
            if tool not in self._harness.allowed_tools:
                raise _DispatchFault("tool_forbidden", "Tool is not in this session's read allowlist", terminal=True)
            request = self._arguments(tool, raw)
            key, scope = self._keys(tool, request)
            if key in self._seen or (tool in ("list_cases", "list_offers") and scope in self._completed_pages):
                raise _DispatchFault("repeated_call", "Repeated read has no progress in this immutable snapshot", terminal=True)
            if tool in ("list_cases", "list_offers"):
                if ("cursor" in request and self._pending.get(scope, {}).get("cursor") != request["cursor"]):
                    raise _DispatchFault("invalid_cursor_sequence", "Cursor was not issued for this session's next page")
                if "cursor" not in request and scope in self._pending:
                    raise _DispatchFault("repeated_call", "Restarting an unfinished page sequence is not progress", terminal=True)
            self._seen.add(key)
            result = self._harness.call(tool, partial(self._invoke, tool), arguments=request)
            self._time_guard(monotonic())
            if monotonic() - started >= self._limits.tool_seconds:
                raise _DispatchFault("tool_timeout", "Reader exceeded its deadline; late result discarded", terminal=True)
            try:
                encoded = _encode(result)
                result_digest, result_bytes = fingerprint(result), len(encoded)
                self._check_result(tool, request, result)
            except (ValueError, TypeError, KeyError, RecursionError) as exc:
                raise _DispatchFault("invalid_tool_result", "Reader result cannot satisfy its JSON contract", terminal=True) from exc
            if result_bytes > self._limits.response_bytes:
                raise _DispatchFault("response_too_large", "Result exceeds byte limit; use a smaller page or narrower read")
            if result["status"] == "error":
                raise _DispatchFault(result["error"]["code"], "Evidence reader reported a structured error")
            self._time_guard(monotonic())
            if monotonic() - started >= self._limits.tool_seconds:
                raise _DispatchFault("tool_timeout", "Validated result exceeded deadline and was discarded", terminal=True)
            if result["status"] == "not_available":
                self._missing = True
            if tool in ("list_cases", "list_offers"):
                cursor = result["data"]["next_cursor"]
                if cursor is not None:
                    seen = self._pending.get(scope, {}).get("seen", 0) + result["data"]["returned_rows"]
                    self._pending[scope] = {"cursor": cursor, "seen": seen, "total_rows": result["data"]["total_rows"]}
                else:
                    self._pending.pop(scope, None)
                    self._completed_pages.add(scope)
        except BaseException as exc:
            if isinstance(exc, _DispatchFault):
                code, message, terminal = exc.code, str(exc), exc.terminal
            elif isinstance(exc, EvidenceError):
                code, message, terminal = "evidence_error", "Evidence reader failed validation", True
            elif isinstance(exc, PolicyViolation):
                code, message, terminal = "policy_violation", "Underlying harness denied the call", True
            else:
                code, message, terminal = "tool_execution_error", "Reader failed; result withheld", True
                if not isinstance(exc, Exception):
                    interrupted = exc
            error = {"code": code, "message": message, "exception_type": type(exc).__name__}
            result = None
            self._errors += 1
            if terminal and self._stop_reason is None:
                self._stop_reason = code
            if self._errors >= self._limits.max_errors and self._stop_reason is None:
                self._stop_reason = "error_budget_exhausted"
        if self._attempts >= self._limits.max_calls and self._stop_reason is None:
            self._stop_reason = "call_budget_exhausted"
        incomplete = bool(self._errors or self._stop_reason or self._missing or self._pending or not self._coverage["complete"])
        outcome = {"dispatcher_version": "5.2", "attempt": self._attempts, "tool": name,
                   "status": "error" if error else "partial" if incomplete else "ok", "complete": False,
                   "snapshot_id": self._snapshot.snapshot_id, "evidence_sha256": self._snapshot.evidence_sha256,
                   "result": result, "error": error, "session": self._state()}
        event = {"sequence": len(self._events) + 1, "attempt": self._attempts, "tool": name,
                 "arguments": request, "argument_type": "omitted" if arguments is _OMITTED else type(arguments).__name__,
                 "executed": self._harness.calls > before, "elapsed_ms": round((monotonic() - started) * 1000, 3),
                 "tool_result_sha256": result_digest, "tool_result_bytes": result_bytes,
                 "outcome": deepcopy(outcome),
                 "previous_event_sha256": self._events[-1]["event_sha256"] if self._events else None}
        event["event_sha256"] = fingerprint(event)
        self._events.append(event)
        if interrupted is not None:
            raise interrupted
        return {**deepcopy(outcome), "event_sha256": event["event_sha256"]}

    def _report(self):
        complete = (self._finalized and self._attempts > 0 and not self._errors and not self._stop_reason
                    and not self._missing and not self._pending and self._coverage["complete"])
        report = {"dispatcher_version": "5.2", "demo_only": True, "started_at": self._started_at,
                  "contract_version": CONTRACT_VERSION, "snapshot_id": self._snapshot.snapshot_id,
                  "evidence_sha256": self._snapshot.evidence_sha256, "source_coverage": deepcopy(self._coverage),
                  "allowed_tools": sorted(self._harness.allowed_tools), "limits": asdict(self._limits),
                  **self._state(), "missing_requested_evidence": self._missing,
                  "status": "complete" if complete else "partial", "complete": complete,
                  "elapsed_ms": round(((self._finished if self._finished is not None else monotonic()) - self._started) * 1000, 3),
                  "trace": deepcopy(self._events)}
        report["report_sha256"] = fingerprint(report)
        return report

    def report(self):
        with self._lock:
            return self._report()

    def finish(self):
        """Close explicitly. Only an error-free, nonexhausted, fully paged session can be complete."""
        with self._lock:
            if not self._finalized:
                now = monotonic()
                if self._stop_reason is None:
                    try:
                        self._time_guard(now)
                    except _DispatchFault:
                        self._errors += 1
                        self._stop_reason = "session_timeout"
                self._finalized = True
                self._finished = now
            return self._report()

    def write_report(self, path: Path):
        """Operator-side audit export; this method is never a dispatchable tool."""
        path = Path(path)
        if path.suffix.lower() != ".json":
            raise ValueError("Dispatcher reports require a .json extension")
        content = _encode(self.report())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
