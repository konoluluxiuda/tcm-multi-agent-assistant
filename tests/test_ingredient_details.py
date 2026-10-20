"""Ingredient details require internal field agreement and original source ownership."""
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_enrichment import build
from tcm.herbs import import_snapshot, lookup
from tcm.ingredient_details import details, inspect_candidate


class IngredientDetailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root/'source'
        (self.source/'etcm_herbs').mkdir(parents=True); (self.source/'etcm_ingredients').mkdir()
        self.row = {'Ingredient Name': ['Compound (+)'], 'Molecular Formula': 'C2H6', 'Molecular Weight': '30.070'}
        self.herb = self.source/'etcm_herbs/Alpha.json'
        self.herb.write_text(json.dumps({'code': 1, 'data': [
            {'id': 'base_information', 'value': [{'key': 'Herb Name in Pinyin', 'value': 'Alpha'}]},
            {'id': 'related_table', 'value': [{'type': 'ingredient', 'value': [self.row]}]}]}))
        self.relative = 'etcm_ingredients/Compound_(+).json'; self.file = self.source/self.relative
        self.document = {'code': 1, 'data': [{'id': 'base_information', 'children': [{'value': [
            {'key': 'Component Name IN English', 'value': 'Compound (+)'},
            {'key': 'Molecular Formula', 'value': 'C2H6'}, {'key': 'Molecular Weight', 'value': '30.07'},
            {'key': 'CAS Number', 'value': 'synthetic-cas'}, {'key': 'PubChem', 'value': ['123', '456']}]}]},
            {'id': 'basic_network', 'value': {'nodes': [{'label': 'Target'}], 'edges': []}}]}
        self.file.write_text(json.dumps(self.document))
        self.base = self.root/'herbs.sqlite3'; import_snapshot(self.source, self.base, 'v1')
        with redirect_stdout(io.StringIO()):build(self.source, self.base, 'v1', self.root/'derived')
        self.database = self.root/'derived/enrichment.sqlite3'
        self.record = lookup(self.base, name='Alpha', dataset_version='v1')['records'][0]
        with sqlite3.connect(self.database) as con:self.edge = con.execute('SELECT edge_id FROM edges').fetchone()[0]

    def get(self):
        return details(self.database, self.source, self.record, 'v1', self.edge)

    def test_internal_agreement_preserves_identifiers_and_both_source_pointers(self):
        before = hashlib.sha256(self.base.read_bytes()).hexdigest()
        result = self.get(); candidate = result['candidates'][0]
        self.assertEqual(candidate['status'], 'source_fields_consistent_not_verified')
        self.assertFalse(candidate['identity_verified'])
        self.assertEqual(candidate['identifiers']['pubchem'][0]['value'], ['123', '456'])
        self.assertEqual(result['herb_json_pointer'], '/data/1/value/0/value/0')
        self.assertEqual(candidate['identifiers']['cas'][0]['json_pointer'], '/data/0/children/0/value/3/value')
        self.assertEqual(candidate['file_sha256'], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), before)

    def test_name_stereochemistry_and_formula_conflicts_do_not_publish_identifiers(self):
        for index, value in [(0, 'Compound (-)'), (1, 'C2H8'), (2, '31.07')]:
            with self.subTest(index=index):
                document = copy.deepcopy(self.document); document['data'][0]['children'][0]['value'][index]['value'] = value
                self.file.write_text(json.dumps(document))
                candidate = self.get()['candidates'][0]
                self.assertEqual(candidate['status'], 'source_fields_inconsistent')
                self.assertNotIn('identifiers', candidate)

    def test_missing_and_duplicate_fields_stay_unresolved(self):
        document = copy.deepcopy(self.document)
        document['data'][0]['children'][0]['value'].pop(2)
        self.file.write_text(json.dumps(document))
        self.assertEqual(self.get()['candidates'][0]['status'], 'insufficient_identity_fields')
        document = copy.deepcopy(self.document)
        document['data'][0]['children'][0]['value'].append({'key': 'Molecular Formula', 'value': 'C2H6'})
        self.file.write_text(json.dumps(document))
        self.assertEqual(self.get()['candidates'][0]['status'], 'insufficient_identity_fields')

    def test_no_direct_targets_are_inferred_from_network_or_herb_targets(self):
        candidate = self.get()['candidates'][0]
        self.assertEqual(candidate['target_links_status'], 'not_recorded')
        self.assertEqual(candidate['target_tables'], [])
        document = copy.deepcopy(self.document)
        document['data'].append({'id': 'related_table', 'value': [{'type': 'target', 'value': [
            {'Target Name': ['Synthetic target'], 'UniProt Accession Number': ['P12345']}]*12}]})
        document['complete_fetch_summary'] = [{'section': 'related_table', 'type': 'target', 'status': 'partial'}]
        self.file.write_text(json.dumps(document))
        table = self.get()['candidates'][0]['target_tables'][0]
        self.assertEqual((table['count'], len(table['rows']), table['truncated']), (12, 10, True))
        self.assertEqual(table['capture_status'][0]['status'], 'partial')
        self.assertEqual(table['rows'][0]['json_pointer'], '/data/2/value/0/value/0')

    def test_source_changes_and_wrong_row_or_version_are_rejected(self):
        with self.assertRaises(ValueError):details(self.database, self.source, self.record, 'v2', self.edge)
        with self.assertRaises(ValueError):details(self.database, self.source, self.record, 'v1', 'source-row:'+'f'*64)
        self.herb.write_text(self.herb.read_text()+'\n')
        with self.assertRaises(ValueError):self.get()

    def test_path_escape_and_oversize_cannot_read_unbounded_files(self):
        outside = self.root/'outside.json'; outside.write_text(json.dumps(self.document))
        self.file.unlink(); self.file.symlink_to(outside)
        self.assertEqual(self.get()['candidates'][0]['status'], 'source_unavailable_or_oversized')
        self.file.unlink(); self.file.write_text(json.dumps(self.document))
        with patch('tcm.ingredient_details.MAX_BYTES', 10), self.assertRaises(ValueError):
            inspect_candidate(self.source, self.relative, self.row)

    def test_api_requires_configured_sources_and_rejects_arbitrary_paths(self):
        import app
        from fastapi.testclient import TestClient
        env = {'TCM_DB': str(self.base), 'TCM_DATASET_VERSION': 'v1',
               'ETCM_ENRICHMENT_DB': str(self.database), 'ETCM_SOURCE_ROOT': str(self.source)}
        params = {'source_record_id': self.record['source_record_id'], 'edge_id': self.edge}
        with patch.dict(os.environ, env), TestClient(app.app) as client:
            result = client.get('/api/etcm/ingredient', params=params)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers['cache-control'], 'no-store')
            self.assertEqual(result.json()['llm_calls'], 0)
            self.assertEqual(client.get('/api/etcm/ingredient', params={**params, 'file': '../outside.json'}).status_code, 400)
            self.assertEqual(client.get('/api/etcm/ingredient', params={**params, 'edge_id': 'invalid'}).status_code, 409)
            with patch.dict(os.environ, {'ETCM_SOURCE_ROOT': ''}):
                self.assertEqual(client.get('/api/etcm/ingredient', params=params).status_code, 503)


if __name__ == '__main__':
    unittest.main()
