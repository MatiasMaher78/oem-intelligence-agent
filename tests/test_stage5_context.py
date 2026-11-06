"""Offline context, configuration identity and verifiable answer-source regressions."""
from dataclasses import asdict,replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_groq_hosted import FakeAPI,frame,call,quota
from oem_agent.groq_hosted import GroqClient,GroqError,MODEL
from oem_agent.ollama_local import OllamaError,OllamaLimits
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import execute_stage2_statistics
from oem_agent.stage4_review import create_dossier,fingerprint
from oem_agent.stage5_agent import run_groq_agent,write_agent_report,SYSTEM_PROMPT
from oem_agent.stage5_context import load_skill,configuration_record,answer_references,CONFIGURATION_VERSION
from oem_agent.stage5_contracts import tool_contracts
from oem_agent.stage5_dispatcher import DispatchLimits
from oem_agent.stage5_evidence import EvidenceSnapshot,EvidenceError


class ContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=tempfile.TemporaryDirectory();cls.addClassCleanup(folder.cleanup)
        path=Path(folder.name)/"synthetic.sqlite";seed_statistics_demo(path)
        cls.evidence=execute_stage2_statistics(path)
        cls.dossier=create_dossier(cls.evidence,dossier_id="CONTEXT-TEST",at="2026-11-05T12:00:00+00:00")
        cls.snapshot=EvidenceSnapshot(cls.evidence,cls.dossier)

    def run_frames(self,frames=None,*,snapshot=None,api=None):
        api=api or FakeAPI(frames if frames is not None else [frame(calls=[call()]),frame("27 pieces, one sufficient DEMO sample.")])
        client=GroqClient(free_quota=quota(),allow_synthetic_transfer=True)
        with patch.object(GroqClient,"_request",side_effect=api):
            report=run_groq_agent(snapshot or self.snapshot,"Synthetic inventory evidence question",client=client)
        return report,api

    def config(self,skill=None,**changes):
        inputs=dict(model=MODEL,provider="groq",limits=asdict(OllamaLimits()),
                    dispatcher_limits=asdict(DispatchLimits()),contracts=tool_contracts(),model_info=None)
        return configuration_record(skill or load_skill(),**(inputs|changes))

    def test_one_bundled_versioned_skill_supplies_compact_prompt(self):
        skill=load_skill()
        self.assertEqual(skill.name,"oem-evidence-review")
        self.assertEqual(skill.version,"1.0.0")
        self.assertEqual(skill.body,SYSTEM_PROMPT)
        self.assertLessEqual(len(SYSTEM_PROMPT.encode("utf-8")),1600)
        self.assertNotIn("metadata:",SYSTEM_PROMPT)
        root=Path(__file__).resolve().parents[1]
        paths=list((root/"oem_agent/skills").rglob("SKILL.md"))
        self.assertEqual(len(paths),1)
        self.assertFalse(list((root/"skills").rglob("SKILL.md")))
        self.assertEqual(sha256(paths[0].read_bytes()).hexdigest(),skill.sha256)

    def test_invalid_missing_oversized_or_duplicate_skill_is_rejected_without_secret_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"SKILL.md"
            for text in (None,"PRIVATE_SKILL_MARKER", "x"*4097,
                         '---\nname: oem-evidence-review\nmetadata:\n  version: "1.0.0"\n  version: "2.0.0"\n---\nPRIVATE_SKILL_MARKER',
                         '---\nname: oem-evidence-review\nmetadata:\n  version: "invalid"\n---\nPRIVATE_SKILL_MARKER'):
                if text is not None:path.write_text(text,encoding="utf-8")
                with self.assertRaises(OllamaError) as error:load_skill(path)
                self.assertEqual(error.exception.code,"invalid_agent_skill")
                self.assertNotIn("PRIVATE_SKILL_MARKER",str(error.exception))

    def test_config_identity_changes_with_skill_model_limits_or_contracts(self):
        skill=load_skill();base,digest=self.config(skill)
        self.assertEqual(digest,fingerprint(base))
        for kwargs in ({"model":"another-model"},{"limits":asdict(OllamaLimits(output_tokens=256))},
                       {"contracts":tool_contracts()[:-1]}):
            self.assertNotEqual(self.config(**kwargs)[1],digest)
        self.assertNotEqual(self.config(replace(skill,version="1.0.1"))[1],digest)
        self.assertNotEqual(self.config(replace(skill,body=skill.body+" Extra instruction."))[1],digest)
        self.assertEqual(base["version"],CONFIGURATION_VERSION)

    def test_config_allowlist_excludes_keys_private_fields_and_transient_usage(self):
        limits={"requests_per_minute":30,"requests_per_day":1000,"tokens_per_minute":8000,"tokens_per_day":200000}
        info={"api_key":"PRIVATE_CONFIG_MARKER","reasoning":"PRIVATE_CONFIG_MARKER",
              "operator_confirmed_free_quota":limits|{"checked_at":"PRIVATE_CONFIG_MARKER","remaining_tokens_day":12}}
        config,digest=self.config(model_info=info,local_budget={"max_requests":4,"max_tokens":8000})
        self.assertNotIn("PRIVATE_CONFIG_MARKER",json.dumps(config))
        self.assertEqual(config["operator_confirmed_free_limits"],limits)
        info["operator_confirmed_free_quota"]["remaining_tokens_day"]=0
        self.assertEqual(self.config(model_info=info,local_budget={"max_requests":4,"max_tokens":8000})[1],digest)

    def test_trace_config_matches_actual_native_prompt_and_schemas(self):
        report,api=self.run_frames()
        posts=[payload for _,path,payload in api.requests if path.endswith("completions")]
        config=report["configuration"]
        self.assertEqual(config["system_prompt_sha256"],sha256(posts[0]["messages"][0]["content"].encode()).hexdigest())
        self.assertEqual(posts[0]["messages"][0]["content"],load_skill().body)
        self.assertEqual(config["contracts_sha256"],fingerprint(tool_contracts()))
        self.assertEqual(report["configuration_sha256"],fingerprint(config))
        self.assertEqual(config["execution_limits"],report["limits"])
        self.assertEqual(config["dispatcher_limits"],report["dispatcher"]["limits"])
        self.assertEqual(config["provider"],"groq")
        self.assertEqual(len(posts[0]["tools"]),5)
        self.assertTrue(all(post["tool_choice"]=="auto" for post in posts))

    def test_complete_answer_has_resolvable_sources_without_semantic_validation_claim(self):
        report,_=self.run_frames()
        self.assertTrue(report["complete"])
        self.assertTrue(report["references_verified"])
        self.assertFalse(report["answer_verified"])
        self.assertEqual([r["id"] for r in report["answer_references"]],["R1"])
        for source in report["answer_references"]:
            self.snapshot.resolve_reference(source["reference"])
            self.assertIn("["+source["id"]+"]",report["answer"])
        expected=report["dispatcher"]["trace"][0]["outcome"]["result"]["references"][0]
        self.assertEqual(report["answer_references"][0]["reference"],expected)

    def test_report_sources_are_not_added_to_llm_history_or_native_results(self):
        report,api=self.run_frames()
        self.assertIn("Fuentes consultadas",report["answer"])
        outbound=[payload for _,path,payload in api.requests if path.endswith("completions")]
        self.assertNotIn("Fuentes consultadas",json.dumps(outbound))
        self.assertNotIn("answer_references",json.dumps(outbound))

    def test_all_27_cost_sources_are_preserved_and_dossier_is_deduplicated(self):
        api=FakeAPI();original=api.__call__;rounds=0
        def model(method,path,payload,deadline):
            nonlocal rounds
            if not path.endswith("completions"):return original(method,path,payload,deadline)
            api.requests.append((method,path,payload));rounds+=1
            args={"view":"costs","limit":20}
            if rounds==2:args["cursor"]=json.loads(payload["messages"][-1]["content"])["data"]["next_cursor"]
            return frame(calls=[call("list_cases",json.dumps(args),"id"+str(rounds))]) if rounds<3 else frame("26 blocked; ID 10 eligible, human review pending.")
        with patch.object(GroqClient,"_request",side_effect=model):
            report=run_groq_agent(self.snapshot,"Cost evidence question",client=GroqClient(free_quota=quota(),allow_synthetic_transfer=True))
        self.assertTrue(report["complete"])
        refs=[r["reference"] for r in report["answer_references"]]
        self.assertEqual(len(refs),28)
        self.assertEqual(sum(r["document"]=="evidence" for r in refs),27)
        self.assertEqual(sum(r["document"]=="dossier" for r in refs),1)
        for ref in refs:self.snapshot.resolve_reference(ref)
        self.assertEqual(len({r["id"] for r in report["answer_references"]}),28)

    def test_partial_answer_keeps_pending_pages_despite_valid_sources(self):
        report,_=self.run_frames([frame(calls=[call("list_cases",'{"limit":1}')]),frame("Partial inventory.")])
        self.assertFalse(report["complete"])
        self.assertEqual(report["dispatcher"]["pending_pages"],1)
        self.assertTrue(report["references_verified"])
        self.assertEqual(report["status"],"partial")

    def test_missing_review_withholds_answer_and_sources(self):
        report,_=self.run_frames([frame(calls=[call("review_status",'{"inventory_id":10}')]),frame("Cannot claim costs.")],
                                 snapshot=EvidenceSnapshot(self.evidence))
        self.assertFalse(report["complete"])
        self.assertIsNone(report["answer"])
        self.assertEqual(report["answer_references"],[])
        self.assertFalse(report["references_verified"])

    def test_accepted_data_without_sources_cannot_be_presented_as_complete(self):
        original=EvidenceSnapshot.query
        def without_refs(snapshot,tool,arguments=None):
            result=original(snapshot,tool,arguments)
            result["references"]=[]
            result["trace"]["result_sha256"]=fingerprint({k:v for k,v in result.items() if k!="trace"})
            return result
        with patch.object(EvidenceSnapshot,"query",autospec=True,side_effect=without_refs):
            report,_=self.run_frames()
        self.assertFalse(report["complete"])
        self.assertIsNone(report["answer"])
        self.assertEqual(report["error"]["code"],"answer_references_invalid")
        self.assertEqual(report["answer_references"],[])

    def test_reference_collection_rejects_foreign_hashes(self):
        result=self.snapshot.query("summarize_run")
        result["references"][0]["sha256"]="0"*64
        with self.assertRaises(EvidenceError):
            answer_references(self.snapshot,{"trace":[{"outcome":{"result":result}}]})

    def test_failure_still_records_configuration_tokens_times_and_sanitized_error(self):
        report,_=self.run_frames([GroqError("groq_rate_limited")])
        self.assertFalse(report["complete"])
        self.assertEqual(report["model_rounds"],1)
        self.assertEqual(report["model_events"][0]["status"],"error")
        self.assertIn("elapsed_ms",report["model_events"][0])
        self.assertEqual(report["configuration_sha256"],fingerprint(report["configuration"]))
        self.assertEqual(report["answer_references"],[])
        self.assertEqual(report["error"]["code"],"groq_rate_limited")

    def test_skill_does_not_grant_forbidden_tools_or_persist_private_reasoning(self):
        first=frame(calls=[call("shell",'{}')])
        first["choices"][0]["message"]["reasoning"]="PRIVATE_THINKING_MARKER"
        report,_=self.run_frames([first])
        self.assertFalse(report["complete"])
        self.assertEqual(report["dispatcher"]["attempts"],1)
        self.assertEqual(report["dispatcher"]["executions"],0)
        self.assertNotIn("PRIVATE_THINKING_MARKER",json.dumps(report))
        self.assertEqual(set(report["dispatcher"]["allowed_tools"]),{c["name"] for c in tool_contracts()})

    def test_configuration_and_sources_are_sealed_by_existing_report_fingerprint(self):
        report,_=self.run_frames()
        self.assertEqual(report["report_sha256"],fingerprint({k:v for k,v in report.items() if k!="report_sha256"}))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"review.json"
            write_agent_report(path,report)
            with self.assertRaises(FileExistsError):write_agent_report(path,report)
            report["configuration"]["version"]="tampered"
            with self.assertRaises(ValueError):write_agent_report(Path(folder)/"tampered.json",report)


if __name__=="__main__":unittest.main()
