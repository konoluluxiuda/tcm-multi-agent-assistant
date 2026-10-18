"""Synthetic checks for lexical retrieval, ambiguity and read-only source handling."""
import json
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever, resolve_names


def fixture(directory):
    evidence = [{'block_id': 'test:1', 'chapter_page': 1,
                 'source': {'source_document': 'test', 'source_pdf_page': 3, 'source_sha256': 'a' * 64},
                 'bbox_0_1000': [100, 200, 400, 230]}]
    def field(label, text):
        return {'scope': 'monograph', 'label': label, 'text': text, 'raw_text': text,
                'overlay_ids': [], 'evidence': evidence}
    entries = []
    for i, name in enumerate(('示例叶', '示例叶炭')):
        fields = [field('用法与用量', '【用法与用量】测试用量。'), field('功能与主治', '【功能与主治】测试用途。')]
        if i == 0:fields.append(field('用法与用量', '【用法与用量】另一炮制品用量。'))
        entries.append({'id': f'test-{i}', 'name_zh_candidate': name, 'raw_name_zh_candidate': name,
                        'fields': fields, 'human_signoff': False, 'production_eligible': False,
                        'raw_flags': [], 'applied_overlay_ids': [], 'review_status': 'pending_human_review',
                        'title_evidence': evidence})
    directory.mkdir()
    for name,value in [('research_view.json', {'human_signoff': False, 'production_eligible': False, 'entries': entries}),
                       ('revision_ledger.json', {})]:
        (directory/name).write_text(json.dumps(value, ensure_ascii=False))
    (directory/'summary.json').write_text(json.dumps({'research_view_sha256': digest(directory/'research_view.json'),
                                                     'ledger_sha256': digest(directory/'revision_ledger.json')}))


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory();cls.root = Path(cls.temp.name)
        fixture(cls.root/'source');build(cls.root/'source',cls.root/'index')

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def setUp(self):self.retriever = ResearchRetriever(self.root/'index')
    def tearDown(self):self.retriever.close()

    def test_longest_name_wins_without_discarding_separate_entities(self):
        names = ['示例叶','示例叶炭']
        self.assertEqual(resolve_names('示例叶炭的功效',names),['示例叶炭'])
        self.assertEqual(resolve_names('示例叶和示例叶炭的区别',names),names)

    def test_routed_query_returns_field_and_source(self):
        r = self.retriever.query('示例叶炭有什么功效？')
        self.assertEqual(r['status'],'candidates')
        self.assertEqual(r['hits'][0]['label'],'功能与主治')
        self.assertEqual(r['hits'][0]['evidence'][0]['source']['source_pdf_page'],3)
        self.assertFalse(r['hits'][0]['production_eligible'])
        self.assertFalse(r['answer_generated'])

    def test_repeated_field_is_reported_as_ambiguous(self):
        r=self.retriever.query('示例叶的用量是多少？')
        self.assertEqual(r['status'],'ambiguous_field');self.assertEqual(len(r['hits']),2)

    def test_unsupported_or_absent_queries_are_distinguished(self):
        for q,status in [('未知药的功效','name_not_resolved'),('示例叶价格','field_not_resolved'),
                         ('示例叶注意事项','field_not_observed'),('示例叶与示例叶炭的功效','multiple_names')]:
            with self.subTest(q=q):
                result=self.retriever.query(q);self.assertEqual(result['status'],status);self.assertEqual(result['hits'],[])

    def test_explicit_filters_do_not_guess_aliases(self):
        r=self.retriever.query('',name='示例叶炭',field='功能与主治')
        self.assertEqual(len(r['hits']),1)
        self.assertEqual(self.retriever.query('',name='示例',field='功能与主治')['status'],'name_not_resolved')

    def test_fts_quotes_do_not_change_sql_or_data(self):
        result=self.retriever.query('"示例叶炭" OR 1=1; DROP TABLE chunks;',method='bm25')
        self.assertIsInstance(result['hits'],list)
        self.assertEqual(self.retriever.connection.execute('SELECT count(*) FROM chunks').fetchone()[0],5)

    def test_invalid_limits_and_write_attempts_are_rejected(self):
        for k in (0,21,True):
            with self.assertRaises(ValueError):self.retriever.query('示例叶',k=k)
        with self.assertRaises(ValueError):self.retriever.query('药'*501)
        with self.assertRaises(sqlite3.OperationalError):self.retriever.connection.execute('DELETE FROM chunks')

    def test_changed_view_is_rejected_before_index_creation(self):
        source=self.root/'changed';fixture(source)
        (source/'research_view.json').write_text('{}')
        with self.assertRaises(ValueError):build(source,self.root/'bad-index')
        self.assertFalse((self.root/'bad-index').exists())

    def test_changed_index_is_rejected_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'index';shutil.copytree(self.root/'index',target)
            path=target/'research.sqlite3';path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaises(ValueError):ResearchRetriever(target)


if __name__ == '__main__':unittest.main()
