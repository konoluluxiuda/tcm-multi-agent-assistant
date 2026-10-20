"""Comparison uses production rule orchestration, not a straw-man planner."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from scripts.compare_evidence_synthetic import compare, rule_baseline
from scripts.eval_evidence_synthetic import CASES


class ComparisonTests(unittest.TestCase):
    def test_rules_handle_local_fields_and_missing_entity_without_models(self):
        with patch('tcm.agent_research.runtime.get_service', side_effect=AssertionError('corpus read')):
            local = rule_baseline(CASES[0]['question'])
            missing = rule_baseline(CASES[2]['question'])
        self.assertEqual(local['status'], 'candidates')
        self.assertEqual({s['chunk_id'] for s in local['sources']},
                         {'synthetic-local-efficacy', 'synthetic-local-caution'})
        self.assertEqual(missing['status'], 'clarify_entity')
        self.assertEqual(missing['sources'], [])
        self.assertEqual(local['model_calls'] + missing['model_calls'], 0)

    def saved(self):
        return {'data_mode': 'synthetic_only', 'cases': [
            {'id': c['id'], 'question': c['question'], 'result': {
                'data_mode': 'synthetic_only', 'status': c['statuses'][0], 'evidence': []}}
            for c in CASES]}

    def test_multi_source_gap_is_reported_without_inventing_accuracy(self):
        report = compare(self.saved())
        combined = report['cases'][1]
        self.assertEqual(combined['rule']['status'], 'literature_term')
        self.assertFalse(combined['rule_source_coverage']['required_groups_available'])
        self.assertIsNone(report['business_accuracy'])
        self.assertIsNone(report['cases'][2]['rule_source_coverage']['required_groups_available'])
        self.assertTrue(all(r['human_task_completion'] is None for r in report['cases']))

    def test_reject_real_source_changed_missing_or_duplicate_inputs(self):
        changes = []
        r = self.saved(); r['data_mode'] = 'configured_local_sources'; changes.append(r)
        r = self.saved(); r['cases'][0]['result']['data_mode'] = 'configured_local_sources'; changes.append(r)
        r = self.saved(); r['cases'][0]['question'] = 'changed'; changes.append(r)
        r = self.saved(); r['cases'].pop(); changes.append(r)
        r = self.saved(); r['cases'].append(deepcopy(r['cases'][0])); changes.append(r)
        for report in changes:
            with self.subTest(report=report), self.assertRaises(ValueError):
                compare(report)
