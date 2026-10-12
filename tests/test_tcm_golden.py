"""Ensure frozen evaluation detects wrong values, wrong evidence and drift."""
import hashlib
import json
import sqlite3
from contextlib import closing
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_tcm_golden import read_sources
from scripts.eval_tcm_golden import evaluate, fingerprint
from tcm.herbs import import_snapshot


class GoldenEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        herbs = self.root / 'etcm_herbs'
        herbs.mkdir(parents=True)
        self.path = herbs / 'HerbA.json'
        self.obj = {'code': 1, 'data': [{'id': 'base_information', 'value': [
            {'key': 'Herb Name in Pinyin', 'value': 'HerbA'},
            {'key': 'Property', 'value': 'Cold'},
            {'key': 'Flavor', 'value': 'Bitter'},
        ]}]}
        self.path.write_text(json.dumps(self.obj))
        self.database = Path(self.tmp.name) / 'herbs.sqlite3'
        import_snapshot(self.root, self.database, 'v1')
        raw = read_sources(self.root)
        self.suite = {
            'suite_version': 1, 'dataset_version': 'v1',
            'review': {'status': 'source_reconciled_pending_human_signoff', 'human_reviewer': None},
            'snapshot': {'record_count': 1, 'sha256': fingerprint([[p, r['sha256']] for p, r in raw.items()])},
            'records': raw, 'cases': [
                {'id': 'detail', 'operation': 'lookup', 'input': {'name': 'HerbA'},
                 'expected': {'status': 'ok', 'paths': ['etcm_herbs/HerbA.json']}},
                {'id': 'validation', 'operation': 'filter', 'input': {'filters': {}},
                 'expected': {'exception': 'ValueError', 'message_contains': 'non-empty dict', 'http_status': 400}},
            ],
        }

    def run_suite(self, **kwargs):
        return evaluate(self.suite, database=self.database, **kwargs)

    def mutate_document(self, change):
        with closing(sqlite3.connect(self.database)) as con, con:
            document = json.loads(con.execute('SELECT document FROM records').fetchone()[0])
            change(document)
            con.execute('UPDATE records SET document=?', (json.dumps(document),))

    def test_pass_does_not_invent_human_signoff(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        report = self.run_suite(source=self.root)
        self.assertTrue(report['passed'])
        self.assertFalse(report['human_signoff'])
        self.assertEqual(report['summary']['passed'], 2)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_wrong_value_fails_even_when_source_hash_metadata_unchanged(self):
        self.mutate_document(lambda doc: doc['fields']['properties'].update(value='Warm'))
        report = self.run_suite()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['summary']['failed'], 1)
        self.assertIn('properties:value', report['cases'][0]['error'])

    def test_wrong_pointer_fails(self):
        self.mutate_document(lambda doc: doc['fields']['properties']['source'].update(json_pointer='/data/0/value/0/value'))
        report = self.run_suite()
        self.assertFalse(report['passed'])
        self.assertIn('json_pointer', report['cases'][0]['error'])

    def test_database_snapshot_drift_blocks_cases(self):
        with closing(sqlite3.connect(self.database)) as con, con:
            con.execute("UPDATE records SET sha256='changed'")
        report = self.run_suite()
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['summary']['not_run'], 2)
        self.assertEqual(report['cases'], [])

    def test_raw_snapshot_drift_blocks_cases(self):
        self.path.write_text('{}')
        report = self.run_suite(source=self.root)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('source snapshot fingerprint', report['error'])

    def test_cli_returns_failure_code_for_wrong_values(self):
        self.mutate_document(lambda doc: doc['fields']['properties'].update(value='Warm'))
        suite_path = Path(self.tmp.name) / 'suite.json'
        suite_path.write_text(json.dumps(self.suite))
        report_path = Path(self.tmp.name) / 'report.json'
        result = subprocess.run([sys.executable, '-m', 'scripts.eval_tcm_golden', '--suite', str(suite_path),
                                 '--db', str(self.database), '--out', str(report_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(report_path.read_text())['summary']['failed'], 1)

    def test_http_validation_checks_contract_without_reusing_repository_message(self):
        suite = {**self.suite, 'cases': [self.suite['cases'][1]]}
        with patch('scripts.eval_tcm_golden.call_api', return_value=(400, {'status':'error', 'error':'provide at least one filter field'})):
            self.assertTrue(evaluate(suite, database=self.database, base_url='http://test')['passed'])
        with patch('scripts.eval_tcm_golden.call_api', return_value=(400, {'status':'ok'})):
            self.assertFalse(evaluate(suite, database=self.database, base_url='http://test')['passed'])



if __name__ == '__main__':
    unittest.main()
