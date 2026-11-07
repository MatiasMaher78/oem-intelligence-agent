"""Lossless model projections and pre-validation quota observations; entirely offline."""
from copy import deepcopy
import json
from time import monotonic
import unittest
from unittest.mock import patch

from test_groq_hosted import FakeAPI, frame, call, quota
from oem_agent.groq_hosted import GroqClient, GroqError, LocalBudget, FreeQuota
from oem_agent.ollama_local import encode
from oem_agent.stage4_review import fingerprint
from oem_agent.stage5_agent import _tool_view, run_groq_agent
from oem_agent.stage5_dispatcher import ReadOnlyDispatcher
from oem_agent.stage5_evidence import EvidenceSnapshot
from oem_agent.stage5_eval_data import build_dataset


class MinimalContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = build_dataset()

    def run_frames(self, frames, *, client=None, snapshot=None, question='Synthetic evidence question'):
        client = client or GroqClient(free_quota=quota(), allow_synthetic_transfer=True)
        api = FakeAPI(frames)
        with patch.object(GroqClient, '_request', side_effect=api):
            report = run_groq_agent(snapshot or self.snapshots['E1'], question, client=client)
        return report, api, client

    def test_all_five_tools_preserve_data_ids_references_coverage_errors_and_warnings(self):
        for key, snapshot in self.snapshots.items():
            identifier = snapshot.evidence['results'][0]['inventory']['id']
            queries = [('summarize_run', {}), ('list_cases', {'limit':1}),
                       ('list_offers', {'inventory_id':identifier,'limit':2}),
                       ('review_status', {'inventory_id':identifier})]
            queries += [('inspect_case', {'inventory_id':identifier,'section':section})
                        for section in ('inventory','statistics','matching','trace')]
            for name, arguments in queries:
                with self.subTest(case=key,tool=name,arguments=arguments):
                    outcome = ReadOnlyDispatcher(snapshot).dispatch(name,arguments)
                    before = fingerprint(outcome)
                    view = _tool_view(outcome);result = outcome['result']
                    self.assertEqual(view['data'], result['data'])
                    self.assertEqual(view['references'], result['references'])
                    self.assertEqual(view['coverage'], result['coverage'])
                    self.assertEqual(view['error'], outcome['error'])
                    self.assertEqual(view['warnings'], result['warnings'])
                    self.assertEqual(view['status'], outcome['status'])
                    self.assertEqual(view['calls_remaining'], outcome['session']['calls_remaining'])
                    self.assertNotIn('session',view)
                    self.assertNotIn('evidence_sha256',view)
                    self.assertEqual(fingerprint(outcome),before)
                    for ref in view['references']:snapshot.resolve_reference(ref)

    def test_existing_cost_groups_round_trip_every_row_and_source(self):
        for snapshot in self.snapshots.values():
            outcome = ReadOnlyDispatcher(snapshot).dispatch('list_cases',{'view':'costs','limit':20})
            before = fingerprint(outcome);view = _tool_view(outcome)
            rows = [{'inventory_id':identifier, **{key:group[key] for key in ('eligible','block_reasons','review_state')}}
                    for group in view['data']['groups'] for identifier in group['inventory_ids']]
            self.assertEqual(rows,outcome['result']['data']['items'])
            refs = [{'document':group['document'],'sha256':group['sha256'],'pointer':pointer}
                    for group in view['references'] for pointer in group['pointers']]
            self.assertEqual(refs,outcome['result']['references'])
            for ref in refs:snapshot.resolve_reference(ref)
            self.assertEqual(fingerprint(outcome),before)

    def test_unavailable_review_preserves_source_identity_null_data_and_warning(self):
        snapshot = EvidenceSnapshot(self.snapshots['E1'].evidence)
        outcome = ReadOnlyDispatcher(snapshot).dispatch('review_status',{'inventory_id':1001})
        view = _tool_view(outcome)
        self.assertIsNone(view['data']);self.assertEqual(view['references'],[])
        self.assertEqual(view['evidence_sha256'],snapshot.evidence_sha256)
        self.assertIn('review_not_provided',view['warnings'])
        self.assertEqual(view['status'],'partial')

    def test_rejected_call_keeps_error_and_identity_without_any_reader_result(self):
        outcome = ReadOnlyDispatcher(self.snapshots['E1']).dispatch('shell',{})
        view = _tool_view(outcome)
        self.assertEqual(view['error'],outcome['error'])
        self.assertEqual(view['error']['code'],'tool_forbidden')
        self.assertEqual(view['evidence_sha256'],outcome['evidence_sha256'])
        self.assertEqual(view['status'],'error');self.assertIsNone(view['data'])

    def test_foreign_document_or_mismatched_hash_never_removes_evidence_identity(self):
        original = ReadOnlyDispatcher(self.snapshots['E1']).dispatch('summarize_run',{})
        for change in ({'document':'dossier'},{'sha256':'0'*64}):
            outcome = deepcopy(original);outcome['result']['references'][0].update(change)
            self.assertEqual(_tool_view(outcome)['evidence_sha256'],outcome['evidence_sha256'])

    def test_zero_null_literal_oem_and_partial_source_survive_compaction(self):
        for key in ('E1','E2','E4','E5'):
            snap = self.snapshots[key];identifier = snap.evidence['results'][0]['inventory']['id']
            outcome = ReadOnlyDispatcher(snap).dispatch('inspect_case',{'inventory_id':identifier,'section':'statistics'})
            view = _tool_view(outcome)
            self.assertEqual(view['data'],outcome['result']['data'])
            if key in ('E2','E4'):
                self.assertEqual(view['data']['content']['statistics']['observed_units'],0)
                self.assertIsNone(view['data']['content']['statistics']['observed_min_price_cents'])
            if key=='E5':
                self.assertFalse(view['coverage']['complete']);self.assertEqual(view['status'],'partial')
        outcome = ReadOnlyDispatcher(self.snapshots['E1']).dispatch('inspect_case',{'inventory_id':1001,'section':'inventory'})
        self.assertEqual(_tool_view(outcome)['data']['content']['oem'],'0000421001')

    def test_first_emission_is_compact_and_full_audit_session_is_retained(self):
        report,api,_ = self.run_frames([frame(calls=[call('inspect_case','{"inventory_id":1001}')]),frame('Synthetic answer.')])
        posts = [p for _,path,p in api.requests if path.endswith('completions')]
        view = json.loads(posts[1]['messages'][-1]['content'])
        event = report['dispatcher']['trace'][0]
        self.assertNotIn('session',view);self.assertNotIn('evidence_sha256',view)
        self.assertIn('session',event['outcome'])
        self.assertEqual(view['data'],event['outcome']['result']['data'])
        self.assertEqual(view['references'],event['outcome']['result']['references'])
        self.assertTrue(report['complete'])

    def test_append_only_messages_keep_measured_prefix_and_do_not_receive_reservation_traces(self):
        report,api,client = self.run_frames([
            frame(calls=[call('inspect_case','{"inventory_id":1001,"section":"statistics"}','r1')]),
            frame(calls=[call('inspect_case','{"inventory_id":1001,"section":"inventory"}','r2')]),frame('Synthetic answer.')])
        posts = [p for _,path,p in api.requests if path.endswith('completions')]
        self.assertEqual(len(posts),3)
        for older,newer in zip(posts,posts[1:]):
            self.assertEqual(older['messages'],newer['messages'][:len(older['messages'])])
        self.assertTrue(all(event['reservation_basis']=='reported_prompt_plus_new_bytes' for event in client.reservation_events[1:]))
        self.assertTrue(all(len(post['tools'])==5 and post['tool_choice']=='auto' for post in posts))
        self.assertNotIn('reservation_events',json.dumps(posts))
        self.assertNotIn('local_batch_budget',json.dumps(posts))
        self.assertEqual(report['reservation_events'],client.reservation_events)
        self.assertTrue(report['complete'])

    def test_missing_page_and_sticky_error_are_not_hidden_by_small_control_metadata(self):
        report,_,_ = self.run_frames([frame(calls=[call('list_offers','{"inventory_id":1001,"limit":1}')]),frame('Partial.')])
        self.assertFalse(report['complete']);self.assertEqual(report['dispatcher']['pending_pages'],1)
        report,_,_ = self.run_frames([frame(calls=[call(arguments='{"invalid":1}')]),frame(calls=[call()]),frame('Partial.')])
        self.assertFalse(report['complete']);self.assertEqual(report['dispatcher']['errors'],1)

    def test_reservation_log_is_captured_in_partial_agent_report(self):
        client = GroqClient(free_quota=quota(),allow_synthetic_transfer=True,local_budget=LocalBudget(1,8000))
        report,api,client = self.run_frames([frame(calls=[call()])],client=client)
        self.assertFalse(report['complete']);self.assertIsNone(report['answer'])
        self.assertEqual(report['error']['code'],'local_budget_exhausted')
        self.assertEqual(len(report['reservation_events']),2)
        self.assertEqual(report['reservation_events'][-1]['status'],'blocked')
        self.assertEqual(report['reservation_events'][-1]['stage'],'preflight')
        self.assertEqual(report['reservation_events'][-1]['blocked_by'],['local_requests'])
        self.assertEqual(sum(path.endswith('completions') for _,path,_ in api.requests),1)


