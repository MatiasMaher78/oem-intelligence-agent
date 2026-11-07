"""Independent benchmark oracles, adversarial scorer mutations and offline/real gates."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oem_agent.groq_hosted import FreeQuota, GroqClient, GroqError
from oem_agent.stage2_db import INVENTORY
from oem_agent.stage4_review import fingerprint
from oem_agent.stage5_eval import (grade, simulated_run, evaluate_batch, evaluate_controls,
                                   main, ScriptedProtocol, _frame, _call)
from oem_agent.stage5_eval_data import (GROUND_TRUTH, SCENARIOS, build_dataset, dataset_identity, question)


def reseal(report):
    report['report_sha256'] = fingerprint({k:v for k,v in report.items() if k != 'report_sha256'})
    return report


def final_frame(case_id, **changes):
    value = {'facts': deepcopy(GROUND_TRUTH[case_id]), 'explanation': 'Explicacion sintetica evaluable por un humano.'}
    value['facts'].update(changes)
    return _frame(json.dumps(value))


def client():
    quota = FreeQuota(datetime.now(timezone.utc).isoformat(),30,1000,8000,200000,None,None)
    return GroqClient(free_quota=quota,allow_synthetic_transfer=True)


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = build_dataset()
        cls.reports = {key: simulated_run(key,snap) for key,snap in cls.snapshots.items()}

    def score(self,key='E1',report=None):
        return grade(key,self.snapshots[key],report or self.reports[key],simulated=True)

    def test_exactly_five_independent_pieces_and_existing_seed_is_unchanged(self):
        self.assertEqual(list(self.snapshots),['E1','E2','E3','E4','E5'])
        ids = [snap.evidence['results'][0]['inventory']['id'] for snap in self.snapshots.values()]
        self.assertEqual(ids,[1001,1002,1003,1004,1005])
        self.assertFalse(set(ids)&{row[0] for row in INVENTORY})
        self.assertEqual(len(INVENTORY),32)
        self.assertEqual([snap.evidence['total_candidates'] for snap in self.snapshots.values()],[1]*5)

    def test_repeat_build_has_identical_source_dossier_question_and_dataset_hashes(self):
        other=build_dataset()
        self.assertEqual(dataset_identity(self.snapshots),dataset_identity(other))
        for key in other:
            self.assertEqual(self.snapshots[key].evidence,other[key].evidence)
            self.assertEqual(self.snapshots[key].dossier,other[key].dossier)

    def test_numeric_and_separator_oems_remain_literal_strings_with_leading_zeros(self):
        for key,expected in [('E1','0000421001'),('E2','EV5A0102B'),('E3','EV5-AB/03.01')]:
            value=self.snapshots[key].evidence['results'][0]['inventory']['oem']
            self.assertIs(type(value),str);self.assertEqual(value,expected)
        self.assertTrue(self.snapshots['E1'].evidence['results'][0]['inventory']['oem'].startswith('0000'))

    def test_authored_numeric_oracles_match_existing_deterministic_math(self):
        for key,snapshot in self.snapshots.items():
            stats=snapshot.evidence['results'][0]['statistics']
            for field,expected in GROUND_TRUTH[key].items():
                if field in stats:self.assertEqual(stats[field],expected,(key,field))
        for key,low,high,units in [('E1',1900,9100,44),('E3',5900,13100,16)]:
            stats=self.snapshots[key].evidence['results'][0]['statistics']
            self.assertEqual(stats['lower_cutoff_cents'],{'numerator':low,'denominator':1})
            self.assertEqual(stats['upper_cutoff_cents'],{'numerator':high,'denominator':1})
            self.assertEqual(stats['filtered_units'],units)
        self.assertGreater(19000,GROUND_TRUTH['E1']['filtered_max_price_cents'])
        self.assertLessEqual(GROUND_TRUTH['E3']['filtered_min_price_cents'],6500)
        self.assertLessEqual(6500,GROUND_TRUTH['E3']['filtered_max_price_cents'])

    def test_prefix_and_separator_traps_are_audited_and_excluded(self):
        for key,trap in [('E1','00004210010'),('E3','EV5AB0301')]:
            rows=self.snapshots[key].evidence['results'][0]['offer_audit']
            matches=[r for r in rows if r['offer']['oem']==trap]
            self.assertEqual(len(matches),1)
            self.assertEqual(matches[0]['oem_match'],'different')
            self.assertFalse(matches[0]['observed_included'])

    def test_incompatible_and_unknown_axes_remain_distinct(self):
        case=self.snapshots['E2'].evidence['results'][0]
        self.assertEqual({(r['oem_match'],r['compatibility']) for r in case['offer_audit']},
                         {('exact','incompatible'),('exact','unknown'),('unknown','confirmed'),('different','confirmed')})
        self.assertEqual(sum(r['offer']['available_units']==0 for r in case['offer_audit']),1)
        self.assertEqual(case['catalog_status'],'not_assessed')
        self.assertFalse(case['has_confirmed_match'])

    def test_no_offers_is_successful_absence_not_source_failure_or_bad_catalog(self):
        case=self.snapshots['E4'].evidence['results'][0]
        self.assertEqual([m['status'] for m in case['marketplaces']],['no_results','no_results'])
        self.assertTrue(case['matching_complete'])
        self.assertEqual(case['catalog_status'],'not_assessed')
        self.assertIsNone(case['statistics']['observed_min_price_cents'])
        self.assertEqual(case['statistics']['observed_units'],0)

    def test_error_fixture_records_real_failed_read_and_keeps_known_offers(self):
        snap=self.snapshots['E5'];evidence=snap.evidence;case=evidence['results'][0]
        self.assertFalse(evidence['complete']);self.assertEqual(evidence['status'],'partial')
        self.assertEqual([m['status'] for m in case['marketplaces']],['error','ok'])
        self.assertEqual(len(case['marketplaces'][1]['comparisons']),5)
        self.assertEqual(sum(e['status']=='error' for e in evidence['trace']),1)
        self.assertIsNone(case['statistics']['filtered_min_price_cents'])
        self.assertFalse(snap.dossier['cases'][0]['eligible'])
        self.assertIn('incomplete_source_coverage',snap.dossier['cases'][0]['block_reasons'])
        self.assertTrue(all(self.snapshots[k].evidence['complete'] for k in ('E1','E2','E3','E4')))

    def test_all_scripted_scenarios_pass_without_claiming_model_quality(self):
        for key in SCENARIOS:
            result=self.score(key)
            self.assertTrue(result['passed'],(key,result['checks']))
            self.assertEqual(result['mode'],'scripted_offline')
            self.assertEqual(result['free_prose_accuracy'],'pending_manual_review')
            self.assertFalse(result['agent_answer_verified'])
            self.assertEqual(result['metrics']['token_source'],'simulated_protocol')
            self.assertEqual(result['evaluation_sha256'],fingerprint({k:v for k,v in result.items() if k!='evaluation_sha256'}))

    def test_expected_abstention_and_partial_source_are_not_false_failures(self):
        for key in ('E2','E4','E5'):
            self.assertTrue(self.score(key)['checks']['abstention'])
        self.assertEqual(self.score('E5')['metrics']['source_errors'],1)
        self.assertEqual(self.score('E4')['metrics']['source_errors'],0)
        self.assertFalse(self.score('E5')['execution_complete'])
        self.assertTrue(self.score('E5')['passed'])
        self.assertTrue(self.score('E4')['execution_complete'])

    def test_mutated_counts_units_prices_null_oem_assessment_and_abstention_fail(self):
        for field,value in [('observed_units',10),('observed_offers_count',55),('observed_min_price_cents',1001),
                            ('filtered_max_price_cents',10000),('oem',421001),('assessment','competitive_price_demo'),('abstain',True)]:
            report=simulated_run('E1',self.snapshots['E1'],frames=[_frame(calls=[_call('inspect_case',{'inventory_id':1001},'read')]),final_frame('E1',**{field:value})])
            self.assertFalse(self.score(report=report)['passed'],field)
        report=deepcopy(self.reports['E4']);payload={'facts':deepcopy(GROUND_TRUTH['E4']),'explanation':'Dato ausente.'}
        payload['facts']['observed_min_price_cents']=0
        footer=report['answer'].split('\n\nFuentes consultadas: ',1)[1]
        report['answer']=json.dumps(payload)+'\n\nFuentes consultadas: '+footer
        self.assertFalse(self.score('E4',reseal(report))['checks']['exact_facts'])

    def test_integer_float_and_bool_coercions_are_not_accepted_as_exact_facts(self):
        for field,value in [('observed_offers_count',10.0),('abstain',0)]:
            report=simulated_run('E1',self.snapshots['E1'],frames=[_frame(calls=[_call('inspect_case',{'inventory_id':1001},'read')]),final_frame('E1',**{field:value})])
            self.assertFalse(self.score(report=report)['checks']['exact_facts'])

    def test_correct_facts_without_necessary_native_reads_do_not_pass(self):
        report=simulated_run('E1',self.snapshots['E1'],frames=[_frame(calls=[_call('summarize_run',{},'read')]),final_frame('E1')])
        value=self.score(report=report)
        self.assertTrue(value['checks']['exact_facts'])
        self.assertFalse(value['checks']['tool_selection']);self.assertFalse(value['passed'])

    def test_model_answer_without_any_evidence_is_rejected(self):
        report=simulated_run('E1',self.snapshots['E1'],frames=[final_frame('E1')])
        self.assertIsNone(report['answer']);self.assertFalse(self.score(report=report)['passed'])

    def test_unfinished_offer_page_cannot_pass_even_with_expected_text(self):
        report=simulated_run('E2',self.snapshots['E2'],frames=[_frame(calls=[_call('inspect_case',{'inventory_id':1002,'section':'matching'},'a'),_call('list_offers',{'inventory_id':1002,'limit':2},'b')]),final_frame('E2')])
        value=self.score('E2',report)
        self.assertFalse(value['passed']);self.assertFalse(value['checks']['coverage'])
        self.assertFalse(value['checks']['tool_selection'])

    def test_foreign_or_unread_reference_and_missing_footer_are_rejected(self):
        report=deepcopy(self.reports['E1']);report['answer_references'][0]['reference']['sha256']='0'*64
        self.assertFalse(self.score(report=reseal(report))['checks']['references'])
        report=deepcopy(self.reports['E1']);report['answer_references'][0]['reference']['pointer']='/selection'
        self.assertFalse(self.score(report=reseal(report))['checks']['references'])
        report=deepcopy(self.reports['E1']);report['answer']=report['answer'].split('\n\nFuentes consultadas: ',1)[0]
        self.assertFalse(self.score(report=reseal(report))['checks']['references'])

    def test_report_and_event_integrity_tampering_are_detected(self):
        report=deepcopy(self.reports['E1']);report['elapsed_ms']+=1
        self.assertFalse(self.score(report=report)['checks']['integrity'])
        report=deepcopy(self.reports['E1']);report['dispatcher']['trace'][0]['elapsed_ms']+=1
        self.assertFalse(self.score(report=reseal(report))['checks']['integrity'])

    def test_configuration_mutation_cannot_hide_behind_recomputed_hashes(self):
        report=deepcopy(self.reports['E1']);report['configuration']['skill']['version']='99.0.0'
        report['configuration_sha256']=fingerprint(report['configuration'])
        self.assertFalse(self.score(report=reseal(report))['checks']['configuration'])

    def test_wrong_prompt_or_excess_limits_are_detected_despite_resealing(self):
        report=deepcopy(self.reports['E1']);report['configuration']['system_prompt_sha256']='0'*64
        report['configuration_sha256']=fingerprint(report['configuration'])
        self.assertFalse(self.score(report=reseal(report))['checks']['configuration'])
        report=deepcopy(self.reports['E1']);report['limits']['context_tokens']=999999
        report['configuration']['execution_limits']=deepcopy(report['limits'])
        report['configuration_sha256']=fingerprint(report['configuration'])
        self.assertFalse(self.score(report=reseal(report))['checks']['errors_and_limits'])

    def test_missing_input_or_known_source_failure_cannot_be_reported_complete(self):
        report=deepcopy(self.reports['E5']);report['complete']=True
        self.assertFalse(self.score('E5',reseal(report))['checks']['coverage'])
        self.assertFalse(self.score('E1',self.reports['E3'])['passed'])

    def test_correct_facts_from_a_different_question_are_not_reproducible_evidence(self):
        report=deepcopy(self.reports['E1']);report['question']='A different synthetic question'
        self.assertFalse(self.score(report=reseal(report))['checks']['traceability'])

    def test_prose_remains_manual_even_when_structured_facts_are_correct(self):
        self.assertEqual(self.score()['free_prose_accuracy'],'pending_manual_review')
        self.assertFalse(self.score()['agent_answer_verified'])

    def test_all_twelve_negative_controls_pass_and_cover_all_five_allowed_tools(self):
        controls=evaluate_controls(self.snapshots)
        self.assertEqual((controls['passed'],controls['total']),(12,12))
        for item in controls['probes']:
            report=item['report'];self.assertFalse(report['complete'])
        allowed=self.reports['E1']['dispatcher']['allowed_tools']
        self.assertEqual(set(allowed),{'summarize_run','list_cases','inspect_case','list_offers','review_status'})

    def test_duplicate_extra_key_and_non_json_answers_fail_format(self):
        for text in ('not JSON','{"facts":{},"facts":{},"explanation":"x"}',
                     '{"facts":{},"explanation":"x","extra":true}'):
            report=deepcopy(self.reports['E1']);footer=report['answer'].split('\n\nFuentes consultadas: ',1)[1]
            report['answer']=text+'\n\nFuentes consultadas: '+footer
            self.assertFalse(self.score(report=reseal(report))['checks']['answer_format'])

    def test_private_protocol_reasoning_is_omitted(self):
        initial=_frame(calls=[_call('inspect_case',{'inventory_id':1001},'read')])
        initial['choices'][0]['message']['reasoning']='PRIVATE_EVALUATION_MARKER'
        report=simulated_run('E1',self.snapshots['E1'],frames=[initial,final_frame('E1')])
        self.assertNotIn('PRIVATE_EVALUATION_MARKER',json.dumps(report))
        self.assertTrue(self.score(report=report)['passed'])

    def test_offline_export_is_exclusive_and_contains_inputs_trace_and_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'benchmark'
            result=evaluate_batch(self.snapshots,list(SCENARIOS),path)
            self.assertEqual((result['passed'],result['total']),(5,5))
            self.assertEqual(result['controls'],{'passed':12,'total':12})
            self.assertTrue(result['no_model_quality_claim_from_scripted_runs'])
            self.assertEqual(result['summary_sha256'],fingerprint({k:v for k,v in result.items() if k!='summary_sha256'}))
            self.assertEqual(len(list(path.glob('*-agent.json'))),5)
            self.assertTrue((path/'controls.json').is_file())
            with self.assertRaises(FileExistsError):evaluate_batch(self.snapshots,['E1'],path)

    def test_offline_cli_never_loads_credentials_or_opens_real_wire(self):
        with tempfile.TemporaryDirectory() as folder, patch('oem_agent.stage5_eval.load_local_env') as env, \
                patch('http.client.HTTPSConnection',side_effect=AssertionError('Network forbidden')), redirect_stdout(io.StringIO()):
            code=main(['offline','--cases','E1','--out',str(Path(folder)/'out')])
        self.assertEqual(code,0);env.assert_not_called()

    def test_real_cli_requires_explicit_transfer_and_recent_quota_before_network(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(GroqClient,'preflight') as preflight, \
                patch('oem_agent.stage5_eval.load_local_env') as env, redirect_stdout(io.StringIO()):
            self.assertEqual(main(['real','--out',str(Path(folder)/'out')]),1)
        preflight.assert_not_called();env.assert_not_called()

    def test_real_mode_rejects_more_than_three_cases_or_duplicates_before_requests(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(GroqClient,'_request') as request:
            for cases in (['E1','E2','E3','E4'],['E1','E1']):
                with self.assertRaises(ValueError):evaluate_batch(self.snapshots,cases,Path(folder)/'new',client=client())
            self.assertFalse((Path(folder)/'new').exists());request.assert_not_called()

    def test_real_batch_stops_after_429_and_does_not_reset_budget_or_retry(self):
        protocol=ScriptedProtocol('E1',frames=[GroqError('groq_rate_limited')]);transport=client()
        with tempfile.TemporaryDirectory() as folder, patch.object(GroqClient,'_request',side_effect=protocol.request):
            result=evaluate_batch(self.snapshots,['E1','E3'],Path(folder)/'new',client=transport)
        self.assertEqual(result['evaluated_cases'],['E1'])
        self.assertFalse(result['all_requested_evaluated']);self.assertEqual(result['passed'],0)
        self.assertEqual(sum(path.endswith('completions') for _,path,_ in protocol.requests),1)
        self.assertEqual(transport.budget_status['used_requests'],1)
        self.assertGreater(transport.budget_status['estimated_unreported_tokens'],0)
        self.assertEqual(transport.budget_status['reported_tokens'],0)

    def test_expected_partial_real_case_stops_later_questions_but_grades_correctly(self):
        protocol=ScriptedProtocol('E5');transport=client()
        with tempfile.TemporaryDirectory() as folder,patch.object(GroqClient,'_request',side_effect=protocol.request):
            result=evaluate_batch(self.snapshots,['E5','E1'],Path(folder)/'new',client=transport)
        self.assertEqual(result['evaluated_cases'],['E5'])
        self.assertEqual(result['passed'],1);self.assertFalse(result['all_requested_evaluated'])
        self.assertEqual(result['provider_budget']['reported_tokens'],560)

    def test_real_simulation_uses_native_auto_choice_and_all_five_schemas(self):
        protocol=ScriptedProtocol('E1');transport=client()
        with tempfile.TemporaryDirectory() as folder,patch.object(GroqClient,'_request',side_effect=protocol.request):
            result=evaluate_batch(self.snapshots,['E1'],Path(folder)/'new',client=transport)
        self.assertEqual(result['passed'],1)
        posts=[payload for _,path,payload in protocol.requests if path.endswith('completions')]
        self.assertEqual(len(posts),2)
        self.assertTrue(all(p['tool_choice']=='auto' and len(p['tools'])==5 for p in posts))
        self.assertNotRegex(question('E1'),r'\b1900\b')


if __name__=='__main__':unittest.main()
