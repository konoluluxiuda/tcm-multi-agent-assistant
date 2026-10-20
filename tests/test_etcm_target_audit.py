"""Duplicate pages and mismatched target filenames must never manufacture graph edges."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.audit_etcm_targets import audit


class TargetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root/'source'
        (self.source/'etcm_targets').mkdir(parents=True)
        self.database = self.root/'enrichment.sqlite3'
        with sqlite3.connect(self.database) as con:
            con.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
                              'CREATE TABLE nodes(node_id TEXT,identifier TEXT);'
                              'CREATE TABLE edges(node_id TEXT,kind TEXT,raw_row TEXT);')
            meta = {'kind': 'etcm_enrichment_v1', 'build_complete': True, 'dataset_version': 'v1', 'source_fingerprint': 'herb-source-sha'}
            con.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in meta.items()])
            con.execute('INSERT INTO nodes VALUES (?,?)', ('n1', 'P12345'))
            row = {'Target Name': ['Example target'], 'Gene Symbol': ['EXA'], 'UniProt Accession Number': ['P12345']}
            con.execute('INSERT INTO edges VALUES (?,?,?)', ('n1', 'target', json.dumps(row)))
        self.document = {'code': 1, 'data': [
            {'id': 'base_information', 'value': [{'key': 'Target Name', 'value': 'Example target'},
                {'key': 'Gene Symbol', 'value': ['EXA']}, {'key': 'UniProt Accession Number', 'value': ['P12345']}]},
            {'id': 'related_table', 'value': [{'type': 'ingredient', 'value': [
                {'Ingredient Name': ['Synthetic compound'], 'Molecular Formula': 'C2H6', 'Molecular Weight': '30.07'}]}]}],
            'complete_fetch_summary': [{'section': 'related_table', 'type': 'ingredient', 'status': 'partial'}]}
        self.file = self.source/'etcm_targets/Example_target.json'; self.file.write_text(json.dumps(self.document))
        self.copy = self.source/'etcm_targets/Wrong_target.json'; self.copy.write_bytes(self.file.read_bytes())

    def run_audit(self, name='out'):
        with redirect_stdout(io.StringIO()):
            result = audit(self.source, self.database, self.root/name)
        report = json.loads((self.root/name/'report.json').read_text())
        files = json.loads((self.root/name/'files.json').read_text())
        rows = [json.loads(line) for line in (self.root/name/'target_ingredients.jsonl').read_text().splitlines()]
        return result, report, files, rows

    def test_duplicate_payloads_export_once_and_wrong_filename_is_isolated(self):
        before = self.file.read_bytes(); db_before = self.database.read_bytes()
        result, report, files, rows = self.run_audit()
        self.assertEqual((result['total_files'], result['distinct_file_hashes'], result['duplicate_file_copies']), (2, 1, 1))
        self.assertEqual(result['eligible_target_contents'], 1)
        self.assertEqual(result['exported_target_ingredient_rows'], 1)
        self.assertEqual(files[1]['status'], 'filename_internal_identity_mismatch')
        self.assertEqual(rows[0]['source_file'], 'etcm_targets/Example_target.json')
        self.assertEqual(rows[0]['source_file_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(rows[0]['capture_status'][0]['status'], 'partial')
        value = self.document
        for token in rows[0]['json_pointer'].split('/')[1:]:
            value = value[int(token)] if isinstance(value, list) else value[token]
        self.assertEqual(value, rows[0]['raw_row'])
        self.assertFalse(rows[0]['ingredient_identity_verified'])
        self.assertEqual(self.file.read_bytes(), before); self.assertEqual(self.database.read_bytes(), db_before)

    def test_payload_without_a_matching_primary_filename_is_not_exported(self):
        self.file.unlink()
        result, report, _, rows = self.run_audit()
        self.assertEqual(report['contents'][0]['status'], 'filename_unresolved')
        self.assertEqual(result['eligible_target_contents'], 0); self.assertEqual(rows, [])

    def test_reference_gene_conflict_or_missing_identifier_prevents_export(self):
        self.copy.unlink()
        self.document['data'][0]['value'][1]['value'] = ['WRONG']
        self.file.write_text(json.dumps(self.document))
        _, report, _, rows = self.run_audit()
        self.assertEqual(report['contents'][0]['status'], 'reference_conflict'); self.assertFalse(rows)
        self.document['data'][0]['value'][2]['value'] = []
        self.file.write_text(json.dumps(self.document))
        _, report, _, rows = self.run_audit('incomplete')
        self.assertEqual(report['contents'][0]['status'], 'identity_incomplete'); self.assertFalse(rows)

    def test_unreferenced_identity_and_ambiguous_ingredient_are_not_silently_linked(self):
        self.copy.unlink()
        self.document['data'][0]['value'][2]['value'] = ['Q12345']
        self.file.write_text(json.dumps(self.document))
        _, report, _, rows = self.run_audit()
        self.assertEqual(report['contents'][0]['status'], 'unreferenced_identity'); self.assertFalse(rows)
        self.document['data'][0]['value'][2]['value'] = ['P12345']
        self.document['data'][1]['value'][0]['value'][0]['Ingredient Name'] = ['A', 'B']
        self.file.write_text(json.dumps(self.document))
        _, report, _, rows = self.run_audit('ambiguous')
        self.assertEqual(report['contents'][0]['malformed_ingredient_rows'], 1); self.assertFalse(rows)

    def test_same_identifier_and_gene_with_other_names_is_not_called_a_gene_conflict(self):
        with sqlite3.connect(self.database) as con:
            row = {'Target Name': ['Other source name'], 'Gene Symbol': ['EXA'], 'UniProt Accession Number': ['P12345']}
            con.execute('INSERT INTO edges VALUES (?,?,?)', ('n1', 'target', json.dumps(row)))
        _, report, _, rows = self.run_audit()
        content = report['contents'][0]
        self.assertEqual(content['status'], 'reference_name_variation')
        self.assertEqual(content['reference_conflicting_rows'], 0)
        self.assertEqual(content['reference_name_variant_rows'], 1)
        self.assertFalse(rows)

    def test_oversize_changed_payload_and_outside_symlink_are_rejected(self):
        with patch('scripts.audit_etcm_targets.MAX_DOCUMENT_BYTES', 10):
            _, report, _, rows = self.run_audit()
        self.assertEqual(report['contents'][0]['status'], 'invalid_or_oversized'); self.assertFalse(rows)
        with patch('scripts.audit_etcm_targets.digest', return_value=('f'*64, self.file.stat().st_size)):
            _, report, _, rows = self.run_audit('changed')
        self.assertEqual(report['contents'][0]['status'], 'invalid_or_oversized'); self.assertFalse(rows)
        self.copy.unlink(); self.copy.symlink_to(self.database)
        result, _, files, _ = self.run_audit('symlink')
        self.assertEqual(result['file_status_counts']['outside_snapshot'], 1)

    def test_existing_or_in_source_output_is_rejected(self):
        self.run_audit()
        with self.assertRaises(FileExistsError):audit(self.source, self.database, self.root/'out')
        with self.assertRaises(ValueError):audit(self.source, self.database, self.source/'out')


if __name__ == '__main__':
    unittest.main()