class ReservationObservationTests(unittest.TestCase):
    def client(self):
        return GroqClient(free_quota=quota(remaining_requests_day=None,remaining_tokens_day=None),allow_synthetic_transfer=True)

    def test_record_precedes_quota_validation_even_when_confirmation_fails(self):
        client = self.client();seen=[]
        def check():
            seen.append(client.reservation_events[0])
            raise GroqError('free_quota_confirmation_stale')
        with patch.object(FreeQuota,'check_fresh',side_effect=check),self.assertRaises(GroqError):
            client._reserve(1000)
        self.assertEqual(seen[0]['status'],'checking')
        self.assertEqual(seen[0]['reserved_tokens'],1000)
        self.assertEqual(client.reservation_events[0]['status'],'blocked')
        self.assertEqual(client.reservation_events[0]['error'],'free_quota_confirmation_stale')
        self.assertEqual(client.budget_status['used_requests'],0)

    def test_missing_confirmation_is_recorded_without_inventing_free_caps(self):
        client = GroqClient()
        with self.assertRaises(GroqError):client._reserve(1)
        event = client.reservation_events[0]
        self.assertEqual(event['blocked_by'],['free_quota_confirmation_required'])
        self.assertIsNone(event['before']['free_daily']['tokens_cap'])
        self.assertIsNone(event['estimated_prompt_tokens'])

    def test_exact_failed_minute_and_remote_limits_are_recorded_without_charging(self):
        client=self.client();now=monotonic()
        client._window=[(now,1312),(now,2510)]
        client._used_requests=2;client._used_tokens=client._reported_tokens=3822
        client._remote_remaining_tokens=4302
        with self.assertRaises(GroqError):client._reserve(4641)
        event=client.reservation_events[0]
        self.assertEqual(event['reserved_tokens'],4641)
        self.assertEqual(event['before']['local_batch_budget']['reported_tokens'],3822)
        self.assertEqual(event['before']['minute_window']['charged_tokens']+event['reserved_tokens'],8463)
        self.assertEqual(set(event['blocked_by']),{'tokens_per_minute','remote_tokens_remaining'})
        self.assertEqual(client.budget_status['charged_tokens'],3822)
        self.assertEqual(client.budget_status['used_requests'],2)

    def test_each_local_daily_minute_and_remote_guard_retains_original_error_codes(self):
        cases=[({}, {'_used_requests':9},1,'local_budget_exhausted','local_requests'),
               ({}, {'_used_tokens':24000},1,'local_budget_exhausted','local_tokens'),
               ({'requests_per_day':1}, {'_used_requests':1},1,'free_quota_exhausted','daily_requests_cap'),
               ({'tokens_per_day':1}, {'_used_tokens':1},1,'free_quota_exhausted','daily_tokens_cap'),
               ({'remaining_requests_day':0},{},1,'free_quota_exhausted','daily_requests_remaining'),
               ({'remaining_tokens_day':0},{},1,'free_quota_exhausted','daily_tokens_remaining'),
               ({}, {'_remote_remaining_requests':0},1,'free_quota_exhausted','remote_requests_remaining'),
               ({}, {'_remote_remaining_tokens':0},1,'free_quota_exhausted','remote_tokens_remaining')]
        for changes,state,amount,code,reason in cases:
            client=GroqClient(free_quota=quota(**({'remaining_requests_day':None,'remaining_tokens_day':None}|changes)))
            for name,value in state.items():setattr(client,name,value)
            before=client.budget_status
            with self.subTest(reason=reason),self.assertRaises(GroqError) as error:client._reserve(amount)
            self.assertEqual(error.exception.code,code)
            self.assertIn(reason,client.reservation_events[0]['blocked_by'])
            self.assertEqual(before,client.budget_status)

    def test_requests_per_minute_guard_is_unchanged(self):
        client=GroqClient(free_quota=quota(requests_per_minute=1))
        client._reserve(1)
        with self.assertRaises(GroqError):client._reserve(1)
        self.assertEqual(client.reservation_events[-1]['blocked_by'],['requests_per_minute'])
        self.assertEqual(client.budget_status['used_requests'],1)

    def test_exact_8000_boundary_is_accepted_and_next_token_is_blocked(self):
        client=self.client();client._reserve(8000)
        self.assertEqual(client.reservation_events[0]['status'],'allowed')
        self.assertEqual(client.reservation_events[0]['before']['minute_window']['tokens_limit'],8000)
        with self.assertRaises(GroqError):client._reserve(1)
        self.assertEqual(client.reservation_events[-1]['blocked_by'],['tokens_per_minute'])
        self.assertEqual(client.budget_status['charged_tokens'],8000)

    def test_expired_window_is_logged_without_resetting_batch_consumption(self):
        client=self.client();client._window=[(0,8000)]
        client._used_tokens=client._reported_tokens=8000;client._used_requests=1
        with patch('oem_agent.groq_hosted.monotonic',return_value=100):client._reserve(1000)
        event=client.reservation_events[0]
        self.assertEqual(event['before']['minute_window']['charged_tokens'],0)
        self.assertEqual(event['before']['local_batch_budget']['reported_tokens'],8000)
        self.assertEqual(client.budget_status['charged_tokens'],9000)

    def test_context_block_has_estimate_before_validation_and_no_request(self):
        client=self.client()
        with patch.object(GroqClient,'_request') as request,self.assertRaises(GroqError):
            client.chat([{'role':'user','content':'PRIVATE_CONTENT_MARKER'+'x'*20000}],[],9999999999)
        request.assert_not_called();event=client.reservation_events[0]
        self.assertEqual(event['stage'],'context');self.assertEqual(event['blocked_by'],['context_bytes'])
        self.assertGreater(event['reserved_tokens'],20000)
        self.assertEqual(event['reserved_tokens'],event['estimated_prompt_tokens']+event['output_reservation']+event['safety_reservation'])
        self.assertNotIn('PRIVATE_CONTENT_MARKER',json.dumps(event))

    def test_preflight_block_preserves_plan_estimate_but_sends_no_inference(self):
        client=self.client();api=FakeAPI();api.models=[]
        with patch.object(GroqClient,'_request',side_effect=api),self.assertRaises(GroqError):
            client.chat([{'role':'user','content':'short'}],[],9999999999)
        event=client.reservation_events[0]
        self.assertEqual(event['stage'],'preflight');self.assertEqual(event['error'],'groq_model_unavailable')
        self.assertIsNotNone(event['estimated_prompt_tokens'])
        self.assertFalse(any(path.endswith('completions') for _,path,_ in api.requests))
        self.assertEqual(client.budget_status['used_requests'],0)

    def test_measured_consumption_and_unreported_estimates_are_separate(self):
        client=self.client();api=FakeAPI([frame('One.'),frame('Two.')])
        messages=[{'role':'user','content':'short'}]
        with patch.object(GroqClient,'_request',side_effect=api):
            client.chat(messages,[],9999999999)
            client.chat(messages+[{'role':'assistant','content':'One.'},{'role':'user','content':'next'}],[],9999999999)
        event=client.reservation_events[1]
        self.assertEqual(event['before']['local_batch_budget']['reported_tokens'],220)
        self.assertEqual(event['before']['local_batch_budget']['charged_tokens'],220)
        self.assertEqual(event['before']['local_batch_budget']['estimated_unreported_tokens'],0)
        self.assertEqual(event['measured_prefix_tokens'],200)
        self.assertEqual(event['estimated_prompt_tokens'],200+event['new_context_bytes'])
        self.assertIsNone(event['before']['free_daily']['remaining_tokens'])
        self.assertEqual(client.budget_status['reported_tokens'],440)

    def test_provider_failure_does_not_turn_allowed_reservation_into_successful_answer_or_retry(self):
        client=self.client();api=FakeAPI([GroqError('groq_rate_limited')])
        with patch.object(GroqClient,'_request',side_effect=api),self.assertRaises(GroqError):
            client.chat([{'role':'user','content':'short'}],[],9999999999)
        self.assertEqual(client.reservation_events[0]['status'],'allowed')
        self.assertEqual(client.budget_status['reported_tokens'],0)
        self.assertGreater(client.budget_status['estimated_unreported_tokens'],0)
        self.assertEqual(sum(path.endswith('completions') for _,path,_ in api.requests),1)

    def test_event_fields_are_copies_and_never_include_keys_messages_or_private_reasoning(self):
        client=self.client();client._reserve(1000)
        first=client.reservation_events
        first[0]['before']['local_batch_budget']['reported_tokens']=999
        self.assertEqual(client.reservation_events[0]['before']['local_batch_budget']['reported_tokens'],0)
        self.assertEqual(client.reservation_events[0]['before']['local_batch_budget']['estimated_unreported_tokens'],0)
        client._reserve(1)
        self.assertEqual(client.reservation_events[1]['before']['local_batch_budget']['reported_tokens'],0)
        self.assertEqual(client.reservation_events[1]['before']['local_batch_budget']['estimated_unreported_tokens'],1000)
        for key in ('messages','tools','headers','authorization','api_key','reasoning','thinking'):
            self.assertNotIn('"'+key+'"',json.dumps(client.reservation_events))


if __name__=='__main__':unittest.main()
