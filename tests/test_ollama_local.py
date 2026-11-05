"""Native-protocol simulations only. The default suite never requires a model or service."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from oem_agent.ollama_local import ENDPOINT, MODEL, OllamaError, OllamaLimits, OllamaLocalClient, decode
from oem_agent.stage3_fixtures import seed_statistics_demo
from oem_agent.stage3_statistics import execute_stage2_statistics
from oem_agent.stage4_review import create_dossier, fingerprint
from oem_agent.stage5_agent import run_local_agent, write_agent_report
from oem_agent.stage5_evidence import EvidenceSnapshot


def native(name, arguments, **extras):
    return {"function": {"name": name, "arguments": arguments}, **extras}


def frame(content="", calls=None, **extras):
    value = {"model": MODEL, "done": True, "done_reason": "stop", "prompt_eval_count": 1000, "eval_count": 20,
             "message": {"role": "assistant", "content": content}}
    if calls is not None:
        value["message"]["tool_calls"] = calls
    value.update(extras)
    return value


class FakeAPI:
    """HTTP endpoint fixture, not a business-question or tool-selection router."""
    def __init__(self, frames=()):
        self.frames = iter(frames)
        self.requests = []
        self.cloud = True
        self.version = "0.40.14"
        self.models = [{"name": MODEL, "digest": "a" * 64, "size": 2500000000}]
        self.show = {"capabilities": ["completion", "tools", "thinking"],
                     "details": {"family": "qwen3", "quantization_level": "Q4_K_M"},
                     "model_info": {"qwen3.context_length": 40960}}

    def __call__(self, method, path, payload, deadline):
        self.requests.append((method, path, deepcopy(payload)))
        if path == "/api/version": return {"version": self.version}
        if path == "/api/status": return {"cloud": {"disabled": self.cloud}}
        if path == "/api/tags": return {"models": deepcopy(self.models)}
        if path == "/api/show": return deepcopy(self.show)
        if path == "/api/ps": return {"models": [{"name": MODEL, "size": 3000000000, "size_vram": 0, "context_length": 4096}]}
        if path == "/api/chat":
            value = next(self.frames)
            if isinstance(value, BaseException): raise value
            return deepcopy(value)
        raise AssertionError("Unexpected endpoint")


class AgentProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.db = Path(cls.folder.name) / "fresh.sqlite"
        seed_statistics_demo(cls.db)
        cls.evidence = execute_stage2_statistics(cls.db)
        cls.dossier = create_dossier(cls.evidence, dossier_id="TEST-53", at="2026-11-04T12:00:00+00:00")
        cls.snapshot = EvidenceSnapshot(cls.evidence, cls.dossier)

    def run_frames(self, frames=(), *, api=None, question="¿Cuántas piezas fueron seleccionadas?", limits=None, snapshot=None):
        api = api or FakeAPI(frames)
        with patch.object(OllamaLocalClient, "_request", side_effect=api):
            report = run_local_agent(snapshot or self.snapshot, question, limits=limits)
        return report, api

    def test_native_model_choice_is_dispatched_and_tool_result_returns_to_the_model(self):
        report, api = self.run_frames([frame(calls=[native("summarize_run", {}, id="call-1")]), frame("Hay 27 piezas sintéticas.")])
        self.assertTrue(report["complete"])
        self.assertEqual((report["model_rounds"], report["native_tool_attempts"]), (2, 1))
        self.assertEqual(report["dispatcher"]["trace"][0]["tool"], "summarize_run")
        self.assertEqual(report["model_events"][0]["dispatcher_attempts"], [1])
        requests = [payload for _, path, payload in api.requests if path == "/api/chat"]
        second = requests[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-1]["tool_name"], "summarize_run")
        self.assertEqual(second[-1]["tool_call_id"], "call-1")
        self.assertEqual(json.loads(second[-1]["content"])["coverage"]["total_candidates"], 27)
        self.assertEqual(len(requests[0]["tools"]), 5)
        self.assertFalse(requests[0]["think"])
        self.assertFalse(requests[0]["truncate"])
        self.assertFalse(requests[0]["shift"])
        self.assertEqual(requests[0]["options"]["num_ctx"], 4096)
        self.assertEqual(requests[0]["options"]["num_gpu"], 0)

    def test_same_question_can_receive_different_model_selected_tools_without_python_routing(self):
        first, _ = self.run_frames([frame(calls=[native("summarize_run", {})]), frame("Resumen.")], question="Consulta")
        second, _ = self.run_frames([frame(calls=[native("inspect_case", {"inventory_id": 10})]), frame("Caso 10.")], question="Consulta")
        self.assertEqual(first["dispatcher"]["trace"][0]["tool"], "summarize_run")
        self.assertEqual(second["dispatcher"]["trace"][0]["tool"], "inspect_case")

    def test_text_that_looks_like_a_tool_call_is_never_executed(self):
        report, _ = self.run_frames([frame('{"tool":"summarize_run","arguments":{}}')])
        self.assertEqual(report["dispatcher"]["attempts"], 0)
        self.assertEqual(report["status"], "abstained")
        self.assertIsNone(report["answer"])

    def test_reasoning_fields_and_intermediate_content_are_never_persisted_or_forwarded(self):
        first = frame("PRIVATE-INTERMEDIATE", calls=[native("summarize_run", {})])
        first["message"]["thinking"] = "PRIVATE-THINKING"
        report, api = self.run_frames([first, frame("Resumen consultivo.")])
        self.assertNotIn("PRIVATE-", json.dumps(report))
        outbound = [payload for _, path, payload in api.requests if path == "/api/chat"]
        self.assertNotIn("PRIVATE-", json.dumps(outbound))
        self.assertTrue(report["model_events"][0]["thinking_omitted"])
        self.assertFalse(report["answer_verified"])

    def test_private_tags_in_final_content_are_withheld_without_logging_them(self):
        report, _ = self.run_frames([frame(calls=[native("summarize_run", {})]), frame("<think>PRIVATE-REASONING</think>Resultado")])
        self.assertFalse(report["complete"])
        self.assertIsNone(report["answer"])
        self.assertNotIn("PRIVATE-REASONING", json.dumps(report))
        self.assertEqual(report["error"]["code"], "private_reasoning_in_content")

    def test_forbidden_native_tool_is_counted_and_stops_without_a_followup_model_request(self):
        report, api = self.run_frames([frame(calls=[native("record_decision", {"inventory_id": 10, "state": "approved"})])])
        self.assertEqual(report["dispatcher"]["attempts"], 1)
        self.assertEqual(report["dispatcher"]["executions"], 0)
        self.assertEqual(report["dispatcher"]["stop_reason"], "tool_forbidden")
        self.assertEqual(sum(path == "/api/chat" for _, path, _ in api.requests), 1)
        self.assertFalse(report["complete"])

    def test_bad_arguments_and_later_success_remain_partial(self):
        report, _ = self.run_frames([frame(calls=[native("inspect_case", {"inventory_id": False})]),
                                     frame(calls=[native("inspect_case", {"inventory_id": 10})]), frame("Dato del caso 10.")])
        self.assertEqual(report["dispatcher"]["attempts"], 2)
        self.assertEqual(report["dispatcher"]["errors"], 1)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["complete"])

    def test_two_errors_stop_and_repeated_calls_are_blocked_by_existing_dispatcher(self):
        for calls, code in (([native("inspect_case", {}), native("inspect_case", {"inventory_id": False})], "error_budget_exhausted"),
                            ([native("summarize_run", {}), native("summarize_run", {})], "repeated_call")):
            report, _ = self.run_frames([frame(calls=calls)])
            self.assertEqual(report["dispatcher"]["attempts"], 2)
            self.assertEqual(report["dispatcher"]["stop_reason"], code)
            self.assertFalse(report["complete"])

    def test_malformed_native_calls_are_counted_before_protocol_rejection(self):
        for call in ({}, native("inspect_case", '{"inventory_id":10}'), {"function": None}):
            report, _ = self.run_frames([frame(calls=[call])])
            self.assertEqual(report["dispatcher"]["attempts"], 1)
            self.assertEqual(report["dispatcher"]["executions"], 0)
            self.assertFalse(report["complete"])

    def test_oversized_native_batch_is_observed_and_rejected_before_any_execution(self):
        report, _ = self.run_frames([frame(calls=[native("summarize_run", {}) for _ in range(9)])])
        self.assertEqual(report["native_tool_attempts"], 9)
        self.assertEqual(report["native_calls_rejected_before_dispatch"], 9)
        self.assertEqual(report["dispatcher"]["executions"], 0)
        self.assertFalse(report["complete"])

    def test_batches_count_all_entries_even_after_the_dispatcher_stops(self):
        report, _ = self.run_frames([frame(calls=[native("shell", {}), native("inspect_case", {"inventory_id": 10})])])
        self.assertEqual(report["native_tool_attempts"], 2)
        self.assertEqual(report["dispatcher"]["attempts"], 2)
        self.assertEqual(report["dispatcher"]["executions"], 0)

    def test_cloud_must_be_confirmed_disabled_and_no_fallback_is_attempted(self):
        api = FakeAPI(); api.cloud = False
        report, api = self.run_frames(api=api)
        self.assertEqual(report["error"]["code"], "cloud_not_disabled")
        self.assertFalse(any(path == "/api/chat" for _, path, _ in api.requests))
        self.assertEqual(report["model_rounds"], 0)
        self.assertFalse(report["complete"])

    def test_missing_or_remote_model_and_missing_tool_capability_block_preflight(self):
        apis = []
        missing = FakeAPI(); missing.models = []; apis.append(missing)
        remote = FakeAPI(); remote.models[0]["remote_host"] = "https://example.invalid"; apis.append(remote)
        unsupported = FakeAPI(); unsupported.show["capabilities"] = ["completion"]; apis.append(unsupported)
        for api in apis:
            report, api = self.run_frames(api=api)
            self.assertFalse(any(path == "/api/chat" for _, path, _ in api.requests))
            self.assertFalse(report["complete"])
            self.assertIsNone(report["model_info"])

    def test_old_server_cannot_silently_ignore_context_flags(self):
        api = FakeAPI(); api.version = "0.34.1"
        report, api = self.run_frames(api=api)
        self.assertEqual(report["error"]["code"], "unsupported_ollama_version")
        self.assertEqual(len(api.requests), 1)

    def test_cloud_status_and_model_digest_are_rechecked_before_each_inference(self):
        api = FakeAPI([frame(calls=[native("summarize_run", {})]), frame("Not used")])
        original = api.__call__
        chats = 0
        def changed(method, path, payload, deadline):
            nonlocal chats
            result = original(method, path, payload, deadline)
            if path == "/api/chat":
                chats += 1
                api.models[0]["digest"] = "b" * 64
            return result
        with patch.object(OllamaLocalClient, "_request", side_effect=changed):
            report = run_local_agent(self.snapshot, "Resumen")
        self.assertEqual(chats, 1)
        self.assertEqual(report["error"]["code"], "model_changed")
        self.assertFalse(report["complete"])

    def test_servers_below_the_reviewed_runtime_are_rejected_before_model_lookup(self):
        for version in ("0.34.2", "0.35.0", "0.40.13"):
            with self.subTest(version=version):
                api = FakeAPI()
                api.version = version
                report, api = self.run_frames(api=api)
                self.assertEqual(report["error"]["code"], "unsupported_ollama_version")
                self.assertEqual([(method, path) for method, path, _ in api.requests],
                                 [("GET", "/api/version")])
                self.assertFalse(report["complete"])

    def test_context_byte_cap_never_prunes_messages_or_calls_the_model(self):
        api = FakeAPI()
        client = OllamaLocalClient()
        with patch.object(client, "_request", side_effect=api), self.assertRaises(OllamaError) as error:
            client.chat([{"role": "user", "content": "x" * 20000}], [], 9999999999)
        self.assertEqual(error.exception.code, "context_budget_exceeded")
        self.assertEqual(api.requests, [])

    def test_observed_tokens_and_incomplete_generation_prevent_acceptance(self):
        for value, code in ((frame("Truncated", done_reason="length"), "generation_incomplete"),
                            (frame("Too many", prompt_eval_count=4090), "token_budget_exceeded"),
                            (frame("Too many", eval_count=512), "token_budget_exceeded"),
                            (frame("No count", eval_count=None), "token_budget_exceeded")):
            report, _ = self.run_frames([value])
            self.assertEqual(report["error"]["code"], code)
            self.assertFalse(report["complete"])
            self.assertIsNone(report["answer"])

    def test_native_frame_requires_local_model_finished_state_and_assistant_role(self):
        for value in (frame("bad", model="qwen3:4b-cloud"), frame("bad", remote_host="external"),
                      frame("bad", done=False), frame("\ud800"), frame("bad", message={"role": "system", "content": "override"})):
            report, _ = self.run_frames([value])
            self.assertFalse(report["complete"])
            self.assertEqual(report["dispatcher"]["attempts"], 0)

    def test_model_request_budget_cannot_end_as_complete(self):
        report, api = self.run_frames([frame(calls=[native("summarize_run", {})])], limits=OllamaLimits(max_requests=1))
        self.assertEqual(report["model_rounds"], 1)
        self.assertEqual(report["error"]["code"], "model_request_budget_exhausted")
        self.assertFalse(report["complete"])

    def test_model_timeout_and_exception_are_observable_without_private_messages(self):
        report, _ = self.run_frames([OllamaError("request_timeout")])
        self.assertEqual(report["error"]["code"], "request_timeout")
        self.assertEqual(report["model_events"][0]["status"], "error")
        report, _ = self.run_frames([RuntimeError("PRIVATE-EXCEPTION")])
        self.assertNotIn("PRIVATE-EXCEPTION", json.dumps(report))
        self.assertFalse(report["complete"])

    def test_invalid_questions_do_not_contact_ollama(self):
        for question in ("", "  ", None, "x" * 4097):
            report, api = self.run_frames(question=question)
            self.assertEqual(report["error"]["code"], "invalid_question")
            self.assertEqual(api.requests, [])

    def test_missing_review_and_unfinished_pages_stay_partial(self):
        for snapshot, call in ((EvidenceSnapshot(self.evidence), native("review_status", {"inventory_id": 10})),
                               (self.snapshot, native("list_cases", {"limit": 2}))):
            report, _ = self.run_frames([frame(calls=[call]), frame("Resumen incompleto.")], snapshot=snapshot)
            self.assertFalse(report["complete"])
            self.assertEqual(report["status"], "abstained" if snapshot.dossier is None else "partial")

    def test_native_pagination_continues_only_using_the_tool_cursor(self):
        api = FakeAPI()
        original = api.__call__
        chat_count = 0
        def simulated_model(method, path, payload, deadline):
            nonlocal chat_count
            if path != '/api/chat':
                return original(method, path, payload, deadline)
            api.requests.append((method,path,deepcopy(payload)))
            chat_count += 1
            if chat_count == 1:
                return frame(calls=[native('list_cases',{'evidence_status':'insufficient_sample','limit':1})])
            if chat_count == 2:
                cursor = json.loads(payload['messages'][-1]['content'])['data']['next_cursor']
                return frame(calls=[native('list_cases',{'evidence_status':'insufficient_sample','limit':1,'cursor':cursor})])
            return frame('Las dos piezas con muestra insuficiente conservan precios filtrados ausentes.')
        with patch.object(OllamaLocalClient,'_request',side_effect=simulated_model):
            report = run_local_agent(self.snapshot,'¿Qué piezas tienen muestra insuficiente?')
        self.assertTrue(report['complete'])
        self.assertEqual(report['dispatcher']['attempts'],2)
        self.assertEqual(report['dispatcher']['pending_pages'],0)

    def test_failed_read_alone_is_not_grounding_for_a_final_business_answer(self):
        report, _ = self.run_frames([frame(calls=[native('inspect_case',{'inventory_id':999})]),frame('Una respuesta sin evidencia.')])
        self.assertFalse(report['complete'])
        self.assertIsNone(report['answer'])
        self.assertEqual(report['status'],'error')

    def test_cancellation_propagates_with_sanitized_observable_audit(self):
        api = FakeAPI([KeyboardInterrupt()])
        with patch.object(OllamaLocalClient,'_request',side_effect=api), self.assertRaises(KeyboardInterrupt) as interrupted:
            run_local_agent(self.snapshot,'Resumen')
        report = interrupted.exception.agent_report
        self.assertFalse(report['complete'])
        self.assertIsNone(report['answer'])
        self.assertEqual(report['model_events'][0]['status'],'error')

    def test_source_files_math_and_human_decisions_remain_unchanged(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        evidence, review = deepcopy(self.evidence), deepcopy(self.dossier)
        report, _ = self.run_frames([frame(calls=[native("inspect_case", {"inventory_id": 10})]), frame("Bandas DEMO.")])
        self.assertTrue(report["complete"])
        stats = report["dispatcher"]["trace"][0]["outcome"]["result"]["data"]["content"]["statistics"]
        self.assertEqual((stats["observed_units"], stats["filtered_units"]), (55, 44))
        self.assertEqual((stats["filtered_min_price_cents"], stats["filtered_max_price_cents"]), (2000, 9000))
        self.assertEqual(self.evidence, evidence)
        self.assertEqual(self.dossier, review)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_agent_audit_hash_and_exclusive_report_creation(self):
        report, _ = self.run_frames([frame(calls=[native("summarize_run", {})]), frame("Resumen.")])
        self.assertEqual(report["report_sha256"], fingerprint({k: v for k, v in report.items() if k != "report_sha256"}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            write_agent_report(path, report)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError): write_agent_report(path, report)
            self.assertEqual(path.read_bytes(), before)
            report["complete"] = False
            with self.assertRaises(ValueError): write_agent_report(Path(folder)/"other.json", report)


class TransportTests(unittest.TestCase):
    def test_strict_json_rejects_duplicates_nonfinite_values_and_nonobjects(self):
        for content in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'\xff', b'{"x":1e999}',
                        b'{"x":"\\ud800"}', '{"x":1}'.encode('utf-16')):
            with self.assertRaises(OllamaError): decode(content)

    def test_transport_uses_only_literal_loopback_and_ignores_environment_routing(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read1.side_effect = [b'{"version":"0.40.14"}', b'']
        with patch('oem_agent.ollama_local.http.client.HTTPConnection', return_value=connection) as factory, \
             patch.dict('os.environ', {'HTTP_PROXY':'http://external.invalid','OLLAMA_HOST':'https://external.invalid'}):
            client = OllamaLocalClient()
            result = client._request('GET','/api/version',None,9999999999)
        self.assertEqual(result['version'],'0.40.14')
        self.assertEqual(factory.call_args.args, ('127.0.0.1',11434))
        connection.close.assert_called_once()
        self.assertNotIn('body',client.events[0])

    def test_redirects_and_http_errors_are_not_followed_or_logged_with_bodies(self):
        for status in (302,401,500):
            connection = MagicMock(); connection.getresponse.return_value.status = status
            with patch('oem_agent.ollama_local.http.client.HTTPConnection',return_value=connection):
                client=OllamaLocalClient()
                with self.assertRaises(OllamaError): client._request('GET','/api/status',None,9999999999)
            connection.getresponse.return_value.read1.assert_not_called()
            self.assertEqual(client.events[0]['http_status'],status)

    def test_oversized_wire_response_is_rejected_without_persisting_body(self):
        connection=MagicMock(); response=connection.getresponse.return_value; response.status=200
        response.read1.side_effect=[b'x'*4096 for _ in range(17)]
        with patch('oem_agent.ollama_local.http.client.HTTPConnection',return_value=connection):
            client=OllamaLocalClient()
            with self.assertRaises(OllamaError) as error: client._request('GET','/api/status',None,9999999999)
        self.assertEqual(error.exception.code,'ollama_response_too_large')
        self.assertNotIn('body',json.dumps(client.events))

    def test_timeout_and_watchdog_close_resources_without_real_waiting(self):
        connection=MagicMock(); connection.getresponse.side_effect=TimeoutError('PRIVATE-DETAIL')
        with patch('oem_agent.ollama_local.http.client.HTTPConnection',return_value=connection), \
             patch('oem_agent.ollama_local.Timer') as timer:
            client=OllamaLocalClient()
            with self.assertRaises(OllamaError) as error: client._request('GET','/api/status',None,9999999999)
        self.assertEqual(error.exception.code,'request_timeout')
        timer.return_value.cancel.assert_called_once()
        callback=timer.call_args.args[1]; callback(connection.sock)
        connection.sock.shutdown.assert_called_with(socket.SHUT_RDWR)
        connection.close.assert_called_once()
        self.assertNotIn('PRIVATE-DETAIL',json.dumps(client.events))

    def test_unavailable_server_never_falls_back_or_activates_a_runtime(self):
        with patch('oem_agent.ollama_local.http.client.HTTPConnection',side_effect=ConnectionRefusedError) as factory:
            client=OllamaLocalClient()
            with self.assertRaises(OllamaError) as error: client.preflight(9999999999)
        self.assertEqual(error.exception.code,'ollama_unavailable')
        self.assertEqual(factory.call_count,1)
        self.assertEqual(client.events[0]['path'],'/api/version')

    def test_endpoints_for_installation_download_and_external_urls_are_forbidden(self):
        client=OllamaLocalClient()
        for path in ('/api/pull','/api/push','/api/create','/api/delete','https://example.invalid/api/chat'):
            with self.assertRaises(OllamaError): client._request('POST',path,{},9999999999)

    def test_limits_are_frozen_and_cannot_expand_the_plan_budgets(self):
        for values in ({'max_requests':10},{'context_tokens':8193},{'output_tokens':1025},
                       {'request_seconds':46},{'session_seconds':181},{'question_bytes':4097},{'max_requests':True}):
            with self.assertRaises(ValueError): OllamaLimits(**values)
        limits=OllamaLimits()
        with self.assertRaises(FrozenInstanceError): limits.context_tokens=99999
        self.assertEqual(ENDPOINT,'http://127.0.0.1:11434')


if __name__ == '__main__':
    unittest.main()
