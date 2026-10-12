"""HTTP smoke tests for the lightweight herb query API (tcm/api.py)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tcm.herbs import import_snapshot

_client = None
_tmp = None


def _obj(pinyin, **fields):
    obj = {'code': 1, 'data': [
        {'id': 'irrelevant', 'value': []},
        {'id': 'base_information', 'value': [
            {'key': 'Herb Name in Pinyin', 'value': pinyin},
            {'key': 'Herb Name in Latin', 'value': '<i>Scutellaria baicalensis</i>'},
        ]},
    ]}
    for key, value in fields.items():
        obj['data'][1]['value'].append({'key': key, 'value': value})
    return obj


def setUpModule():
    global _client, _tmp
    _tmp = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(_tmp.cleanup)
    root = Path(_tmp.name) / 'source'
    herbs = root / 'etcm_herbs'
    herbs.mkdir(parents=True)
    db = Path(_tmp.name) / 'herbs.sqlite3'
    for name, pinyin, fields in [
        ('HerbA', 'HerbA', {'Family': 'Leguminosae', 'Flavor': 'Bitter'}),
        ('HerbB', 'HerbB', {'Family': 'Leguminosae', 'Flavor': 'Sweet'}),
        ('HerbC', 'HerbC', {'Family': 'Apiaceae'}),
    ]:
        (herbs / (name + '.json')).write_text(json.dumps(_obj(pinyin, **fields)), encoding='utf-8')
    import_snapshot(root, db, 'v1')

    from fastapi.testclient import TestClient
    from tcm import api
    settings = patch.multiple(api, DB_PATH=str(db), DATASET_VERSION='v1')
    settings.start()
    unittest.addModuleCleanup(settings.stop)
    _client = TestClient(api.app)
    unittest.addModuleCleanup(_client.close)



class ApiTests(unittest.TestCase):
    def test_health(self):
        self.assertEqual(_client.get('/health').json(), {'status': 'healthy'})

    def test_readiness_uses_actual_dataset(self):
        result = _client.get('/ready')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'status': 'ready', 'dataset_version': 'v1', 'record_count': 3})
        from tcm import api
        with patch.object(api, 'DATASET_VERSION', 'absent'):
            missing = _client.get('/ready')
            self.assertEqual(missing.status_code, 503)
            self.assertEqual(missing.json()['status'], 'dataset_not_found')
        with patch.object(api, 'DB_PATH', '/nonexistent/herbs.sqlite3'):
            self.assertEqual(_client.get('/ready').status_code, 503)
            self.assertEqual(_client.get('/health').status_code, 200)

    def test_herb_detail(self):
        res = _client.get('/herbs/HerbA')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'ok')

    def test_filter(self):
        res = _client.get('/herbs', params={'family': 'Leguminosae'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['count'], 2)
        combined = _client.get('/herbs', params={'family': 'Leguminosae', 'flavors': 'bitter'})
        self.assertEqual(combined.json()['count'], 1)

    def test_compare(self):
        res = _client.get('/herbs/compare', params={'names': 'HerbA,HerbB,Missing'})
        body = res.json()
        self.assertEqual([h['status'] for h in body['herbs']], ['ok', 'ok', 'not_found'])

    def test_missing_db_returns_503(self):
        from tcm import api
        original = api.DB_PATH
        try:
            api.DB_PATH = '/nonexistent/herbs.sqlite3'
            res = _client.get('/herbs/HerbA')
            self.assertEqual(res.status_code, 503)
        finally:
            api.DB_PATH = original

    def test_invalid_filter_returns_400(self):
        self.assertEqual(_client.get('/herbs', params={}).status_code, 400)
        self.assertEqual(_client.get('/herbs', params={'name_pinyin': 'x'}).status_code, 400)


    def test_mixed_unknown_empty_and_duplicate_filters_rejected(self):
        for params in [
            {'family': 'Leguminosae', 'flavor': 'bitter'},
            {'family': 'Leguminosae', 'flavors': ''},
            {'family': 'Leguminosae', 'flavors': '   '},
            [('family', 'Leguminosae'), ('family', 'Apiaceae')],
        ]:
            with self.subTest(params=params):
                self.assertEqual(_client.get('/herbs', params=params).status_code, 400)

    def test_filter_and_compare_preserve_field_evidence(self):
        detail = _client.get('/herbs/HerbA').json()['records'][0]
        filtered = _client.get('/herbs', params={'flavors': 'Bitter'}).json()['records'][0]
        compared = _client.get('/herbs/compare', params={'names': 'HerbA'}).json()['herbs'][0]['record']
        for record in (filtered, compared):
            self.assertEqual(record['fields']['flavors'], detail['fields']['flavors'])
            self.assertEqual(record['source'], detail['source'])
            self.assertIn('source_content_unverified', record['warnings'])
            self.assertEqual(record['fields']['properties']['state'], 'missing')
        unresolved = _client.get('/herbs/compare', params={'names': '黄芩'}).json()['herbs'][0]
        self.assertEqual(unresolved['status'], 'unsupported_name_mapping')
        self.assertIn('chinese_names_unmapped', unresolved['warnings'])



    def test_pagination_keeps_total_and_nonoverlapping_records(self):
        first = _client.get('/herbs', params={'family': 'Leguminosae', 'limit': 1}).json()
        second = _client.get('/herbs', params={'family': 'Leguminosae', 'limit': 1, 'offset': first['next_offset'], 'dataset_version': first['dataset_version']}).json()
        self.assertEqual(first['count'], 2)
        self.assertEqual(first['returned_count'], 1)
        self.assertEqual(second['offset'], 1)
        self.assertIsNone(second['next_offset'])
        self.assertNotEqual(first['records'][0]['source_record_id'], second['records'][0]['source_record_id'])
        beyond = _client.get('/herbs', params={'family': 'Leguminosae', 'offset': 20}).json()
        self.assertEqual(beyond['count'], 2)
        self.assertEqual(beyond['records'], [])

    def test_invalid_page_bounds(self):
        for params in [{'limit': 0}, {'limit': 101}, {'offset': -1}, {'limit': 'bad'}]:
            with self.subTest(params=params):
                self.assertEqual(_client.get('/herbs', params={'family': 'x', **params}).status_code, 422)
        self.assertEqual(_client.get('/herbs', params=[('family', 'x'), ('limit', 1), ('limit', 2)]).status_code, 400)

    def test_source_record_route_and_explicit_version(self):
        detail = _client.get('/herbs/HerbA').json()['records'][0]
        res = _client.get('/herb-records/' + detail['source_record_id'], params={'dataset_version': 'v1'}).json()
        self.assertEqual(res['records'][0], detail)
        self.assertEqual(_client.get('/herb-records/missing').json()['status'], 'not_found')
        for path, params in [('/herbs/HerbA', {}), ('/herb-records/' + detail['source_record_id'], {}), ('/herbs/compare', {'names':'HerbA'}), ('/herbs', {'family':'Leguminosae'})]:
            with self.subTest(path=path):
                self.assertEqual(_client.get(path, params={**params, 'dataset_version': 'absent'}).json()['status'], 'dataset_not_found')


if __name__ == '__main__':
    unittest.main()
