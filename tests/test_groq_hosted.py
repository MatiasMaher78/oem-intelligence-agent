"""Offline Groq wire simulations; no API key, service, or new business fixtures."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone, timedelta
import json
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from oem_agent.groq_hosted import MODEL, FreeQuota, GroqClient, GroqError, LocalBudget
from oem_agent.ollama_local import OllamaLimits, encode
from oem_agent.stage5_agent import run_groq_agent, write_agent_report
from oem_agent.stage5_evidence import EvidenceSnapshot
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import execute_stage2_statistics
from oem_agent.stage4_review import create_dossier


def quota(**changes):
    values = dict(checked_at=datetime.now(timezone.utc).isoformat(), requests_per_minute=30,
                  requests_per_day=1000, tokens_per_minute=8000, tokens_per_day=200000,
                  remaining_requests_day=1000, remaining_tokens_day=200000)
    return FreeQuota(**(values | changes))


def call(name="summarize_run", arguments="{}", call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def frame(content="Respuesta.", calls=None, **changes):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    value = {"model": MODEL, "object": "chat.completion", "choices": [{"index": 0, "message": message,
             "finish_reason": "tool_calls" if calls else "stop"}],
             "usage": {"prompt_tokens": 200, "completion_tokens": 20, "total_tokens": 220,
                       "prompt_time": 0.1, "completion_time": 0.2, "total_time": 0.3}}
    return value | changes


class FakeAPI:
    def __init__(self, frames=()):
        self.frames = iter(frames)
        self.requests = []
        self.models = [{"id": MODEL, "active": True, "context_window": 131072}]

    def __call__(self, method, path, payload, deadline):
        self.requests.append((method, path, deepcopy(payload)))
        if path == "/openai/v1/models":
            return {"data": deepcopy(self.models)}
        if path == "/openai/v1/chat/completions":
            value = next(self.frames)
            if isinstance(value, BaseException):
                raise value
            return deepcopy(value)
        raise AssertionError("Unexpected endpoint")


class GroqAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        db = Path(cls.folder.name)/"fresh.sqlite"
        seed_statistics_demo(db)
        cls.evidence = execute_stage2_statistics(db)
        cls.dossier = create_dossier(cls.evidence, dossier_id="TEST-GROQ", at="2026-11-05T12:00:00+00:00")
        cls.snapshot = EvidenceSnapshot(cls.evidence, cls.dossier)

    def run_frames(self, frames=(), *, api=None, client=None, snapshot=None):
        api = api or FakeAPI(frames)
        client = client or GroqClient(free_quota=quota(), allow_synthetic_transfer=True)
        with patch.object(GroqClient, "_request", side_effect=api):
            report = run_groq_agent(snapshot or self.snapshot, "Resumen de evidencia sintética", client=client)
        return report, api

    def test_native_selection_uses_same_dispatcher_and_five_contracts(self):
        report, api = self.run_frames([frame(calls=[call()]), frame("Hay 27 piezas sintéticas.")])
        self.assertTrue(report["complete"])
        self.assertEqual((report["model_rounds"], report["native_tool_attempts"]), (2, 1))
        self.assertEqual(report["provider"], "groq")
        self.assertFalse(report["answer_verified"])
        self.assertEqual(report["dispatcher"]["trace"][0]["tool"], "summarize_run")
        requests = [p for _, path, p in api.requests if path.endswith("completions")]
        self.assertEqual(len(requests[0]["tools"]), 5)
        self.assertEqual(requests[0]["reasoning_effort"], "none")
        self.assertEqual(requests[0]["reasoning_format"], "hidden")
        self.assertEqual(requests[0]["service_tier"], "on_demand")
        self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "call_1")
        self.assertIsInstance(requests[1]["messages"][-2]["tool_calls"][0]["function"]["arguments"], str)
        self.assertEqual(report["model_events"][0]["metrics"]["generation_tokens_per_second"], 100)

    def test_same_question_does_not_route_to_a_fixed_tool(self):
        a, _ = self.run_frames([frame(calls=[call()]), frame("Resumen.")])
        b, _ = self.run_frames([frame(calls=[call("inspect_case", '{"inventory_id":10}')]), frame("Caso 10.")])
        self.assertNotEqual(a["dispatcher"]["trace"][0]["tool"], b["dispatcher"]["trace"][0]["tool"])

    def test_no_transfer_without_explicit_authorization(self):
        client = GroqClient(free_quota=quota())
        report, api = self.run_frames(client=client)
        self.assertFalse(report["complete"])
        self.assertEqual(report["error"]["code"], "synthetic_transfer_not_authorized")
        self.assertFalse(any(path.endswith("completions") for _, path, _ in api.requests))

    def test_missing_free_confirmation_prevents_even_preflight(self):
        report, api = self.run_frames(client=GroqClient(allow_synthetic_transfer=True))
        self.assertEqual(report["error"]["code"], "free_quota_confirmation_required")
        self.assertEqual(api.requests, [])

    def test_exhausted_operator_quota_prevents_chat(self):
        for q in (quota(remaining_requests_day=0), quota(remaining_tokens_day=1)):
            report, api = self.run_frames(client=GroqClient(free_quota=q, allow_synthetic_transfer=True))
            self.assertFalse(report["complete"])
            self.assertEqual(report["error"]["code"], "free_quota_exhausted")
            self.assertFalse(any(path.endswith("completions") for _, path, _ in api.requests))

    def test_unavailable_inactive_and_small_context_model_never_fallback(self):
        for models in ([], [{"id": MODEL,"active":False,"context_window":131072}],
                       [{"id":MODEL,"active":True,"context_window":1024}]):
            api = FakeAPI(); api.models = models
            report, api = self.run_frames(api=api)
            self.assertFalse(report["complete"])
            self.assertFalse(any(path.endswith("completions") for _, path, _ in api.requests))

    def test_forbidden_calls_are_counted_and_never_execute(self):
        for name in ("shell", "sql", "approve_review", "update_inventory"):
            report, api = self.run_frames([frame(calls=[call(name)])])
            self.assertEqual(report["dispatcher"]["attempts"], 1)
            self.assertEqual(report["dispatcher"]["executions"], 0)
            self.assertFalse(report["complete"])
            self.assertEqual(sum(path.endswith("completions") for _, path, _ in api.requests), 1)

    def test_malformed_arguments_and_calls_are_counted_without_logging_raw_text(self):
        for native in (call(arguments="PRIVATE-MALFORMED"), call(arguments='{"a":1,"a":2}'),
                       call(arguments='{"a":NaN}'), call(arguments='[]'), call(arguments={}), None,
                       call(call_id="bad id")):
            report, _ = self.run_frames([frame(calls=[native])])
            self.assertEqual(report["dispatcher"]["attempts"], 1)
            self.assertFalse(report["complete"])
            self.assertNotIn("PRIVATE-MALFORMED", json.dumps(report))

    def test_bad_schema_arguments_remain_sticky_after_a_valid_read(self):
        report, _ = self.run_frames([frame(calls=[call(arguments='{"unexpected":1}')]),
                                     frame(calls=[call()]), frame("No afirmar éxito.")])
        self.assertFalse(report["complete"])
        self.assertEqual(report["dispatcher"]["errors"], 1)

    def test_repeated_calls_stop_and_count(self):
        report, _ = self.run_frames([frame(calls=[call()]), frame(calls=[call()])])
        self.assertEqual(report["dispatcher"]["attempts"], 2)
        self.assertEqual(report["dispatcher"]["executions"], 1)
        self.assertFalse(report["complete"])

    def test_missing_review_and_unfinished_page_stay_partial(self):
        for snapshot, native in ((EvidenceSnapshot(self.evidence), call("review_status", '{"inventory_id":10}')),
                                 (self.snapshot, call("list_cases", '{"limit":1}'))):
            report, _ = self.run_frames([frame(calls=[native]), frame("Parcial.")], snapshot=snapshot)
            self.assertFalse(report["complete"])

    def test_private_reasoning_and_intermediate_content_are_discarded(self):
        value = frame("PRIVATE-INTERMEDIATE", calls=[call()])
        value["choices"][0]["message"]["reasoning"] = "PRIVATE-REASONING"
        report, api = self.run_frames([value, frame("Resumen.")])
        text = json.dumps(report)+json.dumps(api.requests)
        self.assertNotIn("PRIVATE-REASONING", text)
        self.assertNotIn("PRIVATE-INTERMEDIATE", text)
        self.assertTrue(report["model_events"][0]["thinking_omitted"])

    def test_native_pagination_returns_the_issued_cursor_without_changing_reader(self):
        api=FakeAPI();base=api.__call__;rounds=0
        def model(method,path,payload,deadline):
            nonlocal rounds
            if not path.endswith("completions"):return base(method,path,payload,deadline)
            api.requests.append((method,path,deepcopy(payload)))
            rounds+=1
            arguments={"evidence_status":"insufficient_sample","limit":1}
            if rounds==2:
                arguments["cursor"]=json.loads(payload["messages"][-1]["content"])["data"]["next_cursor"]
            return frame(calls=[call("list_cases",json.dumps(arguments),f"call_{rounds}")]) if rounds<3 else frame("Dos piezas con muestra insuficiente.")
        client=GroqClient(free_quota=quota(),allow_synthetic_transfer=True)
        with patch.object(GroqClient,"_request",side_effect=model):
            report=run_groq_agent(self.snapshot,"Listar piezas con muestra insuficiente",client=client)
        self.assertTrue(report["complete"])
        self.assertEqual(report["dispatcher"]["attempts"],2)
        self.assertEqual(report["dispatcher"]["pending_pages"],0)

    def test_duplicate_native_ids_are_counted_and_cancellation_keeps_audit(self):
        report,_=self.run_frames([frame(calls=[call(),call("inspect_case",'{"inventory_id":10}')])])
        self.assertEqual(report["dispatcher"]["attempts"],2)
        self.assertFalse(report["complete"])
        with self.assertRaises(KeyboardInterrupt) as error:
            self.run_frames([KeyboardInterrupt("PRIVATE")])
        self.assertFalse(error.exception.agent_report["complete"])
        self.assertNotIn("PRIVATE",json.dumps(error.exception.agent_report))

    def test_truncation_wrong_model_and_invalid_usage_withhold_final(self):
        values = [frame(model="paid/other"), frame(usage={"prompt_tokens":4090,"completion_tokens":20,"total_tokens":4110})]
        length = frame(); length["choices"][0]["finish_reason"] = "length"; values.append(length)
        private = frame("<think>PRIVATE</think>"); values.append(private)
        missing = frame(); missing["usage"].pop("total_tokens"); values.append(missing)
        for value in values:
            report, _ = self.run_frames([value])
            self.assertFalse(report["complete"])
            self.assertIsNone(report["answer"])

    def test_no_tool_evidence_cannot_ground_a_complete_answer(self):
        report, _ = self.run_frames([frame("Hay 28 piezas.")])
        self.assertFalse(report["complete"])
        self.assertIsNone(report["answer"])

    def test_model_call_budget_stays_unchanged(self):
        client = GroqClient(OllamaLimits(max_requests=1),free_quota=quota(),allow_synthetic_transfer=True)
        report, _ = self.run_frames([frame(calls=[call()])], client=client)
        self.assertEqual(report["error"]["code"], "model_request_budget_exhausted")
        self.assertFalse(report["complete"])

    def test_provider_errors_never_leak_native_exception_or_retry(self):
        report, api = self.run_frames([GroqError("groq_rate_limited")])
        self.assertEqual(report["error"]["code"], "groq_rate_limited")
        self.assertEqual(report["model_rounds"],1)
        self.assertIsNone(report["answer"])
        report, _ = self.run_frames([RuntimeError("SECRET-ERROR")])
        self.assertNotIn("SECRET-ERROR",json.dumps(report))

    def test_exclusive_report_and_snapshot_isolation(self):
        before=self.snapshot.evidence
        report,_=self.run_frames([frame(calls=[call()]),frame("Resumen.")])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"groq.json"
            write_agent_report(path,report)
            with self.assertRaises(FileExistsError):write_agent_report(path,report)
        self.assertEqual(self.snapshot.evidence,before)


    def test_unknown_remaining_native_cycle_preserves_null_and_separate_local_budget(self):
        client=GroqClient(free_quota=quota(remaining_requests_day=None,remaining_tokens_day=None),
                          allow_synthetic_transfer=True,local_budget=LocalBudget(3,8000))
        report,_=self.run_frames([frame(calls=[call()]),frame("Resumen.")],client=client)
        self.assertTrue(report["complete"])
        self.assertIsNone(report["model_info"]["operator_confirmed_free_quota"]["remaining_requests_day"])
        self.assertIsNone(report["model_info"]["operator_confirmed_free_quota"]["remaining_tokens_day"])
        self.assertEqual(report["model_info"]["local_batch_budget"]["limits"],{"max_requests":3,"max_tokens":8000})
        self.assertEqual(client.budget_status["used_requests"],2)
        self.assertEqual(client.budget_status["charged_tokens"],440)

    def test_unknown_remaining_stops_on_local_call_or_token_budget(self):
        for budget,frames,expected_posts in ((LocalBudget(1,8000),[frame(calls=[call()]),frame("Should not be requested")],1),
                                             (LocalBudget(3,1),[],0)):
            client=GroqClient(free_quota=quota(remaining_requests_day=None,remaining_tokens_day=None),
                              allow_synthetic_transfer=True,local_budget=budget)
            report,api=self.run_frames(frames,client=client)
            self.assertFalse(report["complete"])
            self.assertIsNone(report["answer"])
            self.assertEqual(report["error"]["code"],"local_budget_exhausted")
            self.assertEqual(sum(path.endswith("completions") for _,path,_ in api.requests),expected_posts)

    def test_unknown_remaining_provider_error_retains_charge_and_has_no_retry(self):
        client=GroqClient(free_quota=quota(remaining_requests_day=None,remaining_tokens_day=None),allow_synthetic_transfer=True)
        report,api=self.run_frames([GroqError("groq_rate_limited")],client=client)
        self.assertFalse(report["complete"])
        self.assertEqual(report["error"]["code"],"groq_rate_limited")
        self.assertEqual(client.budget_status["used_requests"],1)
        self.assertGreater(client.budget_status["charged_tokens"],220)
        self.assertEqual(sum(path.endswith("completions") for _,path,_ in api.requests),1)

    def test_local_budget_spans_multiple_questions(self):
        client=GroqClient(free_quota=quota(remaining_requests_day=None,remaining_tokens_day=None),
                          allow_synthetic_transfer=True,local_budget=LocalBudget(3,12000))
        first,_=self.run_frames([frame(calls=[call()]),frame("Uno.")],client=client)
        second,api=self.run_frames([frame(calls=[call()]),frame("Withheld")],client=client)
        self.assertTrue(first["complete"])
        self.assertFalse(second["complete"])
        self.assertEqual(second["error"]["code"],"local_budget_exhausted")
        self.assertEqual(client.budget_status["used_requests"],3)
        self.assertEqual(sum(path.endswith("completions") for _,path,_ in api.requests),1)


class GroqTransportTests(unittest.TestCase):
    def test_https_fixed_host_tls_and_secret_never_in_events(self):
        response=MagicMock(status=200)
        response.getheader.return_value=None
        response.read1.side_effect=[encode({"data":[]}),b""]
        connection=MagicMock();connection.getresponse.return_value=response
        with patch.dict("os.environ",{"GROQ_API_KEY":"gsk-TEST-SECRET","HTTPS_PROXY":"http://evil","GROQ_BASE_URL":"https://evil"}), \
             patch("oem_agent.groq_hosted.http.client.HTTPSConnection",return_value=connection) as constructor:
            client=GroqClient()
            client._request("GET","/openai/v1/models",None,9999999999)
        self.assertEqual(constructor.call_args.args,("api.groq.com",443))
        self.assertTrue(constructor.call_args.kwargs["context"].check_hostname)
        self.assertNotIn("gsk-TEST-SECRET",json.dumps(client.events))
        connection.close.assert_called_once()

    def test_missing_key_contacts_no_host_and_never_prints_values(self):
        with patch.dict("os.environ",{},clear=True),patch("oem_agent.groq_hosted.http.client.HTTPSConnection") as constructor:
            client=GroqClient()
            with self.assertRaises(GroqError) as error:client.availability(9999999999)
        self.assertEqual(error.exception.code,"groq_api_key_missing")
        constructor.assert_not_called()

    def test_redirect_auth_quota_and_server_errors_do_not_read_bodies_or_retry(self):
        for status in (302,401,403,404,429,500):
            response=MagicMock(status=status);response.getheader.return_value=None
            connection=MagicMock();connection.getresponse.return_value=response
            with patch.dict("os.environ",{"GROQ_API_KEY":"SECRET"}),patch("oem_agent.groq_hosted.http.client.HTTPSConnection",return_value=connection) as constructor:
                client=GroqClient()
                with self.assertRaises(GroqError):client._request("GET","/openai/v1/models",None,9999999999)
            response.read1.assert_not_called()
            self.assertEqual(constructor.call_count,1)
            self.assertNotIn("SECRET",json.dumps(client.events))

    def test_nonallowlisted_endpoints_and_unauthorized_posts_do_not_connect(self):
        client=GroqClient()
        with patch("oem_agent.groq_hosted.http.client.HTTPSConnection") as constructor:
            for path in ("https://evil","/billing","/openai/v1/files","/api/pull"):
                with self.assertRaises(GroqError):client._request("POST",path,{},9999999999)
            with self.assertRaises(GroqError):client._request("POST","/openai/v1/chat/completions",{},9999999999)
        constructor.assert_not_called()

    def test_timeout_cancels_and_closes_without_private_exception(self):
        connection=MagicMock();connection.getresponse.side_effect=socket.timeout("SECRET")
        with patch.dict("os.environ",{"GROQ_API_KEY":"SECRET"}),patch("oem_agent.groq_hosted.http.client.HTTPSConnection",return_value=connection),patch("oem_agent.groq_hosted.Timer") as timer:
            client=GroqClient()
            with self.assertRaises(GroqError):client._request("GET","/openai/v1/models",None,9999999999)
        connection.close.assert_called_once()
        timer.return_value.cancel.assert_called_once()
        self.assertEqual(client.events[-1]["error"],"request_timeout")
        self.assertNotIn("SECRET",json.dumps(client.events))

    def test_wire_size_invalid_json_and_quota_headers_fail_closed(self):
        for content,header in ((b"x"*65537,None),(b'{"data":[],"data":[]}',None),(b"{}","SECRET")):
            response=MagicMock(status=200);response.getheader.return_value=header
            response.read1.side_effect=[content,b""]
            connection=MagicMock();connection.getresponse.return_value=response
            with patch.dict("os.environ",{"GROQ_API_KEY":"SECRET"}),patch("oem_agent.groq_hosted.http.client.HTTPSConnection",return_value=connection):
                client=GroqClient()
                with self.assertRaises(GroqError):client._request("GET","/openai/v1/models",None,9999999999)
            self.assertNotIn("SECRET",json.dumps(client.events))

    def test_free_confirmation_is_frozen_recent_bounded_and_free_only(self):
        q=quota()
        with self.assertRaises(FrozenInstanceError):q.plan="developer"
        for changes in ({"plan":"developer"},{"tokens_per_minute":8001},{"remaining_tokens_day":-1},{"requests_per_minute":True}):
            with self.assertRaises(ValueError):quota(**changes)
        for instant in ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),"2026-11-05T12:00:00"):
            with self.assertRaises(GroqError):quota(checked_at=instant)

    def test_rate_reservations_persist_across_questions_and_remote_zero_blocks(self):
        client=GroqClient(free_quota=quota())
        client._reserve(5000)
        with self.assertRaises(GroqError):client._reserve(3001)
        client=GroqClient(free_quota=quota())
        client._remote_remaining_requests=0
        with self.assertRaises(GroqError):client._reserve(1)

    def test_context_overflow_does_not_send_any_request_or_prune(self):
        client=GroqClient(free_quota=quota(),allow_synthetic_transfer=True)
        with patch.object(client,"_request") as request,self.assertRaises(GroqError):
            client.chat([{"role":"user","content":"x"*20000}],[],9999999999)
        request.assert_not_called()

    def test_metadata_without_rate_headers_does_not_erase_last_observed_zero(self):
        response=MagicMock(status=200);response.getheader.return_value=None
        response.read1.side_effect=[encode({"data":[]}),b""]
        connection=MagicMock();connection.getresponse.return_value=response
        client=GroqClient(free_quota=quota());client._remote_remaining_tokens=0
        with patch.dict("os.environ",{"GROQ_API_KEY":"SECRET"}),patch("oem_agent.groq_hosted.http.client.HTTPSConnection",return_value=connection):
            client._request("GET","/openai/v1/models",None,9999999999)
        with self.assertRaises(GroqError):client._reserve(1)


    def test_nullable_remaining_accepts_only_null_or_bounded_integers(self):
        for changes in ({"remaining_requests_day":None},{"remaining_tokens_day":None},
                        {"remaining_requests_day":None,"remaining_tokens_day":None}):
            value=quota(**changes)
            for name,expected in changes.items():self.assertIsNone(getattr(value,name))
        for field in ("remaining_requests_day","remaining_tokens_day"):
            for value in ("unknown",False,1.0,-1,200001):
                with self.assertRaises(ValueError):quota(**{field:value})

    def test_local_budget_is_frozen_bounded_and_distinct_from_free_quota(self):
        budget=LocalBudget()
        with self.assertRaises(FrozenInstanceError):budget.max_requests=10
        for values in ((0,1),(10,1),(True,1),(1,0),(1,24001),(1,1.5)):
            with self.assertRaises(ValueError):LocalBudget(*values)
        with self.assertRaises(TypeError):GroqClient(local_budget={"max_requests":1})

    def test_unknown_remaining_keeps_known_daily_minute_and_remote_limits(self):
        cases=((quota(requests_per_day=1,remaining_requests_day=None,remaining_tokens_day=None),{},1,1),
               (quota(tokens_per_day=1,remaining_requests_day=None,remaining_tokens_day=None),{},1,1),
               (quota(remaining_requests_day=None,remaining_tokens_day=None),{},5000,3001),
               (quota(remaining_requests_day=None,remaining_tokens_day=None),{"_remote_remaining_requests":0},0,1),
               (quota(remaining_requests_day=None,remaining_tokens_day=None),{"_remote_remaining_tokens":0},0,1),
               (quota(remaining_requests_day=1,remaining_tokens_day=None),{},1,1),
               (quota(remaining_requests_day=None,remaining_tokens_day=1),{},1,1))
        for value,state,first,second in cases:
            client=GroqClient(free_quota=value)
            for name,item in state.items():setattr(client,name,item)
            if first:client._reserve(first)
            with self.assertRaises(GroqError) as error:client._reserve(second)
            self.assertEqual(error.exception.code,"free_quota_exhausted")

    def test_unexpected_model_specific_limits_stop_before_body_or_inference_result(self):
        for header,value in (("x-ratelimit-limit-requests","999"),("x-ratelimit-limit-requests","1001"),
                             ("x-ratelimit-limit-tokens","7999"),("x-ratelimit-limit-tokens","8001")):
            response=MagicMock(status=200)
            response.getheader.side_effect=lambda name,h=header,v=value:v if name==h else None
            connection=MagicMock();connection.getresponse.return_value=response
            client=GroqClient(free_quota=quota(remaining_requests_day=None,remaining_tokens_day=None),allow_synthetic_transfer=True)
            with patch.dict("os.environ",{"GROQ_API_KEY":"OFFLINE_TEST_TOKEN"}),patch("oem_agent.groq_hosted.http.client.HTTPSConnection",return_value=connection):
                with self.assertRaises(GroqError) as error:client._request("POST","/openai/v1/chat/completions",{},9999999999)
            self.assertEqual(error.exception.code,"unexpected_account_limits")
            response.read1.assert_not_called()
            connection.close.assert_called_once()


class GroqSmokeTests(unittest.TestCase):
    def setUp(self):
        # Protocol tests must never load the operator's real local credentials.
        loader = patch("examples.stage5_3_groq_smoke.load_local_env", return_value={
            "dotenv_present": False, "api_key_available": False, "model_supported": True})
        loader.start()
        self.addCleanup(loader.stop)

    def test_default_cli_is_metadata_only(self):
        from examples.stage5_3_groq_smoke import main
        with patch.object(GroqClient,"availability",return_value={"active":True}),patch.object(GroqClient,"chat") as chat,patch("sys.stdout",new_callable=io.StringIO) as output:
            self.assertEqual(main([]),0)
        self.assertEqual(json.loads(output.getvalue())["inferences"],0)
        chat.assert_not_called()

    def test_transfer_flag_without_quota_or_inputs_cannot_infer(self):
        from examples.stage5_3_groq_smoke import main
        with patch.object(GroqClient,"_request") as request,patch("sys.stdout",new_callable=io.StringIO):
            self.assertEqual(main(["--allow-synthetic-transfer"]),1)
        request.assert_not_called()

    def test_more_than_three_questions_is_rejected_before_network(self):
        from examples.stage5_3_groq_smoke import main
        with patch.object(GroqClient,"_request") as request,patch("sys.stderr",new_callable=io.StringIO),self.assertRaises(SystemExit):
            main(["--questions","4"])
        request.assert_not_called()


if __name__=="__main__":unittest.main()
