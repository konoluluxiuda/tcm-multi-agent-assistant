"""Fault injection against the independent review CLI, using handwritten truth."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IndependentReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.source = self.directory / 'source'
        self.raw = self.source / 'etcm_herbs/HerbA.json'
        self.raw.parent.mkdir(parents=True)
        obj = {'code': 1, 'data': [{'id': 'base_information', 'value': [
            {'key': 'Herb Name in Pinyin', 'value': 'HerbA'},
            {'key': 'Property', 'value': 'Cold'},
        ]}]}
        self.raw.write_text(json.dumps(obj), encoding='utf-8')
        path = 'etcm_herbs/HerbA.json'
        digest = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        # Explicit oracle for two source fields; all other fields absent.
        names = ('name_pinyin', 'latin_name', 'english_name', 'family', 'habitat',
                 'medicinal_part', 'properties', 'flavors', 'meridians',
                 'efficacy_class', 'indications', 'reference_pmids', 'cross_references')
        fields = {name: {'state': 'missing', 'value': None, 'json_pointer': None,
                         'raw_field_key': None, 'raw_value': None} for name in names}
        for name, value, key, index in (('name_pinyin', 'HerbA', 'Herb Name in Pinyin', 0),
                                        ('properties', 'Cold', 'Property', 1)):
            fields[name] = {'state': 'present', 'value': value,
                            'json_pointer': f'/data/0/value/{index}/value',
                            'raw_field_key': key, 'raw_value': value}
        payload = json.dumps([[path, digest]], ensure_ascii=False, separators=(',', ':')).encode()
        self.suite = {'suite_version': 1, 'dataset_version': 'fixture-v1',
                      'review': {'status': 'source_reconciled_pending_human_signoff', 'human_reviewer': None},
                      'snapshot': {'record_count': 1, 'sha256': hashlib.sha256(payload).hexdigest()},
                      'records': {path: {'sha256': digest, 'fields': fields}},
                      'cases': [{'id': 'detail', 'operation': 'lookup', 'input': {'name': 'HerbA'},
                                 'expected': {'status': 'ok', 'paths': [path]}}]}
        self.path = self.directory / 'suite.json'
        self.out = self.directory / 'review'

    def write_suite(self):
        self.path.write_text(json.dumps(self.suite), encoding='utf-8')

    def run_review(self, phase='all'):
        return subprocess.run(
            [sys.executable, '-m', 'scripts.review_tcm_golden', '--suite', str(self.path),
             '--source', str(self.source), '--out', str(self.out), '--phase', phase],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )

    def assert_mismatch(self, suffix):
        result = self.run_review()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads((self.out / 'review.json').read_text())
        checks = list(report['snapshot_checks'])
        for group in report['record_checks'] + report['case_checks']:
            checks.extend(group['checks'])
        self.assertTrue(any(check['key'].endswith(suffix) and check['ok'] is False
                            for check in checks), suffix)

    def test_correct_fixture_and_inputs_unchanged(self):
        self.write_suite()
        before = (self.path.read_bytes(), self.raw.read_bytes())
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, (self.path.read_bytes(), self.raw.read_bytes()))
        self.assertIsNone(json.loads(self.path.read_text())['review']['human_reviewer'])

    def test_wrong_frozen_field_detected(self):
        self.suite['records']['etcm_herbs/HerbA.json']['fields']['properties']['value'] = 'Warm'
        self.write_suite()
        self.assert_mismatch('.properties.value')

    def test_wrong_pointer_detected(self):
        self.suite['records']['etcm_herbs/HerbA.json']['fields']['properties']['json_pointer'] = '/data/0/value/0/value'
        self.write_suite()
        self.assert_mismatch('.properties.json_pointer')

    def test_wrong_expected_result_detected(self):
        self.suite['cases'][0]['expected']['paths'] = []
        self.write_suite()
        self.assert_mismatch('.paths')

    def test_explicit_current_version_is_not_missing(self):
        self.suite['cases'][0]['input']['dataset_version'] = 'fixture-v1'
        self.write_suite()
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_present_but_empty_field_keeps_provenance(self):
        obj = json.loads(self.raw.read_text())
        obj['data'][0]['value'][1]['value'] = ''
        self.raw.write_text(json.dumps(obj), encoding='utf-8')
        digest = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.suite['records']['etcm_herbs/HerbA.json']['sha256'] = digest
        field = self.suite['records']['etcm_herbs/HerbA.json']['fields']['properties']
        field.update(state='missing', value=None, raw_value='')
        payload = json.dumps([['etcm_herbs/HerbA.json', digest]], ensure_ascii=False,
                             separators=(',', ':')).encode()
        self.suite['snapshot']['sha256'] = hashlib.sha256(payload).hexdigest()
        self.write_suite()
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_output_inside_source_refused(self):
        self.write_suite()
        before = self.raw.read_bytes()
        self.out = self.source / 'audit-output'
        self.assertNotEqual(self.run_review().returncode, 0)
        self.assertFalse(self.out.exists())
        self.assertEqual(before, self.raw.read_bytes())

    def test_snapshot_drift_detected(self):
        self.write_suite()
        self.raw.write_text(self.raw.read_text() + '\n')
        self.assert_mismatch('snapshot.sha256')

    def test_changed_suite_invalidates_blind_binding(self):
        self.write_suite()
        result = self.run_review('blind')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.suite['cases'][0]['expected']['paths'] = []
        self.write_suite()
        self.assertNotEqual(self.run_review('compare').returncode, 0)


if __name__ == '__main__':
    unittest.main()
