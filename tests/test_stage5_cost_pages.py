"""Compact, fully paged cost evidence and measured-prefix quota regressions, offline only."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from oem_agent.groq_hosted import FreeQuota, GroqClient, GroqError, LocalBudget, MODEL
from oem_agent.ollama_local import encode
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import execute_stage2_statistics, StatisticsConfig
from oem_agent.stage4_review import create_dossier, record_decision
from oem_agent.stage5_agent import run_groq_agent, _tool_view, SYSTEM_PROMPT
from oem_agent.stage5_contracts import tool_contracts
from oem_agent.stage5_dispatcher import ReadOnlyDispatcher
from oem_agent.stage5_evidence import EvidenceSnapshot


def quota():
    return FreeQuota(datetime.now(timezone.utc).isoformat(),30,1000,8000,200000,None,None)


def frame(content="", calls=None, prompt=1000, output=20):
    return {"model":MODEL,"object":"chat.completion","choices":[{"index":0,
            "finish_reason":"tool_calls" if calls else "stop",
            "message":{"role":"assistant","content":content,"tool_calls":calls or []}}],
            "usage":{"prompt_tokens":prompt,"completion_tokens":output,"total_tokens":prompt+output}}


def call(arguments, identifier):
    return {"id":identifier,"type":"function","function":{"name":"list_cases","arguments":json.dumps(arguments)}}


class CostPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=tempfile.TemporaryDirectory();cls.addClassCleanup(folder.cleanup)
        cls.db=Path(folder.name)/"fresh.sqlite"
        seed_statistics_demo(cls.db)
        cls.evidence=execute_stage2_statistics(cls.db)
        cls.dossier=create_dossier(cls.evidence,dossier_id="COST-PAGES-TEST",at="2026-11-05T12:00:00+00:00")
        cls.snapshot=EvidenceSnapshot(cls.evidence,cls.dossier)

    def pages(self,snapshot=None):
        dispatcher=ReadOnlyDispatcher(snapshot or self.snapshot)
        one=dispatcher.dispatch("list_cases",{"view":"costs","limit":20})
        self.assertIsNone(one["error"])
        two=dispatcher.dispatch("list_cases",{"view":"costs","limit":20,"cursor":one["result"]["data"]["next_cursor"]})
        self.assertIsNone(two["error"])
        return (one,two),dispatcher.finish()

    def test_two_pages_preserve_all_27_ids_and_26_blocks_from_existing_review(self):
        pages,report=self.pages()
        rows=[row for page in pages for row in page["result"]["data"]["items"]]
        self.assertEqual([len(page["result"]["data"]["items"]) for page in pages],[20,7])
        self.assertEqual(len({r["inventory_id"] for r in rows}),27)
        self.assertEqual(sum(not r["eligible"] for r in rows),26)
        self.assertTrue(report["complete"])
        self.assertEqual(report["pending_pages"],0)
        for row in rows:
            old=self.snapshot.query("review_status",{"inventory_id":row["inventory_id"]})["data"]["case"]
            self.assertEqual({k:row[k] for k in ("eligible","block_reasons","review_state")},
                             {k:old[k] for k in ("eligible","block_reasons","review_state")})
        for page in pages:
            self.assertLess(len(encode(page["result"])),8192)
            for ref in page["result"]["references"]:self.snapshot.resolve_reference(ref)
        self.assertEqual([r["inventory_id"] for r in rows if r["eligible"]],[10])
        self.assertTrue(all(r["review_state"]=="pending" for r in rows))

    def test_compact_projection_round_trips_every_row_and_reference(self):
        pages,_=self.pages()
        for page in pages:
            view=_tool_view(page)
            rebuilt={identifier:{"inventory_id":identifier,**{k:g[k] for k in ("eligible","block_reasons","review_state")}}
                     for g in view["data"]["groups"] for identifier in g["inventory_ids"]}
            self.assertEqual(rebuilt,{r["inventory_id"]:r for r in page["result"]["data"]["items"]})
            refs=[{"document":group["document"],"sha256":group["sha256"],"pointer":pointer}
                  for group in view["references"] for pointer in group["pointers"]]
            self.assertEqual(refs,page["result"]["references"])
            self.assertEqual(view["data"]["returned_rows"],len(rebuilt))
            self.assertEqual(view["data"]["next_cursor"],page["result"]["data"]["next_cursor"])
            self.assertLess(len(encode(view)),len(encode(page["result"])))

    def test_existing_inventory_view_and_other_four_contracts_are_preserved(self):
        result=self.snapshot.query("list_cases",{"limit":5})
        self.assertEqual(result["data"]["scope"],"selected_inventory")
        self.assertIn("inventory",result["data"]["items"][0])
        self.assertEqual(set(result["data"]["items"][0]),{"inventory","evidence_status","matching_complete","has_confirmed_match"})
        self.assertEqual(_tool_view(ReadOnlyDispatcher(self.snapshot).dispatch("list_cases",{"limit":5}))["data"],result["data"])
        self.assertEqual({c["name"] for c in tool_contracts()},
                         {"summarize_run","list_cases","inspect_case","list_offers","review_status"})
        self.assertIn("hasta 20",SYSTEM_PROMPT)
        large=ReadOnlyDispatcher(self.snapshot).dispatch("list_cases",{"limit":20})
        self.assertEqual(large["error"]["code"],"response_too_large")

    def test_oem_filter_and_cursor_cannot_cross_views_or_filters(self):
        first=self.snapshot.query("list_cases",{"view":"costs","limit":20})
        cursor=first["data"]["next_cursor"]
        for arguments in ({"cursor":cursor},{"view":"inventory","cursor":cursor},
                          {"view":"costs","cursor":cursor,"evidence_status":"insufficient_sample"}):
            self.assertEqual(self.snapshot.query("list_cases",arguments)["error"]["code"],"invalid_cursor")
        oem=self.evidence["results"][1]["inventory"]["oem"]
        only=self.snapshot.query("list_cases",{"view":"costs","oem":oem})
        self.assertEqual([r["inventory_id"] for r in only["data"]["items"]],[10])
        empty=self.snapshot.query("list_cases",{"view":"costs","oem":oem+"X"})
        self.assertEqual(empty["data"]["total_rows"],0)
        self.assertFalse(empty["warnings"])

    def test_missing_dossier_is_unavailable_not_empty_success(self):
        dispatcher=ReadOnlyDispatcher(EvidenceSnapshot(self.evidence))
        result=dispatcher.dispatch("list_cases",{"view":"costs"})
        self.assertIsNone(result["error"])
        self.assertEqual(result["result"]["status"],"not_available")
        self.assertIsNone(result["result"]["data"])
        self.assertIn("review_not_provided",result["result"]["warnings"])
        self.assertFalse(dispatcher.finish()["complete"])
        self.assertTrue(dispatcher.report()["missing_requested_evidence"])

    def test_changed_evidence_uses_current_eligibility_and_invalidates_old_approval(self):
        approved=record_decision(self.dossier,self.evidence,inventory_id=10,state="approved",reviewer="TEST",reason="Synthetic test")
        changed=execute_stage2_statistics(self.db,config=StatisticsConfig(min_sample_size=11))
        snapshot=EvidenceSnapshot(changed,approved)
        result=snapshot.query("list_cases",{"view":"costs","limit":20})
        row=next(r for r in result["data"]["items"] if r["inventory_id"]==10)
        self.assertTrue(result["data"]["needs_refresh"])
        self.assertIn("review_evidence_changed",result["warnings"])
        self.assertFalse(row["eligible"])
        self.assertNotEqual(row["review_state"],"approved")
        expected=snapshot.query("review_status",{"inventory_id":10})["data"]["case"]
        self.assertEqual(row["block_reasons"],expected["block_reasons"])

    def test_partial_origin_and_malformed_view_never_finish_complete(self):
        with patch("oem_agent.stage3_statistics._list_source_offers",side_effect=sqlite3.OperationalError("Synthetic")):
            evidence=execute_stage2_statistics(self.db)
        pages,report=self.pages(EvidenceSnapshot(evidence,self.dossier))
        self.assertFalse(report["complete"])
        self.assertFalse(pages[0]["result"]["coverage"]["complete"])
        dispatcher=ReadOnlyDispatcher(self.snapshot)
        result=dispatcher.dispatch("list_cases",{"view":"shell"})
        self.assertEqual(result["error"]["code"],"invalid_arguments")
        self.assertEqual(dispatcher.report()["attempts"],1)
        self.assertEqual(dispatcher.report()["executions"],0)

    def run_native_pages(self,early=False,error=False):
        requests=[];rounds=0
        def api(method,path,payload,deadline):
            nonlocal rounds
            requests.append((method,path,deepcopy(payload)))
            if path.endswith("models"):return {"data":[{"id":MODEL,"active":True,"context_window":131072}]}
            rounds+=1
            if error and rounds==2:raise GroqError("groq_rate_limited")
            if rounds==1:return frame(calls=[call({"view":"costs","limit":20},"cost1")])
            if rounds==2 and not early:
                cursor=json.loads(payload["messages"][-1]["content"])["data"]["next_cursor"]
                return frame(calls=[call({"view":"costs","limit":20,"cursor":cursor},"cost2")],prompt=1600,output=35)
            return frame("26 blocked DEMO cases, all selected except 10; evidence and filtered prices are missing. Human reviews pending.",prompt=2000,output=120)
        client=GroqClient(free_quota=quota(),allow_synthetic_transfer=True,local_budget=LocalBudget(4,8000))
        with patch.object(GroqClient,"_request",side_effect=api):
            report=run_groq_agent(self.snapshot,"Which cases are blocked for DEMO costs and why?",client=client)
        return report,requests,client

    def test_native_model_pages_20_and_7_then_answers_within_free_limits(self):
        report,requests,client=self.run_native_pages()
        self.assertTrue(report["complete"])
        self.assertEqual(report["native_tool_attempts"],2)
        self.assertEqual(report["dispatcher"]["pending_pages"],0)
        self.assertEqual([len(e["outcome"]["result"]["data"]["items"]) for e in report["dispatcher"]["trace"]],[20,7])
        posts=[payload for _,path,payload in requests if path.endswith("completions")]
        self.assertEqual(len(posts),3)
        self.assertTrue(all(p["tool_choice"]=="auto" and len(p["tools"])==5 for p in posts))
        self.assertEqual(client.budget_status["reported_tokens"],4775)
        self.assertEqual(client.budget_status["estimated_unreported_tokens"],0)
        self.assertEqual(report["model_events"][1]["metrics"]["reservation_basis"],"reported_prompt_plus_new_bytes")
        self.assertLess(sum(e["metrics"]["reported_total_tokens"] for e in report["model_events"][:2])+
                        report["model_events"][2]["metrics"]["reserved_tokens"],8000)

    def test_model_finishing_before_last_page_stays_partial(self):
        report,_,_=self.run_native_pages(early=True)
        self.assertFalse(report["complete"])
        self.assertEqual(report["status"],"partial")
        self.assertEqual(report["dispatcher"]["pending_pages"],1)

    def test_provider_error_stops_without_retry_or_complete_answer(self):
        report,requests,client=self.run_native_pages(error=True)
        self.assertFalse(report["complete"])
        self.assertIsNone(report["answer"])
        self.assertEqual(report["error"]["code"],"groq_rate_limited")
        self.assertEqual(sum(path.endswith("completions") for _,path,_ in requests),2)
        self.assertEqual(client.budget_status["reported_tokens"],1020)
        self.assertGreater(client.budget_status["estimated_unreported_tokens"],0)


class PromptAccountingTests(unittest.TestCase):
    def client(self):return GroqClient(free_quota=quota(),allow_synthetic_transfer=True)

    def test_measured_prefix_is_used_only_when_messages_and_tools_match(self):
        client=self.client()
        messages=[{"role":"user","content":"x"*1000}]
        estimate,basis,state=client._estimate_prompt(messages,[])
        self.assertEqual(basis,"full_bytes_estimate")
        client._prompt_basis={**state,"prompt_tokens":200}
        extended=messages+[{"role":"assistant","content":"x"*500}]
        value,basis,_=client._estimate_prompt(extended,[])
        self.assertEqual(basis,"reported_prompt_plus_new_bytes")
        self.assertEqual(value,200+len(encode({"messages":extended,"tools":[]}))-state["bytes"])
        self.assertLess(value,len(encode({"messages":extended,"tools":[]})))
        for changed,tools in (([{"role":"user","content":"other"}],[]),(extended,[{"different":"schema"}])):
            value,basis,_=client._estimate_prompt(changed,tools)
            self.assertEqual(basis,"full_bytes_estimate")
            self.assertEqual(value,len(encode({"messages":changed,"tools":tools})))

    def test_reported_usage_replaces_estimate_even_on_valid_usage_error(self):
        client=self.client()
        def api(method,path,payload,deadline):
            if path.endswith("models"):return {"data":[{"id":MODEL,"active":True,"context_window":131072}]}
            return frame("",prompt=2000,output=20)
        with patch.object(GroqClient,"_request",side_effect=api),self.assertRaises(GroqError) as error:
            client.chat([{"role":"user","content":"short"}],[],9999999999)
        self.assertEqual(error.exception.code,"quota_estimate_exceeded")
        self.assertEqual(client.budget_status["reported_tokens"],2020)
        self.assertEqual(client.budget_status["charged_tokens"],2020)
        self.assertEqual(client.budget_status["estimated_unreported_tokens"],0)

    def test_length_failure_retains_real_usage_not_bytes_as_consumption(self):
        client=self.client()
        def api(method,path,payload,deadline):
            if path.endswith("models"):return {"data":[{"id":MODEL,"active":True,"context_window":131072}]}
            value=frame("",prompt=200,output=20)
            value["choices"][0]["finish_reason"]="length"
            return value
        with patch.object(GroqClient,"_request",side_effect=api),self.assertRaises(GroqError) as error:
            client.chat([{"role":"user","content":"short"}],[],9999999999)
        self.assertEqual(error.exception.code,"generation_incomplete")
        self.assertEqual(client.budget_status["reported_tokens"],220)
        self.assertEqual(client.budget_status["charged_tokens"],220)


if __name__=="__main__":unittest.main()
