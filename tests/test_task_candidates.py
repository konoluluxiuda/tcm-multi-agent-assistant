"""Draft validation must reject fake provenance, split leakage and support claims."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.validate_tcm_task_candidates import QUOTAS, REVIEW, validate


class CandidateValidationTests(unittest.TestCase):
    def test_repository_candidate_schema(self):
        path = Path(__file__).resolve().parents[1] / 'eval/tcm_task_candidates_v1.json'
        report = validate(json.loads(path.read_text()))
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['candidate_count'], 60)
        self.assertEqual(report['source_validation'], 'not_run')
        self.assertEqual(report['task_execution'], 'not_run')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)
        path = self.source / 'etcm_herbs/Fixture.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'Property': 'Cold'}))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        pairs = [['etcm_herbs/Fixture.json', digest]]
        snapshot = hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        cases = []
        for category, count in QUOTAS.items():
            for _ in range(count):
                index = len(cases)
                split = 'dev' if index < 20 else 'test'
                cases.append({'id': f'fixture-{index}', 'category': category,
                              'question': f'Fixture question {index}', 'origin': 'designed',
                              'group_id': f'fixture-group-{index}', 'split': split,
                              'leakage_keys': [f'fixture:{index}'],
                              'capability_status': 'supported', 'execution_mode': 'api',
                              'input': {}, 'expected': {}, 'review_status': REVIEW,
                              'source_evidence': [{'relative_path': 'etcm_herbs/Fixture.json',
                                                   'sha256': digest, 'json_pointer': '/Property',
                                                   'raw_value': 'Cold'}],
                              'limitations': ['synthetic validator fixture'],
                              'execution_instructions': 'Do not execute this synthetic task'})
                if category == 'sparse_evidence':
                    cases[-1]['evidence_focus'] = 'sparse' if _ < 5 else 'pmid'
        self.suite = {'kind': 'task_candidates', 'origin': 'designed', 'review_status': REVIEW,
                      'observed_question_count': 0, 'source_snapshot': {'record_count': 1, 'sha256': snapshot},
                      'cases': cases}

    def test_valid_draft_is_not_execution_or_human_approval(self):
        report = validate(self.suite, self.source)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['task_execution'], 'not_run')
        self.assertEqual(report['human_review'], 'pending')

    def test_shared_anchor_and_group_across_splits_rejected(self):
        for key in ('leakage_keys', 'group_id'):
            with self.subTest(key=key):
                suite = copy.deepcopy(self.suite)
                suite['cases'][20][key] = suite['cases'][0][key]
                report = validate(suite)
                self.assertEqual(report['status'], 'failed')
                self.assertTrue(any('crosses dev/test' in x for x in report['errors']))

    def test_wrong_pointer_value_and_hash_rejected(self):
        for key, value in (('json_pointer', '/Absent'), ('raw_value', 'Warm'), ('sha256', '0'*64)):
            with self.subTest(key=key):
                suite = copy.deepcopy(self.suite)
                suite['cases'][0]['source_evidence'][0][key] = value
                self.assertEqual(validate(suite, self.source)['status'], 'failed')

    def test_source_drift_rejected(self):
        (self.source / 'etcm_herbs/Fixture.json').write_text('{"Property":"Warm"}')
        report = validate(self.suite, self.source)
        self.assertIn('source snapshot binding mismatch', report['errors'])

    def test_observed_or_supported_chat_claim_rejected(self):
        suite = copy.deepcopy(self.suite)
        suite['observed_question_count'] = 60
        suite['cases'][0]['execution_mode'] = 'planned_chat'
        report = validate(suite)
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(any('observed' in x for x in report['errors']))
        self.assertTrue(any('planned chat' in x for x in report['errors']))


if __name__ == '__main__':
    unittest.main()
