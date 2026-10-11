"""HTTP smoke tests for the lightweight herb query API (tcm/api.py)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

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
    os.environ['TCM_DB'] = str(db)
    os.environ['TCM_DATASET_VERSION'] = 'v1'

    from fastapi.testclient import TestClient
    from tcm import api
    _client = TestClient(api.app)


def tearDownModule():
    if _tmp is not None:
        _tmp.cleanup()


class ApiTests(unittest.TestCase):
    def test_health(self):
        self.assertEqual(_client.get('/health').json(), {'status': 'healthy'})

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


if __name__ == '__main__':
    unittest.main()
