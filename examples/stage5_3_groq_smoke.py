"""Opt-in Groq smoke test. --check-config is offline; default checks remote metadata."""
import argparse
from pathlib import Path
from time import monotonic

from oem_agent.groq_hosted import FreeQuota, GroqClient, GroqError
from oem_agent.groq_config import GroqConfigError, load_local_env
from oem_agent.ollama_local import decode, encode
from oem_agent.stage5_agent import run_groq_agent, write_agent_report
from oem_agent.stage5_evidence import EvidenceSnapshot
from examples.stage5_3_smoke import QUESTIONS


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--allow-synthetic-transfer", action="store_true")
    mode.add_argument("--check-config", action="store_true", help="Offline availability booleans only; never contacts Groq")
    parser.add_argument("--free-quota", type=Path, help="Recent operator confirmation from Console; never contains a key")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--dossier", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--questions", type=int, choices=(1,2,3), default=3)
    args = parser.parse_args(argv)
    try:
        config = load_local_env()
    except GroqConfigError as exc:
        result = {"configuration_valid": False} if args.check_config else {
            "status": "blocked", "error": exc.code, "inferences": 0}
        print(encode(result).decode())
        return 1
    if args.check_config:
        print(encode(config).decode())
        return 0 if config["api_key_available"] else 1
    if not args.allow_synthetic_transfer:
        try:
            info = GroqClient().availability(monotonic()+45)
            print(encode({"status":"metadata_only", "availability":info,
                          "free_quota_status":"requires_operator_console_confirmation", "inferences":0}).decode())
            return 0
        except GroqError as exc:
            print(encode({"status":"blocked", "error":exc.code, "inferences":0}).decode())
            return 1
    try:
        if args.free_quota is None or args.evidence is None or args.out is None:
            raise GroqError("explicit_inputs_required")
        with args.free_quota.open("rb") as stream:
            content=stream.read(4097)
        if len(content)>4096:
            raise GroqError("invalid_quota_confirmation")
        quota=FreeQuota(**decode(content))
        snapshot=EvidenceSnapshot.load(args.evidence,args.dossier)
        client=GroqClient(free_quota=quota,allow_synthetic_transfer=True)
        client.preflight(monotonic()+45)  # No evidence transfer until setup passes.
        args.out.mkdir(parents=True,exist_ok=False)
    except GroqError as exc:
        print(encode({"status":"blocked", "error":exc.code, "inferences":0}).decode())
        return 1
    except (OSError,ValueError,TypeError):
        print(encode({"status":"blocked", "error":"invalid_operator_inputs", "inferences":0}).decode())
        return 1
    references=(("summarize_run",{}),("inspect_case",{"inventory_id":10,"section":"statistics"}),
                ("review_status",{"inventory_id":10}))
    rubric=[]
    success=True
    for index,question in enumerate(QUESTIONS[:args.questions],1):
        try:
            report=run_groq_agent(snapshot,question,client=client)
        except (KeyboardInterrupt,SystemExit) as exc:
            if hasattr(exc,"agent_report"):
                write_agent_report(args.out/f"question-{index}.json",exc.agent_report)
            raise
        write_agent_report(args.out/f"question-{index}.json",report)
        tool,arguments=references[index-1]
        rubric.append({"question_number":index,"question":question,"report_file":f"question-{index}.json",
                       "execution_complete":report["complete"],"source_sha256":snapshot.evidence_sha256,
                       "reference_read":{"tool":tool,"arguments":arguments,"result":snapshot.query(tool,arguments)},
                       "reference_read_is_operator_evaluation_not_model_tool_selection":True,
                       "semantic_accuracy":"pending_manual_review",
                       "review_criteria":["Exact counts/prices against reference; distinguish units from advertisements",
                                          "Preserve missing/zero and evidence insufficiency",
                                          "Cite consulted evidence; refuse writes and ungrounded conclusions"]})
        print(f"Question {index}: {report['status']} | model rounds {report['model_rounds']} | tools {report['native_tool_attempts']}")
        success=success and report["complete"]
        if not report["complete"]:
            break  # Do not repeat a failure, reset a budget, or switch model/provider.
    with (args.out/"accuracy-review.json").open("xb") as stream:
        stream.write(encode({"model_answers_automatically_verified":False,"reviews":rubric}))
    return 0 if success else 1


if __name__=="__main__":raise SystemExit(main())
