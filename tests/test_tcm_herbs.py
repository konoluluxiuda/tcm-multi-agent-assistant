import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tcm.herbs import InvalidRecord, import_snapshot, lookup, parse_herb


class HerbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.herbs = self.root / 'etcm_herbs'
        self.herbs.mkdir(parents=True)
        self.db = Path(self.tmp.name) / 'herbs.sqlite3'
        self.obj = {'code': 1, 'data': [
            {'id': 'irrelevant', 'value': []},
            {'id': 'base_information', 'value': [
                {'key': 'Herb Name in Pinyin', 'value': ' HuangQin '},
                {'key': 'Herb Name in Latin', 'value': '<i>Scutellaria baicalensis</i>'},
                {'key': 'Property', 'value': 'UnknownTerm'},
                {'key': 'PubMed', 'value': ['123', '123']},
            ]}]}
        self.write('HuangQin', self.obj)

    def write(self, name, obj):
        path = self.herbs / (name + '.json')
        path.write_text(json.dumps(obj), encoding='utf-8')
        return path

    def ingest(self, version='v1'):
        return import_snapshot(self.root, self.db, version)

    def query(self, **kw):
        return lookup(self.db, **kw)

    def test_provenance_html_missing_and_unknown_values(self):
        self.ingest()
        result = self.query(name=' huangqin ')
        self.assertEqual(result['status'], 'ok')
        doc = result['records'][0]
        self.assertIsNone(doc['name_zh'])
        self.assertIsNone(doc['herb_id'])
        self.assertEqual(doc['fields']['flavors']['state'], 'missing')
        self.assertEqual(doc['fields']['properties']['value'], 'UnknownTerm')
        field = doc['fields']['latin_name']
        self.assertEqual(field['value'], 'Scutellaria baicalensis')
        self.assertEqual(field['source']['json_pointer'], '/data/1/value/1/value')
        self.assertEqual(field['source']['raw_value'], self.obj['data'][1]['value'][1]['value'])
        self.assertEqual(doc['fields']['reference_pmids']['value'], ['123'])
        self.assertEqual(self.query(name='SCUTELLARIA BAICALENSIS')['status'], 'ok')
        self.assertEqual(self.query(source_record_id=doc['source_record_id'])['status'], 'ok')

    def test_idempotence_and_source_unchanged(self):
        path = self.herbs / 'HuangQin.json'
        original = path.read_bytes()
        first, second = self.ingest(), self.ingest()
        self.assertEqual(first['inserted'], 1)
        self.assertEqual(second['unchanged'], 1)
        self.assertEqual(path.read_bytes(), original)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM records').fetchone()[0], 1)

    def test_duplicate_name_returns_candidates(self):
        self.write('HuangQin_', self.obj)
        self.assertEqual(self.ingest()['duplicate_pinyin_groups'], 1)
        result = self.query(name='HuangQin')
        self.assertEqual(result['status'], 'ambiguous_entity')
        self.assertEqual(len(result['records']), 2)
        self.assertNotEqual(*[r['source_record_id'] for r in result['records']])

    def test_changed_invalid_source_cannot_serve_old_valid_record(self):
        self.ingest()
        source_id = self.query(name='HuangQin')['records'][0]['source_record_id']
        self.write('HuangQin', {'code': 2, 'msg': 'No Data'})
        self.assertEqual(self.ingest()['quarantined'], 1)
        self.assertEqual(self.query(name='HuangQin')['status'], 'not_found')
        self.assertEqual(self.query(source_record_id=source_id)['status'], 'quarantined')

    def test_update_preserves_id_and_version_isolation(self):
        self.ingest()
        original = self.query(name='HuangQin')['records'][0]
        changed = copy.deepcopy(self.obj)
        changed['data'][1]['value'][0]['value'] = 'NewName'
        self.write('HuangQin', changed)
        self.assertEqual(self.ingest('v2')['inserted'], 1)
        self.assertEqual(self.query(name='NewName')['records'][0]['source_record_id'], original['source_record_id'])
        self.assertEqual(self.query(name='HuangQin', dataset_version='v1')['status'], 'ok')
        self.assertEqual(self.ingest('v1')['updated'], 1)
        self.assertEqual(self.query(name='HuangQin', dataset_version='v1')['status'], 'not_found')

    def test_removed_file_and_empty_snapshot_protection(self):
        self.write('Other', {'code': 2})
        self.ingest()
        source_id = self.query(name='HuangQin')['records'][0]['source_record_id']
        (self.herbs / 'HuangQin.json').unlink()
        self.assertEqual(self.ingest()['missing_from_snapshot'], 1)
        self.assertEqual(self.query(source_record_id=source_id)['status'], 'missing_from_snapshot')
        (self.herbs / 'Other.json').unlink()
        with self.assertRaises(ValueError):
            self.ingest()

    def test_query_boundaries(self):
        self.ingest()
        self.assertEqual(self.query(name='黄芩')['status'], 'unsupported_name_mapping')
        self.assertEqual(self.query(name="' OR 1=1 --")['status'], 'not_found')
        self.assertEqual(self.query(name='HuangQin', dataset_version='absent')['status'], 'dataset_not_found')
        with self.assertRaises(ValueError):
            self.query(name=' ')
        with self.assertRaises(sqlite3.OperationalError):
            lookup(Path(self.tmp.name) / 'missing.sqlite3', name='x')
        self.assertFalse((Path(self.tmp.name) / 'missing.sqlite3').exists())

    def test_invalid_shapes_duplicates_and_pmids(self):
        cases = [{'code': True}, {'code': 1, 'data': {}}, {'code': 1, 'data': []}]
        for value in (123, ['bad-id']):
            obj = copy.deepcopy(self.obj)
            obj['data'][1]['value'][3]['value'] = value
            cases.append(obj)
        duplicate = copy.deepcopy(self.obj)
        duplicate['data'][1]['value'].append(duplicate['data'][1]['value'][0])
        cases.append(duplicate)
        for obj in cases:
            with self.subTest(obj=obj), self.assertRaises(InvalidRecord):
                parse_herb(json.dumps(obj).encode(), 'etcm_herbs/test.json', 'v1')
        for raw in (b'{"code":1,"code":2}', b'{"code":NaN}'):
            with self.assertRaises(InvalidRecord):
                parse_herb(raw, 'etcm_herbs/test.json', 'v1')

    def test_io_failure_rolls_back_whole_batch(self):
        self.ingest()
        self.write('A', self.obj)
        old_read = Path.read_bytes
        def fail(path):
            if path.name == 'HuangQin.json':
                raise OSError('simulated read failure')
            return old_read(path)
        with patch.object(Path, 'read_bytes', fail), self.assertRaises(OSError):
            self.ingest()
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM records').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM batches').fetchone()[0], 1)
        self.assertEqual(self.query(name='HuangQin')['status'], 'ok')

    def test_database_cannot_be_written_into_source(self):
        with self.assertRaises(ValueError):
            import_snapshot(self.root, self.root / 'bad.sqlite3', 'v1')
        self.assertFalse((self.root / 'bad.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
