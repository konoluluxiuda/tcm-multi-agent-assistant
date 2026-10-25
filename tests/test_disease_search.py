"""Source discovery must be literal, bounded, versioned and never choose a disease."""
from contextlib import redirect_stdout
import io
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from scripts.build_etcm_diseases import build
from tests import test_disease_analysis as analysis_fixtures
from tests.test_disease_catalog import document
from tests.test_intent_planner import Model
from tcm.analysis_tools import invoke,execute_turn,command
from tcm.analysis_chat import parse
from tcm.disease_search import search
from tcm.intent_planner import plan_question


class SearchTests(unittest.TestCase):
    def setUp(self):
        analysis_fixtures.DiseaseAnalysisTests.setUp(self)
        for i in range(25):
            name=f'Synthetic disease subtype {i:02d}'
            (self.disease_source/'etcm_diseases'/f'{name}.json').write_text(json.dumps(document(name)))
        with redirect_stdout(io.StringIO()):build(self.disease_source,self.database,self.root/'many','disease-search-v1')
        self.disease_db=self.root/'many/diseases.sqlite3'
        p=patch.dict(os.environ,{'ETCM_DISEASE_DB':str(self.disease_db)});p.start();self.addCleanup(p.stop)

    def turn(self,q,**kw):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':q,**kw})

    def test_natural_examples_count_ids_and_replay(self):
        for question in ('随便提供两个疾病及id','请给我2个疾病和ID','列出三个疾病名称及编号'):
            request=parse(question)
            count=request['call']['arguments']['page_size']
            with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('No model needed')):
                response=self.turn(question).json()['assistant']
            data=response['analysis_result']['data']
            self.assertEqual(len(data['items']),count)
            self.assertTrue(all(item['record_id'].startswith('etcm-disease:') for item in data['items']))
            replay=self.turn('回看分析结果').json()['assistant']['analysis_result']['data']
            self.assertEqual(replay['items'],data['items'])
            second=search(self.disease_db,page=2,page_size=count)
            self.assertFalse({r['record_id'] for r in data['items']}&{r['record_id'] for r in second['items']})
            call=parse(command(request['call']),explicit=True)['call']
            self.assertEqual(call['arguments']['page_size'],count)
        for question in ('提供两个疾病及id并推荐用药','提供两个疾病及id还要分析靶点'):
            self.assertIsNone(parse(question))
        for size in (0,21,True):
            with self.assertRaises(ValueError):search(self.disease_db,page_size=size)

    def test_directory_paraphrases_and_continuation(self):
        questions={'请帮我列举两种疾病并附上ID':2,'给我几个疾病及编号':5,
                   '给我十一种疾病和ID':11,'随便给2个疾病':2,'列出前二十条疾病来源及id':20}
        for q,count in questions.items():
            result=self.turn(q).json()['assistant']
            self.assertEqual(len(result['analysis_result']['data']['items']),count,q)
        first=self.turn('给我两个疾病及id').json()['assistant']['analysis_result']['data']
        second=self.turn('再给两个疾病').json()['assistant']['analysis_result']['data']
        self.assertEqual(second['page'],2)
        self.assertFalse({r['record_id'] for r in first['items']}&{r['record_id'] for r in second['items']})
        back=self.turn('疾病上一页').json()['assistant']['analysis_result']['data']
        self.assertEqual(back['items'],first['items'])
        for q in ('提供0个疾病','提供21个疾病','再给三个疾病'):
            self.assertEqual(self.turn(q).json()['assistant']['status'],'clarification_needed')
        for q in ('数据库里有哪些疾病','有哪些疾病及ID','查看疾病列表'):
            self.assertEqual(parse(q)['call']['tool'],'search_disease_sources')
        for q in ('列出两种疾病并预测治疗方案','给我几个疾病及编号，再查文献','再给两个疾病并分析靶点'):
            self.assertIsNone(parse(q))

    def test_directory_followup_requires_own_current_focus(self):
        from tcm.analysis_tools import execute_turn
        r,_=execute_turn(parse('疾病下一页'))
        self.assertEqual(r['status'],'clarification_needed')
        self.turn('给我两个疾病及id')
        cookie=self.client.cookies[self.app.SESSION_COOKIE_NAME]
        self.client.cookies.clear()
        self.assertEqual(self.turn('再给两个疾病').json()['assistant']['status'],'clarification_needed')
        self.client.cookies.set(self.app.SESSION_COOKIE_NAME,cookie)
        with sqlite3.connect(self.disease_db) as con:con.execute("UPDATE metadata SET value='changed' WHERE key='source_fingerprint'")
        self.assertEqual(self.turn('疾病下一页').json()['assistant']['status'],'unavailable')

    def test_model_cannot_change_directory_count(self):
        for size,status in ((2,'ok'),(20,'invalid_output')):
            plan={'action':'analysis','analysis':{'tool':'search_disease_sources','arguments':{'name':'','page_size':size}}}
            result=plan_question('给我两个疾病及ID',sdk=Model(plan))
            self.assertEqual(result['intent_status'],status)
        plan={'action':'analysis','analysis':{'tool':'search_disease_sources','arguments':{'name':'','page_size':2,'page':2}}}
        self.assertEqual(plan_question('给我两个疾病及ID',sdk=Model(plan))['intent_status'],'invalid_output')

    def test_literal_keywords_exact_priority_browse_and_pages(self):
        first=search(self.disease_db,'SYNTHETIC disease');second=search(self.disease_db,'SYNTHETIC disease',page=2)
        self.assertEqual(first['total'],26);self.assertEqual(first['items'][0]['name'],'Synthetic disease')
        self.assertEqual(first['items'][0]['match_kind'],'exact');self.assertEqual(len(second['items']),6)
        self.assertFalse({x['record_id'] for x in first['items']}&{x['record_id'] for x in second['items']})
        self.assertEqual(search(self.disease_db,'subtype synthetic')['total'],25)
        self.assertEqual(search(self.disease_db)['total'],29)
        for word in ('%','_',"' OR 1=1 --",'糖尿病'):
            self.assertEqual(search(self.disease_db,word)['total'],0)

    def test_parser_roundtrips_blank_browse_and_keyword_page(self):
        for name in ('','Synthetic disease'):
            call={'tool':'search_disease_sources','arguments':{'name':name,'page':2}}
            self.assertEqual(parse(command(call),explicit=True)['call'],call)
        for q in ('浏览疾病目录','有哪些疾病','疾病目录'):
            self.assertEqual(parse(q)['call']['arguments']['name'],'')
        self.assertEqual(parse('查找疾病： diabetes')['call']['arguments']['name'],'diabetes')
        self.assertNotIn('call',parse('/analysis diseases --page nope',explicit=True))
        self.assertEqual(invoke({'tool':'search_disease_sources','arguments':{'name':'a','page':0}}).error.code,'invalid_arguments')

    def test_search_chat_replay_and_selection_are_local(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('No model needed')):
            d=self.turn('查找疾病 Synthetic').json()['assistant']['analysis_result']['data']
            self.assertEqual(d['total'],26)
            self.assertEqual(self.turn('回看分析结果').json()['assistant']['analysis_result']['tool'],'search_disease_sources')
            self.assertEqual(self.client.get('/conversation').json()['assistant']['analysis_result']['data']['query'],'Synthetic')
            r=self.turn('/analysis disease '+d['items'][0]['record_id']).json()['assistant']
            self.assertEqual(r['analysis_result']['tool'],'disease_gene_profile')
        cookie=self.client.cookies[self.app.SESSION_COOKIE_NAME]
        self.turn('查找疾病 Synthetic')
        saved=str(self.memory.load(cookie));self.assertNotIn('target_ids',saved);self.assertNotIn('subtype 00',saved)

    def test_partial_ranking_requires_selection_and_keeps_requested_parameters(self):
        with patch('tcm.target_similarity.query',side_effect=AssertionError('No ranking before selection')):
            r=self.turn('疾病 Synthetic 的候选药材，按共同靶点数排序，前5个').json()['assistant']
        self.assertEqual(r['status'],'clarification_needed');self.assertEqual(len(r['options']),20)
        self.assertTrue(r['options_truncated']);query=r['options'][0]['query']
        self.assertIn('--top-k 5 --metric common_count',query)
        data=self.turn(query).json()['assistant']['analysis_result']['data']
        self.assertEqual((data['top_k'],data['metric']),(5,'common_count'))

    def test_version_change_invalidates_replay_and_removed_id_not_replaced(self):
        r,focus=execute_turn(parse('查找疾病 Synthetic'));sid=r['analysis_result']['data']['items'][0]['record_id']
        with sqlite3.connect(self.disease_db) as db:db.execute('DELETE FROM diseases WHERE record_id=?',(sid,))
        r,_=execute_turn({'followup':True},focus);self.assertEqual(r['status'],'unavailable')
        r=self.turn('/analysis disease '+sid).json()['assistant'];self.assertEqual(r['status'],'clarification_needed');self.assertFalse(r['options'])

    def test_empty_disabled_search_and_output_gate(self):
        r=invoke({'tool':'search_disease_sources','arguments':{'name':'unknown'}});self.assertEqual(r.status,'empty')
        with patch.dict(os.environ,{'ETCM_DISEASE_DB':''}):
            r,_=execute_turn(parse('浏览疾病目录'));self.assertEqual(r['status'],'unavailable')
        data=search(self.disease_db,'Synthetic');data['total']=0
        with patch('tcm.disease_search.query',return_value=data):
            self.assertEqual(invoke({'tool':'search_disease_sources','arguments':{'name':'Synthetic'}}).error.code,'invalid_result')

    def test_model_literals_and_empty_term_cannot_replace_named_request(self):
        plan={'action':'analysis','analysis':{'tool':'search_disease_sources','arguments':{'name':'Synthetic'}}}
        self.assertEqual(plan_question('帮我找一找名称含 Synthetic 的疾病来源',sdk=Model(plan))['intent_status'],'ok')
        self.assertEqual(plan_question('查找疾病 糖尿病',sdk=Model(plan))['intent_status'],'invalid_output')
        plan['analysis']['arguments']['name']=''
        self.assertEqual(plan_question('浏览疾病目录',sdk=Model(plan))['intent_status'],'ok')
        self.assertEqual(plan_question('查找疾病 Synthetic',sdk=Model(plan))['intent_status'],'invalid_output')

    def test_same_names_are_distinct_sources_and_model_search_is_one_call(self):
        with sqlite3.connect(self.disease_db) as db:
            db.execute("UPDATE diseases SET name='Synthetic disease',name_key='synthetic disease' WHERE name='Same set'")
        r=search(self.disease_db,'Synthetic disease');self.assertEqual(r['total'],27)
        self.assertNotEqual(r['items'][0]['record_id'],r['items'][1]['record_id'])
        model=Model({'action':'analysis','analysis':{'tool':'search_disease_sources','arguments':{'name':'Synthetic'}}})
        self.turn('介绍两个数据库')
        with patch('config.create_chat_model',return_value=model):
            result=self.turn('能找找带 Synthetic 字样的病名吗').json()
        self.assertEqual(result['model_calls'],1);self.assertEqual(result['assistant']['analysis_result']['tool'],'search_disease_sources')
        self.assertEqual(len(model.inputs[0]),2);self.assertNotIn('介绍两个数据库',str(model.inputs));self.assertNotIn('subtype 00',str(model.inputs))


if __name__=='__main__':unittest.main()
