"""Entity/field filters must apply before lexical ranking and top-k truncation."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever
from tests.test_mineru_retrieval import fixture


class ScopedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.root=root;source=root/'view';fixture(source)
        path=source/'research_view.json';view=json.loads(path.read_text())
        target=view['entries'][1]
        prototype=target['fields'][1]
        target['fields'].append({**prototype,'label':'注意','text':'【注意】功效。'*10,'raw_text':'【注意】功效。'*10})
        target['fields'].append({**prototype,'scope':'appendix:test','text':'【功能与主治】测试用途。'*10})
        # Many out-of-scope records compete for the same lexical terms.
        for i in range(8):
            view['entries'].append({**target,'id':'distractor-'+str(i),'name_zh_candidate':'无关药材'+str(i),
                                    'fields':[{**prototype,'text':'【功能与主治】测试用途功效。'*10}]})
        path.write_text(json.dumps(view,ensure_ascii=False))
        summary=json.loads((source/'summary.json').read_text());summary['research_view_sha256']=digest(path)
        (source/'summary.json').write_text(json.dumps(summary));build(source,root/'index')
        self.r=ResearchRetriever(root/'index');self.addCleanup(self.r.close)

    def test_body_terms_work_without_a_field_alias_and_never_leave_entity(self):
        question='示例叶炭的测试用途'
        self.assertEqual(self.r.query(question)['status'],'field_not_resolved')
        result=self.r.query(question,method='name_bm25',k=1)
        self.assertEqual(result['status'],'candidates')
        self.assertEqual(len(result['hits']),1)
        self.assertEqual(result['hits'][0]['name'],'示例叶炭')
        self.assertEqual(result['hits'][0]['label'],'功能与主治')
        self.assertFalse(result['hits'][0]['scope'].startswith('appendix:'))

    def test_known_field_and_appendix_filters_apply_before_limit(self):
        result=self.r.query('示例叶炭的测试用途功效',method='name_bm25',k=1)
        self.assertEqual(result['status'],'candidates')
        self.assertEqual(result['hits'][0]['entry_id'],'test-1')
        self.assertEqual(result['hits'][0]['label'],'功能与主治')
        self.assertEqual(result['hits'][0]['scope'],'monograph')
        self.assertIn('before_limit',result['query_interpretation']['ranking'])

    def test_absent_entity_field_and_unknown_topic_do_not_trigger_global_fallback(self):
        for question,status in [('未知药的功效','name_not_resolved'),('示例叶炭的贮藏','field_not_observed'),
                                ('示例叶炭的市场价格','field_not_resolved'),('示例叶炭','field_not_resolved'),
                                ('示例叶和示例叶炭的功效','multiple_names'),('炙示例叶的功效','variant_scope_unresolved')]:
            with self.subTest(question=question):
                result=self.r.query(question,method='name_bm25')
                self.assertEqual(result['status'],status);self.assertEqual(result['hits'],[])

    def test_duplicate_field_warning_survives_top_one_ranking(self):
        for question in ('示例叶的用量','示例叶的另一记载'):
            result=self.r.query(question,method='name_bm25',k=1)
            self.assertEqual(result['status'],'ambiguous_field')
            self.assertEqual(result['field_occurrences'],2)
            self.assertEqual(len(result['hits']),1)
            self.assertEqual(result['hits'][0]['name'],'示例叶')

    def test_explicit_filters_cannot_silently_change_bm25_semantics(self):
        with self.assertRaises(ValueError):
            self.r.query('测试用途',method='name_bm25',name='示例叶炭')
        with self.assertRaises(ValueError):
            self.r.query('示例叶炭的功效',method='name_bm25',k=True)

    def test_comparison_reports_entity_scope_and_rejects_wrong_reference_view(self):
        from scripts.eval_mineru_retrieval import evaluate
        question=self.root/'questions.json';reference=self.root/'reference.json'
        question.write_text(json.dumps({'k':5,'cases':[{'id':'one','group':'body_term_diagnostic','question':'示例叶炭的测试用途'}]}))
        view=self.root/'view/research_view.json'
        ref={'question_sha256':digest(question),'source_view_sha256':digest(view),'limits':['synthetic reference'],
             'references':[{'case_id':'one','entry_id':'test-1','field_index':1,'field_occurrences':1,
                'name':'示例叶炭','source_document':'test','source_pdf_page':3,'source_sha256':'a'*64,
                'coarse_region_0_1000':[100,200,400,230]}]}
        reference.write_text(json.dumps(ref))
        result=evaluate(self.root/'index',question,reference,view,self.root/'comparison',methods=['name_bm25'])
        score=result['results']['name_bm25']['body_term_diagnostic']
        self.assertEqual(score['evidence_hit_at_5'],1)
        self.assertEqual(score['top1_wrong_entity_count'],0)
        self.assertEqual(result['external_calls'],0)
        ref['source_view_sha256']='wrong';reference.write_text(json.dumps(ref))
        with self.assertRaises(ValueError):
            evaluate(self.root/'index',question,reference,view,self.root/'should_not_exist')
        self.assertFalse((self.root/'should_not_exist').exists())
