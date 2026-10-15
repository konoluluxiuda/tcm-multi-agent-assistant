"""Offline execution checks use synthetic sources and the actual HTTP routes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from scripts.eval_tcm_tasks import ApiChecks, check_case, evaluate
from tcm import api as routes
from tcm.herbs import import_snapshot


class TaskExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.database = self.root / 'herbs.sqlite3'
        self.version = 'synthetic-task-test'
        self.ids = []
        self.anchors = []
        for name in ('FixtureA', 'FixtureB', 'FixtureC'):
            relative = f'etcm_herbs/{name}.json'
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps({'code': 1, 'data': [{
                'id': 'base_information', 'value': [
                    {'key': 'Herb Name in Pinyin', 'value': name},
                    {'key': 'Family', 'value': 'Fixtureaceae'},
                ]}]}).encode()
            path.write_bytes(raw)
            self.ids.append('etcm2-local:' + hashlib.sha256(relative.encode()).hexdigest())
            self.anchors.append({'relative_path': relative,
                                 'sha256': hashlib.sha256(raw).hexdigest(),
                                 'json_pointer': '/data/0/value/1/value',
                                 'raw_value': 'Fixtureaceae'})
        import_snapshot(self.source, self.database, self.version)
        route_patch = patch.multiple(routes, DB_PATH=str(self.database), DATASET_VERSION=self.version)
        route_patch.start()
        self.addCleanup(route_patch.stop)
        self.client = TestClient(routes.app)
        self.addCleanup(self.client.close)
        self.api = ApiChecks(self.client, self.version)

    def case(self, inputs=None, expected=None, **overrides):
        result = {
            'id': 'synthetic-case', 'category': 'detail', 'split': 'dev',
            'capability_status': 'supported', 'execution_mode': 'api',
            'input': inputs or {'operation': 'lookup', 'name': 'FixtureA'},
            'expected': expected if expected is not None else {
                'status': 'ok', 'record_count': 1, 'source_record_id': self.ids[0],
                'fields': {'family': {'state': 'present', 'value': 'Fixtureaceae'}}},
            'source_evidence': [copy.deepcopy(self.anchors[0])],
        }
        result.update(overrides)
        return result

    def test_correct_lookup_checks_real_fields_and_keeps_human_review_pending(self):
        row = check_case(self.case(), self.api)
        self.assertEqual(row['automated_status'], 'passed')
        self.assertEqual(row['task_status'], 'pending_human_review')
        self.assertEqual(row['human_review'], 'pending')
        self.assertTrue(any(c['key'].endswith('source_hash') for c in row['checks']))
        self.assertEqual(row['requests'][0]['http_status'], 200)

    def test_wrong_value_pointer_and_hash_each_fail(self):
        for defect in ('value', 'pointer', 'hash'):
            with self.subTest(defect=defect):
                case = self.case()
                if defect == 'value':
                    case['expected']['fields']['family']['value'] = 'Invented family'
                elif defect == 'pointer':
                    case['source_evidence'][0]['json_pointer'] = '/data/0/value/0/value'
                else:
                    case['source_evidence'][0]['sha256'] = '0' * 64
                row = check_case(case, self.api)
                self.assertEqual(row['automated_status'], 'failed')
                self.assertTrue(any(not c['passed'] for c in row['checks']))

    def test_section_anchor_resolves_present_identity_without_faking_field_evidence(self):
        case = self.case()
        raw = json.loads((self.source / self.anchors[0]['relative_path']).read_text())
        case['source_evidence'] = [{**self.anchors[0], 'json_pointer': '/data/0/value',
                                    'raw_value': raw['data'][0]['value']}]
        self.assertEqual(check_case(case, self.api)['automated_status'], 'passed')
        case['source_evidence'][0]['raw_value'][1]['value'] = 'Wrong family'
        row = check_case(case, self.api)
        self.assertEqual(row['automated_status'], 'failed')
        self.assertTrue(any(not c['passed'] and c['key'].endswith('.raw_value') for c in row['checks']))

    def test_snapshot_mismatch_blocks_execution_before_requests(self):
        suite = json.loads((Path(__file__).resolve().parents[1] / 'eval/tcm_task_candidates_v1.json').read_text())
        client = Mock()
        report = evaluate(suite, self.database, client)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['summary']['automated_executed'], 0)
        self.assertIsNone(report['task_completion_rate'])
        client.get.assert_not_called()

    def test_complete_filter_ids_are_checked_beyond_the_requested_page(self):
        inputs = {'operation': 'filter', 'filters': {'family': 'Fixtureaceae'}, 'limit': 1}
        expected = {'status': 'ok', 'count': 3, 'returned_count': 1,
                    'page_source_record_ids': self.ids[:1],
                    'all_matching_source_record_ids': list(self.ids)}
        self.assertEqual(check_case(self.case(inputs, expected), self.api)['automated_status'], 'passed')
        expected['all_matching_source_record_ids'][-1] = 'etcm2-local:invented'
        row = check_case(self.case(inputs, expected), self.api)
        self.assertEqual(row['automated_status'], 'failed')
        failure = next(c for c in row['checks'] if c['key'] == 'all_matching_source_record_ids')
        self.assertEqual(failure['actual'], self.ids)
        self.assertGreaterEqual(len(row['requests']), 2)

    def test_limit_zero_is_rejected_by_real_fastapi_validation(self):
        row = check_case(self.case(
            {'operation': 'filter', 'filters': {'family': 'Fixtureaceae'}, 'limit': 0},
            {'http_status': 422, 'detail_location': ['query', 'limit']}), self.api)
        self.assertEqual(row['automated_status'], 'passed')
        self.assertEqual(row['requests'][0]['http_status'], 422)
        self.assertTrue(next(c for c in row['checks'] if c['key'] == 'detail_location')['passed'])

    def test_chinese_degradation_passes_without_claiming_requested_capability(self):
        row = check_case(self.case(
            {'operation': 'lookup', 'name': '测试药材'},
            {'api_contract': {'status': 'unsupported_name_mapping', 'records': [],
                              'required_warning': 'chinese_names_unmapped'},
             'requested_capability': 'Chinese name resolution',
             'requested_capability_supported': False}, capability_status='unsupported'), self.api)
        self.assertEqual(row['degradation_contract'], 'passed')
        self.assertEqual(row['automated_status'], 'partial')
        self.assertEqual(row['task_status'], 'unsupported')
        self.assertIn('requested_capability_supported', row['unverified_expected_keys'])

    def test_manual_ui_and_planned_chat_never_issue_http_requests(self):
        client = Mock()
        api = ApiChecks(client, self.version)
        for mode, operation, task_status in (
                ('manual_ui', 'ui_sequence', 'manual_required'),
                ('planned_chat', 'planned_chat_sequence', 'unsupported')):
            with self.subTest(mode=mode):
                row = check_case(self.case({'operation': operation}, execution_mode=mode), api)
                self.assertEqual(row['automated_status'], 'not_run')
                self.assertEqual(row['task_status'], task_status)
                self.assertEqual(row['requests'], [])
                self.assertEqual(row['unverified_expected_keys'], sorted(self.case()['expected']))
        client.get.assert_not_called()

    def test_unknown_expected_key_is_explicitly_unverified(self):
        case = self.case()
        case['expected']['future_answer_quality'] = True
        row = check_case(case, self.api)
        self.assertEqual(row['automated_status'], 'partial')
        self.assertEqual(row['unverified_expected_keys'], ['future_answer_quality'])
        self.assertFalse(any(c['key'] == 'future_answer_quality' for c in row['checks']))
        self.assertEqual(row['task_status'], 'pending_human_review')

    def test_transport_exception_is_a_failure(self):
        client = Mock()
        client.get.side_effect = RuntimeError('synthetic transport failure')
        row = check_case(self.case(), ApiChecks(client, self.version))
        self.assertEqual(row['automated_status'], 'failed')
        self.assertIn('synthetic transport failure', row['error'])
        self.assertEqual(row['task_status'], 'pending_human_review')


if __name__ == '__main__':
    unittest.main()
