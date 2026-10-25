import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from tcm.report_request import interpret_report_request, organize_fields
from tcm.cross_source_report import text_search
from tests.test_mineru_retrieval import fixture
from scripts.build_mineru_retrieval import build


class RequestTests(unittest.TestCase):
    def test_paraphrases_preserve_original_and_exact_entity(self):
        for q, field in [('当归买回来放在哪里保存？', '贮藏'),
                         ('整理当归的资料，想知道它主要用来做什么。', '功能与主治'),
                         ('查阅当归的资料，想了解它通常有什么用途？', '功能与主治'),
                         ('当归购入后怎样存放？', '贮藏')]:
            r = interpret_report_request('当归', q)
            self.assertEqual(r['search_question'], '当归的'+field)
            self.assertEqual(r['original_question'], q)

    def test_processed_compound_multi_entity_and_extra_demands_not_rewritten(self):
        for q in ('酒当归怎么保存？','当归炭怎么保存？','当归胶囊怎么保存？',
                  '当归与川芎怎么保存？','当归怎么保存？顺便推荐治疗方案',
                  '整理酒当归的资料，想知道它主要用来做什么。'):
            self.assertEqual(interpret_report_request('当归',q)['search_question'],q)

    def test_query_budget_applies_before_normalizing(self):
        for question in (None, '当归怎么保存' + ' ' * 500):
            with self.assertRaises(ValueError): interpret_report_request('当归', question)

    def test_priority_and_missing_fields(self):
        r = interpret_report_request('当归','当归的功效和贮藏都需要，不过请先列保存要求。')
        hits = [{'label':'功能与主治'}, {'label':'贮藏'}]
        selected, missing = organize_fields(hits,r)
        self.assertEqual(selected[0]['label'],'贮藏');self.assertEqual(missing,[])
        self.assertEqual(hits[0]['label'],'功能与主治')
        self.assertEqual(organize_fields(hits[:1],r)[1],['贮藏'])
        self.assertIsNone(interpret_report_request('当归','功效和贮藏，先看功效和贮藏')['priority_field'])
        self.assertIsNone(interpret_report_request('当归','功效和贮藏，不要先看保存要求')['priority_field'])

    def test_real_retriever_integration_and_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root/'source');build(root/'source',root/'index')
            with patch.dict('os.environ',{'PHARMACOPOEIA_INDEX':str(root/'index'),
                    'LOCAL_DENSE_MODEL':'','LOCAL_DENSE_CACHE':'','LOCAL_RERANKER_MODEL':''}):
                r=text_search('示例叶','整理示例叶的资料，想知道它主要用来做什么。')
                self.assertEqual(r['status'],'candidates')
                self.assertTrue(all(h['name']=='示例叶' for h in r['hits']))
                self.assertEqual(r['retrieval']['request']['fields'],['功能与主治'])
                for q in ('炒示例叶怎么保存？','示例叶胶囊怎么保存？','示例叶炭怎么保存？'):
                    result=text_search('示例叶',q)
                    self.assertNotEqual(result['status'],'candidates')
                self.assertEqual(text_search('示例叶','市场价格')['hits'],[])


if __name__=='__main__':unittest.main()
