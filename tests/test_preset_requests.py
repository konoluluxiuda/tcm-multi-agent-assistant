"""Bounded preset parsing and shared HTTP delivery, without provider access."""
from pathlib import Path
import re
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from tcm.dataops.preset_requests import parse, proposal
from tests import test_requirements_feedback_http as fixtures


class PresetParsingTests(unittest.TestCase):
    def test_documented_questions_are_fully_consumed(self):
        questions=re.findall(r'^\| (Q\d\d) \| (.*?) \|',Path('docs/PRESET_QUESTION_SCOPE.md').read_text(),re.M)
        self.assertEqual(len(questions),10)
        for key,q in questions:
            with self.subTest(key=key):self.assertIsNotNone(parse(q))
        d=parse(questions[3][1])
        self.assertEqual(len(d.objects),2)
        self.assertEqual({f.name for f in d.requirements[0].fields},{'family','properties','flavors','meridians'})

    def test_extra_requirements_negation_unknown_fields_and_all_not_swallowed(self):
        for q in ['查看白术的ETCM科名并推荐处方','查看白术的ETCM科名和未知字段',
                  '查看白术的ETCM科名和','查看白术的全部靶点记录，先显示一页',
                  '查看白术的ETCM科名，不要药用部位','把白术的ETCM科名生成表格，药名第一列']:
            with self.subTest(q=q):self.assertIsNone(parse(q))

    def test_relation_only_resolves_unique_sources_and_stays_on_first_page(self):
        r=proposal('查看白术的成分记录，先显示一页。',{'白术':['s1']})
        self.assertTrue(r['route']['request']['resolve_unique_sources'])
        self.assertEqual(r['route']['request']['page'],1)
        self.assertEqual(proposal('查看白术的成分记录，先显示一页。',{'白术':['s1','s2']})['status'],'needs_input')

    def test_unknown_and_ambiguous_sources_cannot_execute(self):
        for catalog in [{},{'白术':['s1','s2']}]:
            r=proposal('查看白术的ETCM科名。',catalog)
            self.assertEqual(r['status'],'needs_input')
            self.assertFalse(r.get('goal'))


class PresetHTTPTests(unittest.TestCase):
    wait=fixtures.FeedbackHTTPTests.wait
    def setUp(self):
        fixtures.FeedbackHTTPTests.setUp(self)
        from tcm.evaluation.api_runner import _synthetic_sources
        sources, env = _synthetic_sources(Path(self.tmp.name)/'preset')
        env.update(DATAOPS_UNDERSTANDING_POLICY='local_then_one_model_v1',ENABLE_PHARMACOPOEIA_RESEARCH='1')
        p=patch.dict('os.environ',env);p.start();self.addCleanup(p.stop)
        for target in ('tcm.dataops.api.configured_sources','tcm.dataops.jobs.research_sources'):
            p=patch(target,sources);p.start();self.addCleanup(p.stop)
        p=patch('tcm.dataops.research_model.configured_model',side_effect=AssertionError('Preset must not use model'))
        p.start();self.addCleanup(p.stop)

    def test_multi_object_and_cross_source_answers(self):
        for i,(q,n) in enumerate([('分别告诉我Alpha和Beta的ETCM药用部位。',2),
                  ('查询Alpha的ETCM药用部位和药典功能与主治。',2)]):
            r=self.client.post('/chat',json={'query':q,'request_key':f'preset-answer-{i}'})
            self.assertEqual(r.status_code,200,r.text)
            b=r.json();self.assertEqual(b['model_calls'],0)
            self.assertEqual(len(b['answer_steps']),n)
            self.assertEqual(b['status'],'success',b)

    def test_single_source_table_is_downloadable_without_added_columns(self):
        r=self.client.post('/chat',json={'query':'把Alpha和Beta的ETCM药用部位生成表格。','request_key':'preset-table-1'})
        self.assertEqual(r.status_code,200,r.text)
        doc=self.wait(r.json()['assistant']['workspace_id'])
        self.assertEqual(doc['status'],'complete')
        self.assertEqual(doc['goal']['document_fields'],[])
        self.assertEqual(self.client.get('/api/dataops/workspaces/'+doc['id']+'/artifact').status_code,200)


class ReleaseBindingTests(unittest.TestCase):
    def test_release_and_base_databases_both_checked(self):
        from types import SimpleNamespace
        from tcm.dataops.answer_delivery import bound_query_sources
        from tcm.dataops.data_access import SourceChanged
        @contextmanager
        def provider():yield SimpleNamespace(revision={'dataset_version':'v','sources':['a']},assert_unchanged=lambda:None)
        @contextmanager
        def release(_):yield SimpleNamespace(revision={'dataset_version':'v','sources':['b'],'release_content_id':'r'})
        with patch.dict('os.environ',{'RAG_RELEASE_PATH':'fixture'}),patch('tcm.dataops.research_sources.ReleaseSources',release):
            with self.assertRaises(SourceChanged):
                with bound_query_sources(provider,{'release_content_id':'r'}):pass
