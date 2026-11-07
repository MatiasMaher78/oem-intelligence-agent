"""Offline identity/coverage accounting and exhaustive aggregate/granular E2 evidence."""
from collections import Counter, defaultdict
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from oem_agent.ollama_local import encode
from oem_agent.stage2_matching import compare_offer
from oem_agent.stage3_statistics import analyze_matching
from oem_agent.stage4_review import fingerprint
from oem_agent.stage5_agent import _tool_view
from oem_agent.stage5_contracts import tool_contracts
from oem_agent.stage5_dispatcher import ReadOnlyDispatcher
from oem_agent.stage5_evidence import EvidenceSnapshot
from oem_agent.stage5_eval import grade, simulated_run, _frame, _call
from oem_agent.stage5_eval_data import GROUND_TRUTH, build_dataset


def reseal(report):
    previous = None
    for event in report['dispatcher']['trace']:
        result = event['outcome']['result']
        if result:
            result['trace']['result_sha256'] = fingerprint({k:v for k,v in result.items() if k!='trace'})
            event['tool_result_sha256'] = fingerprint(result)
            event['tool_result_bytes'] = len(encode(result))
        event['previous_event_sha256'] = previous
        event['event_sha256'] = fingerprint({k:v for k,v in event.items() if k!='event_sha256'})
        previous = event['event_sha256']
    dispatch = report['dispatcher']
    dispatch['report_sha256'] = fingerprint({k:v for k,v in dispatch.items() if k!='report_sha256'})
    report['report_sha256'] = fingerprint({k:v for k,v in report.items() if k!='report_sha256'})
    return report


class MatchingSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = build_dataset()
        cls.aggregate_report = simulated_run('E2', cls.snapshots['E2'])

    def result(self, snapshot):
        identifier = snapshot.evidence['results'][0]['inventory']['id']
        return snapshot.query('inspect_case', {'inventory_id':identifier,'section':'matching'})

    def rebuild(self, offers, *, base='E1', audit_complete=True, identifier=None):
        evidence = self.snapshots[base].evidence
        case = evidence['results'][0]
        if identifier is not None:case['inventory']['id'] = identifier
        for market in case['marketplaces']:
            if market['status']=='error':continue
            rows = [compare_offer(case['inventory'],offer) for offer in offers if offer['marketplace_id']==market['marketplace_id']]
            market.update(comparisons=rows, offers_count=len(rows), confirmed_matches_count=sum(row['confirmed_match'] for row in rows),
                          status='ok' if rows else 'no_results')
        case['matching_complete'] = all(market['status']!='error' for market in case['marketplaces'])
        found = any(market['confirmed_matches_count'] for market in case['marketplaces'])
        case['has_confirmed_match'] = True if found else False if case['matching_complete'] else None
        evidence['complete'] = case['matching_complete'] and audit_complete
        evidence['status'] = 'complete' if evidence['complete'] else 'partial'
        evidence = analyze_matching(evidence,source_offers=offers,audit_complete=audit_complete)
        return EvidenceSnapshot(evidence)

    def offer(self, **changes):
        offer = deepcopy(self.snapshots['E1'].evidence['results'][0]['offer_audit'][0]['offer'])
        offer.update(changes)
        return offer

    def check_accounting(self, snapshot):
        result = self.result(snapshot);summary = result['data']['content']['offer_summary']
        case = snapshot.evidence['results'][0]
        expected = defaultdict(set)
        for row in case['offer_audit']:
            expected[(row['oem_match'],row['compatibility'])].add((row['source_marketplace_id'],row['offer']['offer_id']))
        actual = {};refs = [];identities = []
        for group in summary['groups']:
            key = (group['oem_match'],group['compatibility'])
            self.assertNotIn(key,actual)
            ids = [(market,identifier) for market,values in group['offer_ids'].items() for identifier in values]
            actual[key] = set(ids);identities += ids
            self.assertEqual(len(ids),len(set(ids)))
            self.assertEqual(group['count'],len(ids))
            self.assertEqual(group['scope'],'case')
            self.assertIs(group['coverage_complete'],summary['coverage_complete'])
            for ref in group['references']:
                row = snapshot.resolve_reference(ref);refs.append(ref['pointer'])
                self.assertEqual((row['oem_match'],row['compatibility']),key)
                self.assertIn((row['source_marketplace_id'],row['offer']['offer_id']),actual[key])
        self.assertEqual(actual,dict(expected))
        self.assertEqual(len(identities),len(set(identities)))
        self.assertEqual(summary['offers_count'],len(identities))
        self.assertEqual(summary['audit_rows_count'],len(case['offer_audit']))
        self.assertEqual(len(refs),len(set(refs)))
        self.assertEqual(set(refs),{'/results/0/offer_audit/'+str(i) for i in range(len(case['offer_audit']))})
        for ref in result['references']:snapshot.resolve_reference(ref)
        return summary

    def test_all_five_scenarios_account_for_each_source_identity_and_audit_row(self):
        for key,snapshot in self.snapshots.items():
            with self.subTest(case=key):self.check_accounting(snapshot)

    def test_E2_four_distinct_combinations_are_not_collapsed(self):
        summary = self.check_accounting(self.snapshots['E2'])
        self.assertEqual(summary['offers_count'],4)
        self.assertEqual({(g['oem_match'],g['compatibility']):g['count'] for g in summary['groups']},
                         {('exact','incompatible'):1,('exact','unknown'):1,('unknown','confirmed'):1,('different','confirmed'):1})

    def test_all_nine_classification_combinations_work_with_an_unrelated_inventory_id(self):
        offers=[]
        for i,oem in enumerate(('0000421001','OTHER-DEMO','')):
            for j,version in enumerate(('BaseEval','OtherEval','')):
                offers.append(self.offer(offer_id=f'NEW-{i}-{j}',oem=oem,version=version,
                                         marketplace_id='DEMO-MARKET-A' if j%2 else 'DEMO-MARKET-B'))
        snapshot = self.rebuild(offers,identifier=808)
        summary = self.check_accounting(snapshot)
        self.assertEqual(len(summary['groups']),9)
        self.assertEqual(summary['offers_count'],9)
        self.assertEqual(self.result(snapshot)['data']['inventory_id'],808)

    def test_same_ID_in_two_marketplaces_is_two_independent_offers(self):
        snapshot = self.rebuild([self.offer(offer_id='00007',marketplace_id=market) for market in ('DEMO-MARKET-A','DEMO-MARKET-B')])
        summary = self.check_accounting(snapshot)
        self.assertEqual(summary['offers_count'],2)
        self.assertEqual(summary['groups'][0]['offer_ids'],{'DEMO-MARKET-A':['00007'],'DEMO-MARKET-B':['00007']})

    def test_identical_duplicates_count_once_and_keep_every_audit_reference(self):
        offer = self.offer(offer_id='DUP-01')
        snapshot = self.rebuild([offer,deepcopy(offer)])
        summary = self.check_accounting(snapshot)
        self.assertEqual((summary['offers_count'],summary['audit_rows_count']),(1,2))
        self.assertEqual(len(summary['groups'][0]['references']),2)
        self.assertEqual(snapshot.evidence['results'][0]['statistics']['observed_offers_count'],1)

    def test_price_conflicts_do_not_become_new_matching_or_pricing_rules(self):
        offer = self.offer(offer_id='PRICE-CONFLICT')
        snapshot = self.rebuild([offer,dict(offer,price_cents=999999)])
        summary = self.check_accounting(snapshot)
        self.assertEqual(summary['groups'][0]['count'],1)
        self.assertEqual(summary['groups'][0]['compatibility'],'confirmed')
        self.assertEqual(snapshot.evidence['results'][0]['statistics']['observed_offers_count'],0)
        self.assertTrue(all('conflicting_duplicate_identity' in row['exclusion_reasons'] for row in snapshot.evidence['results'][0]['offer_audit']))

    def test_conflicting_classifications_fail_closed_instead_of_counting_an_ID_twice(self):
        offer = self.offer(offer_id='CLASS-CONFLICT')
        snapshot = self.rebuild([offer,dict(offer,version='OtherEval')])
        result = self.result(snapshot)
        self.assertEqual(result['status'],'error');self.assertIsNone(result['data'])
        self.assertEqual(result['error']['code'],'ambiguous_offer_identity')
        audit = snapshot.query('list_offers',{'inventory_id':1001,'limit':2})
        self.assertEqual(len(audit['data']['items']),2)
        dispatcher=ReadOnlyDispatcher(snapshot)
        dispatcher.dispatch('inspect_case',{'inventory_id':1001,'section':'matching'})
        self.assertFalse(dispatcher.finish()['complete'])

    def test_missing_IDs_are_explicit_errors_and_remain_in_granular_audit(self):
        for value in ('',None):
            with self.subTest(identifier=value):
                snapshot=self.rebuild([self.offer(offer_id=value)])
                result=self.result(snapshot)
                self.assertEqual(result['error']['code'],'unidentifiable_offer')
                self.assertIsNone(result['data'])
                self.assertEqual(snapshot.query('list_offers',{'inventory_id':1001})['data']['total_rows'],1)

    def test_empty_success_is_complete_with_zero_known_offers(self):
        summary=self.check_accounting(self.snapshots['E4'])
        self.assertEqual(summary['groups'],[]);self.assertEqual(summary['offers_count'],0)
        self.assertTrue(summary['coverage_complete'])

    def test_source_error_keeps_known_offers_but_all_groups_remain_partial(self):
        snapshot=self.snapshots['E5'];summary=self.check_accounting(snapshot)
        self.assertEqual(summary['offers_count'],5);self.assertFalse(summary['coverage_complete'])
        self.assertFalse(summary['groups'][0]['coverage_complete'])
        self.assertEqual(self.result(snapshot)['status'],'partial')
        self.assertEqual(self.result(snapshot)['data']['content']['marketplaces'][0]['status'],'error')

    def test_empty_known_source_with_an_error_does_not_assert_global_absence(self):
        snapshot=self.rebuild([],base='E5');summary=self.check_accounting(snapshot)
        self.assertEqual(summary['groups'],[]);self.assertEqual(summary['offers_count'],0)
        self.assertFalse(summary['coverage_complete'])
        self.assertEqual(self.result(snapshot)['status'],'partial')

    def test_incomplete_catalog_audit_keeps_all_known_IDs_and_partial_coverage(self):
        snapshot=self.rebuild([self.offer()],audit_complete=False)
        summary=self.check_accounting(snapshot)
        self.assertFalse(summary['coverage_complete'])
        self.assertEqual(summary['offers_count'],1)

    def test_returned_summary_is_isolated_and_does_not_mutate_snapshot_or_rules(self):
        snapshot=self.snapshots['E2'];before=fingerprint(snapshot.evidence)
        first=self.result(snapshot);first['data']['content']['offer_summary']['groups'][0]['offer_ids'].clear()
        first['data']['content']['offer_summary']['groups'][0]['references'].clear()
        self.check_accounting(snapshot)
        self.assertEqual(fingerprint(snapshot.evidence),before)

    def test_OEM_is_literal_and_units_are_not_offer_counts(self):
        content=self.result(self.snapshots['E1'])['data']['content']
        self.assertEqual(content['oem'],'0000421001')
        self.assertEqual(content['offer_summary']['offers_count'],11)
        self.assertEqual(self.snapshots['E1'].evidence['results'][0]['statistics']['observed_units'],55)

    def test_granular_reader_matches_prechange_result_hash_without_local_artifacts(self):
        # Golden hash of the deterministic pre-change reader output, including its references.
        result=self.snapshots['E2'].query('list_offers',{'inventory_id':1002,'limit':2})
        self.assertEqual(fingerprint(result),'bf8719060f86f5d1d523fb16ed143b6cb3d43cdf9ca712fe9defaa50ff2246ab')

    def test_large_summary_is_rejected_by_existing_size_guard_without_truncation(self):
        snapshot=self.rebuild([self.offer(offer_id=f'LARGE-{i:04d}') for i in range(70)])
        self.assertEqual(self.result(snapshot)['data']['content']['offer_summary']['offers_count'],70)
        dispatcher=ReadOnlyDispatcher(snapshot)
        outcome=dispatcher.dispatch('inspect_case',{'inventory_id':1001,'section':'matching'})
        self.assertEqual(outcome['error']['code'],'response_too_large')
        self.assertFalse(dispatcher.finish()['complete'])

    def test_summary_passes_unchanged_five_tool_contracts_and_keeps_model_data(self):
        snapshot=self.snapshots['E2'];dispatcher=ReadOnlyDispatcher(snapshot)
        outcome=dispatcher.dispatch('inspect_case',{'inventory_id':1002,'section':'matching'})
        self.assertIsNone(outcome['error']);self.assertEqual(len(tool_contracts()),5)
        self.assertEqual(_tool_view(outcome)['data'],outcome['result']['data'])
        self.assertTrue(dispatcher.finish()['complete'])

    def test_E2_aggregate_route_passes_with_two_simulated_native_reads(self):
        result=grade('E2',self.snapshots['E2'],self.aggregate_report,simulated=True)
        self.assertTrue(result['passed'],result['checks'])
        self.assertEqual(result['metrics']['tools'],['inspect_case','inspect_case'])
        self.assertEqual(self.aggregate_report['model_rounds'],2)
        self.assertFalse(self.aggregate_report['answer_verified'])

    def test_E2_full_granular_route_is_still_accepted_without_an_aggregate(self):
        original=EvidenceSnapshot.query
        def legacy(snapshot,tool,arguments=None):
            result=original(snapshot,tool,arguments)
            if tool=='inspect_case' and arguments.get('section')=='matching':
                result['data']['content'].pop('offer_summary',None)
                result['trace']['result_sha256']=fingerprint({k:v for k,v in result.items() if k!='trace'})
            return result
        matching=_call('inspect_case',{'inventory_id':1002,'section':'matching'},'m')
        first=_call('list_offers',{'inventory_id':1002,'limit':2},'p1')
        page=self.snapshots['E2'].query('list_offers',{'inventory_id':1002,'limit':2})
        second=_call('list_offers',{'inventory_id':1002,'limit':2,'cursor':page['data']['next_cursor']},'p2')
        final=_frame(json.dumps({'facts':GROUND_TRUTH['E2'],'explanation':'Granular completo, sin aprobacion comercial.'}))
        with patch.object(EvidenceSnapshot,'query',legacy):
            report=simulated_run('E2',self.snapshots['E2'],frames=[_frame(calls=[matching,first]),_frame(calls=[second]),final])
            score=grade('E2',self.snapshots['E2'],report,simulated=True)
        self.assertTrue(score['passed'],score['checks'])
        self.assertEqual(report['dispatcher']['pending_pages'],0)

    def test_correct_facts_without_matching_summary_or_audit_do_not_pass(self):
        final=_frame(json.dumps({'facts':GROUND_TRUTH['E2'],'explanation':'Sin evidencia de clasificacion.'}))
        report=simulated_run('E2',self.snapshots['E2'],frames=[_frame(calls=[_call('inspect_case',{'inventory_id':1002},'s')]),final])
        score=grade('E2',self.snapshots['E2'],report,simulated=True)
        self.assertTrue(score['checks']['exact_facts']);self.assertFalse(score['checks']['tool_selection'])
        self.assertFalse(score['passed'])

    def test_aggregate_mutations_cannot_pass_despite_resealed_hashes(self):
        def duplicate(s):s['groups'][1]['offer_ids']=deepcopy(s['groups'][0]['offer_ids'])
        def foreign_ref(s):s['groups'][0]['references'][0]['sha256']='0'*64
        changes=[lambda s:s['groups'].pop(),lambda s:s.update(offers_count=3),
                 lambda s:s['groups'][0].update(count=True),lambda s:s['groups'][0].update(oem_match='different'),
                 lambda s:s.update(coverage_complete=False),lambda s:s['groups'][0].update(coverage_complete=False),
                 lambda s:s.update(audit_rows_count=3),duplicate,foreign_ref]
        for change in changes:
            with self.subTest(change=change):
                report=deepcopy(self.aggregate_report)
                summary=report['dispatcher']['trace'][0]['outcome']['result']['data']['content']['offer_summary']
                change(summary);reseal(report)
                score=grade('E2',self.snapshots['E2'],report,simulated=True)
                self.assertTrue(score['checks']['exact_facts'])
                self.assertFalse(score['checks']['tool_selection']);self.assertFalse(score['passed'])

    def test_a_partial_granular_sequence_stays_partial_even_with_an_exhaustive_summary(self):
        final=_frame(json.dumps({'facts':GROUND_TRUTH['E2'],'explanation':'Quedo una pagina pendiente.'}))
        calls=[_call('inspect_case',{'inventory_id':1002,'section':'matching'},'m'),
               _call('list_offers',{'inventory_id':1002,'limit':2},'p')]
        report=simulated_run('E2',self.snapshots['E2'],frames=[_frame(calls=calls),final])
        score=grade('E2',self.snapshots['E2'],report,simulated=True)
        self.assertFalse(report['complete']);self.assertFalse(score['passed'])
        self.assertFalse(score['checks']['tool_selection']);self.assertFalse(score['checks']['coverage'])


if __name__=='__main__':unittest.main()
