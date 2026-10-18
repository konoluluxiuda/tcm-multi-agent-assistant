"""Literature scope, provider failures and persistence must remain distinguishable."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from tcm.literature_store import LiteratureStore
from tcm.pubmed import PubMedClient


def article(pmid='123', abstract=True):
    return f'''<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>
    <ArticleTitle>Example <i>herbal</i> study</ArticleTitle>
    <Journal><Title>Fixture journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
    {'<Abstract><AbstractText Label="RESULTS">Fixture result, not clinical advice.</AbstractText></Abstract>' if abstract else ''}
    <PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList>
    </Article></MedlineCitation><PubmedData><ArticleIdList>
    <ArticleId IdType="doi">10.0000/fixture</ArticleId><ArticleId IdType="pmc">PMC123</ArticleId>
    </ArticleIdList></PubmedData></PubmedArticle>'''


def client(ids=None, xml=None, count=None):
    ids = ['123'] if ids is None else ids
    data = {'esearchresult': {'count': str(len(ids) if count is None else count), 'idlist': ids,
                             'querytranslation': 'fixture translation'}}
    def transport(endpoint, params):
        if endpoint == 'esearch.fcgi':
            return json.dumps(data).encode()
        return (xml if xml is not None else '<PubmedArticleSet>' + article() + '</PubmedArticleSet>').encode()
    return PubMedClient(transport)


class PubMedTests(unittest.TestCase):
    def test_metadata_and_abstract_keep_scope_and_source(self):
        result = client().search('Astragalus[Title/Abstract]')
        self.assertEqual(result['status'], 'ok')
        record = result['articles'][0]
        self.assertEqual(record['title'], 'Example herbal study')
        self.assertEqual(record['publication_types'], ['Review'])
        self.assertEqual(record['abstract'][0]['label'], 'RESULTS')
        self.assertEqual(record['doi'], '10.0000/fixture')
        self.assertEqual(record['evidence_scope'], 'abstract')
        self.assertFalse(record['full_text_retrieved'])
        self.assertEqual(len(result['response_sha256']), 2)

    def test_no_abstract_is_not_fulltext_even_with_pmc_link(self):
        record = client(xml='<PubmedArticleSet>' + article(abstract=False) + '</PubmedArticleSet>').search('x')['articles'][0]
        self.assertEqual(record['evidence_scope'], 'metadata_only')
        self.assertEqual(record['pmc_id'], 'PMC123')
        self.assertFalse(record['full_text_retrieved'])

    def test_zero_count_is_only_no_results_case(self):
        self.assertEqual(client(ids=[]).search('x')['status'], 'no_results')
        self.assertEqual(client(ids=[], count=5).search('x')['error_code'], 'invalid_response')

    def test_missing_records_are_explicit_partial_not_success(self):
        result = client(ids=['123', '456']).search('x')
        self.assertEqual(result['status'], 'partial_fetch')
        self.assertEqual(result['missing_pmids'], ['456'])

    def test_wrong_and_duplicate_pmids_are_rejected(self):
        for xml in ('<PubmedArticleSet>' + article('999') + '</PubmedArticleSet>',
                    '<PubmedArticleSet>' + article() * 2 + '</PubmedArticleSet>'):
            result = client(xml=xml).search('x')
            self.assertEqual(result['status'], 'error')
            self.assertFalse(result['articles'])

    def test_rate_limit_timeout_and_network_errors_do_not_leak_body(self):
        for error, code in [(HTTPError('https://secret', 429, 'secret', None, None), 'rate_limited'),
                            (TimeoutError('secret'), 'timeout'), (URLError('secret'), 'network_error'),
                            (URLError(TimeoutError('secret')), 'timeout')]:
            def fail(endpoint, params):
                raise error
            result = PubMedClient(fail).search('x')
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['error_code'], code)
            self.assertNotIn('secret', json.dumps(result))

    def test_malformed_provider_data_is_not_empty_search(self):
        for body in (b'<html>blocked</html>', b'{"error":"secret"}',
                     b'{"esearchresult":{"count":"x","idlist":[]}}'):
            result = PubMedClient(lambda endpoint, params: body).search('x')
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(client(xml='<broken').search('x')['error_code'], 'invalid_response')

    def test_validation_precedes_network(self):
        def forbidden(*args):
            self.fail('Invalid input must not request network')
        for query, limit in [('', 5), ('x' * 2001, 5), ('x', 21), ('x', True)]:
            with self.assertRaises(ValueError):
                PubMedClient(forbidden).search(query, limit)

    def test_search_order_and_exact_query_preserved(self):
        calls = []
        def transport(endpoint, params):
            calls.append((endpoint, params))
            if endpoint == 'esearch.fcgi':
                return b'{"esearchresult":{"count":"2","idlist":["456","123"]}}'
            return ('<PubmedArticleSet>' + article() + article('456') + '</PubmedArticleSet>').encode()
        query = 'Astragalus[Title/Abstract] AND randomized controlled trial[Publication Type]'
        result = PubMedClient(transport).search(query, 2)
        self.assertEqual(calls[0][1]['term'], query)
        self.assertEqual(calls[1][1]['id'], '456,123')
        self.assertEqual([a['pmid'] for a in result['articles']], ['456', '123'])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'literature.sqlite3'

    def open(self):
        store = LiteratureStore(self.path)
        self.addCleanup(store.close)
        return store

    def test_dedup_does_not_change_historical_snapshot(self):
        store = self.open()
        original = client().search('first')
        run = store.save(original)
        revised = deepcopy(original)
        revised['articles'][0]['title'] = 'Revised title'
        store.save(revised)
        self.assertEqual(store.db.execute('SELECT count(*) FROM articles').fetchone()[0], 1)
        self.assertEqual(store.db.execute('SELECT count(*) FROM run_articles').fetchone()[0], 2)
        reopened = self.open()
        self.assertEqual(reopened.get(run), original)
        self.assertIn('Example', json.loads(reopened.db.execute(
            'SELECT payload FROM run_articles WHERE run_id=?', (run,)).fetchone()[0])['title'])

    def test_failed_and_empty_runs_remain_distinct(self):
        store = self.open()
        empty = client(ids=[]).search('zero')
        failed = PubMedClient(lambda *args: b'not json').search('failed')
        self.assertEqual(store.get(store.save(empty))['status'], 'no_results')
        self.assertEqual(store.get(store.save(failed))['status'], 'error')
        self.assertIsNone(store.get("' OR 1=1 --"))

    def test_failed_save_rolls_back_whole_run(self):
        store = self.open()
        result = client().search('x')
        result['articles'] *= 2
        with self.assertRaises(sqlite3.IntegrityError):
            store.save(result)
        for table in ('search_runs', 'articles', 'run_articles'):
            self.assertEqual(store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_unknown_schema_and_unversioned_foreign_database_rejected(self):
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            LiteratureStore(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA user_version=0')
            db.execute('CREATE TABLE private_data(secret TEXT)')
        with self.assertRaises(ValueError):
            LiteratureStore(self.path)

    def test_other_app_schema_v1_rejected_without_changes(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE conversations(payload TEXT)')
            db.execute('PRAGMA user_version=1')
        with self.assertRaises(ValueError):
            LiteratureStore(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [('conversations',)])

    def test_cli_replays_persisted_snapshot_without_provider(self):
        store = self.open()
        run = store.save(client().search('fixture only'))
        proc = subprocess.run([sys.executable, '-m', 'scripts.search_pubmed', '--db', str(self.path),
                               '--run-id', run], text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['run_id'], run)


if __name__ == '__main__':
    unittest.main()
