"""Reproducible offline evaluation; real Groq execution requires explicit operator opt-in."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from .groq_hosted import FreeQuota, GroqClient, GroqError, LocalBudget, MODEL
from .groq_config import load_local_env
from .ollama_local import OllamaError, OllamaLimits, encode, decode
from .stage4_review import fingerprint
from .stage5_agent import run_groq_agent, write_agent_report
from .stage5_context import answer_references, load_skill, CONFIGURATION_VERSION
from .stage5_contracts import tool_contracts
from .stage5_dispatcher import ReadOnlyDispatcher, DispatchLimits
from .stage5_eval_data import GROUND_TRUTH, SCENARIOS, build_dataset, dataset_identity, question
from .stage5_evidence import EvidenceSnapshot

EVALUATOR_VERSION = "5.5.1"


def _sealed(value, field):
    return value.get(field) == fingerprint({k: v for k, v in value.items() if k != field})


def _answer(report):
    text = report.get("answer")
    if not isinstance(text, str):
        return None
    text = text.rsplit("\n\nFuentes consultadas: ", 1)[0].strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    try:
        value = decode(text)
        if (set(value) != {"facts", "explanation"} or type(value["facts"]) is not dict
                or type(value["explanation"]) is not str or not value["explanation"].strip()):
            return None
        return value
    except OllamaError:
        return None


def grade(case_id, snapshot, report, *, simulated=False):
    """Strict finite-fact oracle, no LLM judge; free prose remains a human check."""
    expected = GROUND_TRUTH[case_id]
    parsed = _answer(report)
    facts = parsed["facts"] if parsed else {}
    checks = {"answer_format": parsed is not None,
              "exact_facts": fingerprint(facts) == fingerprint(expected),
              "abstention": type(facts.get("abstain")) is bool and facts["abstain"] is expected["abstain"]}
    dispatch = report["dispatcher"]
    trace = dispatch["trace"]
    checks["integrity"] = _sealed(report, "report_sha256") and _sealed(dispatch, "report_sha256")
    previous = None
    try:
        for event in trace:
            checks["integrity"] &= _sealed(event, "event_sha256") and event["previous_event_sha256"] == previous
            previous = event["event_sha256"]
            result = event["outcome"]["result"]
            if result:
                checks["integrity"] &= (result == snapshot.query(event["tool"], event["arguments"])
                                         and result["snapshot_id"] == snapshot.snapshot_id)
        refs = answer_references(snapshot, dispatch)
        checks["references"] = bool(refs) and refs == report["answer_references"] and report["references_verified"] is True
        footer = "\n\nFuentes consultadas: " + ", ".join("["+r["id"]+"]" for r in refs)
        checks["references"] &= bool(report["answer"] and report["answer"].endswith(footer))
    except (ValueError, KeyError, TypeError):
        checks["references"] = False
        checks["integrity"] = False
    reads = [(e["tool"], e["arguments"], e["outcome"]["result"]) for e in trace
             if e["executed"] and e["outcome"]["result"] and e["outcome"]["result"]["data"] is not None]
    identifier = SCENARIOS[case_id][0]
    sections = {args.get("section", "statistics") for tool, args, _ in reads
                if tool == "inspect_case" and args.get("inventory_id") == identifier}
    if case_id == "E2":
        audit = [item for tool, _, result in reads if tool == "list_offers"
                 for item in result["data"]["items"] if item["inventory_id"] == identifier]
        expected_rows = snapshot.evidence["results"][0]["offer_audit"]
        granular = Counter(fingerprint(item["audit"]) for item in audit) == Counter(fingerprint(row) for row in expected_rows)
        projected = snapshot.query("inspect_case", {"inventory_id": identifier, "section": "matching"})
        expected_summary = projected["data"]["content"].get("offer_summary") if projected["data"] else None
        summaries = [result["data"]["content"].get("offer_summary") for tool, args, result in reads
                     if tool == "inspect_case" and args.get("inventory_id") == identifier and args.get("section") == "matching"]
        aggregate = any(isinstance(value, dict) and value.get("coverage_complete") is True
                        and fingerprint(value) == fingerprint(expected_summary) for value in summaries)
        checks["tool_selection"] = "matching" in sections and (granular or aggregate) and dispatch["pending_pages"] == 0
    else:
        checks["tool_selection"] = "statistics" in sections and (case_id not in ("E4", "E5") or "matching" in sections)
    checks["coverage"] = (report["complete"] is (case_id != "E5") and dispatch["pending_pages"] == 0
                          and dispatch["source_coverage"]["complete"] is snapshot.evidence["complete"])
    checks["errors_and_limits"] = (report["error"] is None and report["errors"] == 0 and dispatch["stop_reason"] is None
                                  and 0 < dispatch["attempts"] < dispatch["limits"]["max_calls"] <= 8
                                  and 0 < report["model_rounds"] < report["limits"]["max_requests"] <= 9)
    try:
        OllamaLimits(**report["limits"])
        DispatchLimits(**dispatch["limits"])
    except (TypeError, ValueError):
        checks["errors_and_limits"] = False
    configuration = report.get("configuration", {})
    skill = load_skill()
    checks["configuration"] = (report.get("configuration_sha256") == fingerprint(configuration)
                                and configuration.get("contracts_sha256") == fingerprint(tool_contracts())
                                and configuration.get("version") == CONFIGURATION_VERSION
                                and configuration.get("model") == report["model"] == MODEL
                                and configuration.get("provider") == report.get("provider") == "groq"
                                and configuration.get("system_prompt_sha256") == sha256(skill.body.encode("utf-8")).hexdigest()
                                and configuration.get("skill") == {"name": skill.name, "version": skill.version, "sha256": skill.sha256}
                                and configuration.get("execution_limits") == report["limits"]
                                and configuration.get("dispatcher_limits") == dispatch["limits"])
    checks["traceability"] = (report["question"] == question(case_id)
                              and dispatch["snapshot_id"] == snapshot.snapshot_id and len(trace) == dispatch["attempts"]
                              and len(report["model_events"]) == report["model_rounds"]
                              and all("elapsed_ms" in e for e in trace+report["model_events"]))
    metrics = [e["metrics"] for e in report["model_events"] if "metrics" in e]
    token_counts = {"input": sum(m["prompt_eval_count"] for m in metrics),
                    "output": sum(m["eval_count"] for m in metrics)} if metrics else None
    value = {"evaluator_version": EVALUATOR_VERSION, "case_id": case_id,
             "mode": "scripted_offline" if simulated else "real_groq",
             "passed": all(checks.values()), "checks": checks,
             "expected_facts": deepcopy(expected), "observed_facts": facts,
             "expected_execution_complete": case_id != "E5", "execution_complete": report["complete"],
             "free_prose_accuracy": "pending_manual_review", "agent_answer_verified": False,
             "metrics": {"elapsed_ms": report["elapsed_ms"], "model_rounds": report["model_rounds"],
                         "tool_attempts": dispatch["attempts"], "tool_executions": dispatch["executions"],
                         "tools": [e["tool"] for e in trace], "errors": report["errors"],
                         "reported_tokens": token_counts, "source_errors": sum(e["status"] == "error" for e in snapshot.evidence["trace"]),
                         "token_source": "simulated_protocol" if simulated else "provider_usage",
                         "all_completions_have_usage": len(metrics) == report["model_rounds"]},
             "report_sha256": report["report_sha256"], "snapshot_id": snapshot.snapshot_id,
             "configuration_sha256": report.get("configuration_sha256"), "question_sha256": fingerprint(question(case_id))}
    return {**value, "evaluation_sha256": fingerprint(value)}


def _call(name, arguments, identifier):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _frame(content="", calls=None):
    message = {"role": "assistant", "content": content}
    if calls: message["tool_calls"] = calls
    return {"model": MODEL, "object": "chat.completion", "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280}}


class ScriptedProtocol:
    """An explicit test transcript, NOT a model or business-question router."""
    def __init__(self, case_id, *, frames=None):
        self.case_id, self.rounds, self.requests = case_id, 0, []
        self.frames = iter(frames) if frames is not None else None

    def request(self, method, path, payload, deadline):
        self.requests.append((method, path, deepcopy(payload)))
        if (method, path) == ("GET", "/openai/v1/models"):
            return {"data": [{"id": MODEL, "active": True, "context_window": 131072}]}
        if (method, path) != ("POST", "/openai/v1/chat/completions"):
            raise AssertionError("Unexpected evaluation endpoint")
        self.rounds += 1
        if self.frames is not None:
            frame = next(self.frames)
            if isinstance(frame, BaseException): raise frame
            return deepcopy(frame)
        identifier = SCENARIOS[self.case_id][0]
        if self.rounds == 1:
            calls = [_call("inspect_case", {"inventory_id": identifier, "section": "matching" if self.case_id == "E2" else "statistics"}, "read_1")]
            if self.case_id == "E2": calls.append(_call("inspect_case", {"inventory_id": identifier, "section": "statistics"}, "read_2"))
            if self.case_id in ("E4", "E5"): calls.append(_call("inspect_case", {"inventory_id": identifier, "section": "matching"}, "read_2"))
            return _frame(calls=calls)
        return _frame(json.dumps({"facts": GROUND_TRUTH[self.case_id], "explanation": "Respuesta de protocolo simulada; requiere revision humana en una prueba real."}))


def simulated_run(case_id, snapshot, *, frames=None):
    protocol = ScriptedProtocol(case_id, frames=frames)
    quota = FreeQuota(datetime.now(timezone.utc).isoformat(), 30, 1000, 8000, 200000, None, None)
    client = GroqClient(free_quota=quota, allow_synthetic_transfer=True)
    # The real wire is unreachable in this scope; all calls use the explicit transcript.
    with patch.object(GroqClient, "_request", side_effect=protocol.request):
        report = run_groq_agent(snapshot, question(case_id), client=client)
    return report



def evaluate_controls(snapshots):
    """Independent negative probes of the current harness, no provider or added tools."""
    snap = snapshots["E1"]
    probes = [
        ("forbidden_shell", [("shell", {})], "tool_forbidden"),
        ("forbidden_approval", [("approve_review", {"inventory_id": 1001})], "tool_forbidden"),
        ("malformed_arguments", [("inspect_case", {"inventory_id": True})], "invalid_arguments"),
        ("unknown_inventory", [("inspect_case", {"inventory_id": 999999})], "unknown_inventory_id"),
        ("repeated_read", [("summarize_run", {}), ("summarize_run", {})], "repeated_call"),
        ("call_budget", [("summarize_run", {}), ("review_status", {"inventory_id": 1001})], None),
        ("unfinished_page", [("list_offers", {"inventory_id": 1001, "limit": 1})], None),
    ]
    results = []
    for name, calls, expected_error in probes:
        dispatcher = ReadOnlyDispatcher(snap, limits=DispatchLimits(max_calls=2) if name == "call_budget" else None)
        outcomes = [dispatcher.dispatch(tool, arguments) for tool, arguments in calls]
        report = dispatcher.finish()
        code = outcomes[-1]["error"]["code"] if outcomes[-1]["error"] else None
        ok = not report["complete"] and report["attempts"] == len(calls) and code == expected_error
        if name.startswith("forbidden_"):ok &= report["executions"] == 0
        if name == "call_budget":ok &= report["stop_reason"] == "call_budget_exhausted"
        if name == "unfinished_page":ok &= report["pending_pages"] == 1
        results.append({"probe": name, "passed": bool(ok), "report": report})
    missing = EvidenceSnapshot(snap.evidence)
    dispatcher = ReadOnlyDispatcher(missing)
    outcome = dispatcher.dispatch("review_status", {"inventory_id": 1001}); report = dispatcher.finish()
    results.append({"probe": "missing_review", "passed": not report["complete"] and outcome["result"]["status"] == "not_available", "report": report})
    for code in ("groq_rate_limited", "groq_auth_failed", "context_budget_exceeded", "session_timeout"):
        report = simulated_run("E1", snap, frames=[GroqError(code)])
        results.append({"probe": code, "passed": not report["complete"] and report["answer"] is None
                        and report["model_rounds"] == 1 and report["dispatcher"]["executions"] == 0
                        and report["error"]["code"] == code, "report": report})
    return {"mode": "scripted_offline", "passed": sum(item["passed"] for item in results),
            "total": len(results), "probes": results}


def evaluate_batch(snapshots, case_ids, out, *, client=None):
    """Shared client/budget for real calls, stop on any incomplete execution or failed check."""
    case_ids = tuple(case_ids)
    if not case_ids or len(set(case_ids)) != len(case_ids) or any(key not in SCENARIOS for key in case_ids):
        raise ValueError("Select distinct evaluation cases")
    real = client is not None
    if real and (type(client) is not GroqClient or len(case_ids) > 3):
        raise ValueError("At most three cases with the existing Groq client")
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    identity = dataset_identity(snapshots)
    def write(name, data):
        with (out/name).open("xb") as f: f.write(encode(data))
    write("dataset.json", identity)
    evaluations = []
    for key in case_ids:
        snapshot = snapshots[key]
        write(f"{key}-evidence.json", snapshot.evidence)
        write(f"{key}-dossier.json", snapshot.dossier)
        try:
            report = run_groq_agent(snapshot, question(key), client=client) if real else simulated_run(key, snapshot)
        except (KeyboardInterrupt, SystemExit) as exc:
            if hasattr(exc, "agent_report"):write_agent_report(out/f"{key}-agent.json", exc.agent_report)
            raise
        write_agent_report(out/f"{key}-agent.json", report)
        evaluation = grade(key, snapshot, report, simulated=not real)
        write(f"{key}-evaluation.json", evaluation); evaluations.append(evaluation)
        if real and (not evaluation["passed"] or not report["complete"]):break
    summary = {"evaluator_version": EVALUATOR_VERSION, "mode": "real_groq" if real else "scripted_offline",
               "dataset_sha256": identity["dataset_sha256"], "requested_cases": list(case_ids),
               "evaluated_cases": [e["case_id"] for e in evaluations], "passed": sum(e["passed"] for e in evaluations),
               "total": len(evaluations), "all_requested_evaluated": len(evaluations) == len(case_ids),
               "no_model_quality_claim_from_scripted_runs": True,
               "manual_prose_review_required": True, "evaluations": evaluations}
    summary["check_pass_counts"] = {name: sum(e["checks"][name] for e in evaluations) for name in evaluations[0]["checks"]}
    if not real:
        controls = evaluate_controls(snapshots)
        write("controls.json", controls)
        summary["controls"] = {"passed": controls["passed"], "total": controls["total"]}
    if real:summary["provider_budget"] = client.budget_status
    summary["summary_sha256"] = fingerprint(summary)
    write("summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("offline", "real"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", choices=tuple(SCENARIOS), nargs="+")
    parser.add_argument("--allow-synthetic-transfer", action="store_true")
    parser.add_argument("--free-quota", type=Path)
    args = parser.parse_args(argv)
    try:
        case_ids = args.cases or (list(SCENARIOS) if args.mode == "offline" else ["E1"])
        client = None
        if args.mode == "real":
            if not args.allow_synthetic_transfer or args.free_quota is None or len(case_ids) > 3:
                raise GroqError("explicit_evaluation_authorization_required")
            # Verify local paths BEFORE loading credentials or contacting the provider.
            if args.out.exists() or len(set(case_ids)) != len(case_ids):
                raise ValueError("Output must be new; cases must be distinct")
            with args.free_quota.open("rb") as f: content = f.read(4097)
            if len(content) > 4096:raise ValueError("Invalid quota file")
            quota = FreeQuota(**decode(content))
            load_local_env()
            client = GroqClient(free_quota=quota, allow_synthetic_transfer=True,
                                local_budget=LocalBudget(max_requests=9, max_tokens=24000),
                                limits=OllamaLimits())
            client.preflight(monotonic()+45)
        result = evaluate_batch(build_dataset(), case_ids, args.out, client=client)
        print(encode({"mode": result["mode"], "passed": result["passed"], "total": result["total"],
                      "all_requested_evaluated": result["all_requested_evaluated"], "out": str(args.out)}).decode())
        return 0 if (result["passed"] == result["total"] and result["all_requested_evaluated"]
                     and ("controls" not in result or result["controls"]["passed"] == result["controls"]["total"])) else 1
    except (OSError, ValueError, OllamaError) as exc:
        print(encode({"status": "blocked", "error": exc.code if isinstance(exc, OllamaError) else "invalid_evaluation_inputs"}).decode())
        return 1


if __name__ == "__main__":raise SystemExit(main())
