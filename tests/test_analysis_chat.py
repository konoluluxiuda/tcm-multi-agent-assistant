from contextlib import redirect_stdout
import io
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_intent_planner as fixtures
from tcm.analysis_chat import parse
from tcm.analysis_tools import tool_specs, SPECS
from tcm.assistant_router import resolve
from tcm.intent_planner import plan_question


class ContractTests(unittest.TestCase):
    def test_registered_tools_publish_their_strict_schemas(self):
        self.assertEqual({s['name'] for s in tool_specs()},set(SPECS))
        for spec in tool_specs():self.assertFalse(spec['parameters']['additionalProperties'])

    def test_complete_natural_and_explicit_requests_keep_scope(self):
        q='哪些药材与白术的靶点集合最相似？列前10个，并显示共同靶点和依据。'
        self.assertEqual(parse(q)['call']['arguments'],{'name':'白术','top_k':10})
        self.assertEqual(parse('炒白术的靶点画像')['call']['arguments']['name'],'炒白术')
        self.assertEqual(parse('与白术靶点最相似的药材，按共同靶点数排序，前5个')['call']['arguments']['metric'],'common_count')
        self.assertEqual(parse('/analysis similarity Alpha --top-k 5 --metric common_count',explicit=True)['call']['arguments']['top_k'],5)
        for q in ('白术的靶点画像，再查疾病','与白术和黄芪靶点最相似的药材','与白术靶点最相似的药材，还要价格'):
            self.assertIsNone(parse(q))

    def test_model_uses_registered_call_and_literal_entity_only(self):
        plan={'action':'analysis','analysis':{'tool':'herb_target_similarity','arguments':{'name':'白术','top_k':5}}}
        model=fixtures.Model(plan);result=plan_question('给白术找五个靶点方面的近邻',sdk=model)
        self.assertEqual(result['intent_status'],'ok');self.assertEqual(result['request']['call']['tool'],'herb_target_similarity')
        self.assertIn('herb_target_profile',model.inputs[0][0].content)
        self.assertEqual(len(model.inputs),1)
        self.assertEqual(plan_question('给炒白术找五个靶点方面的近邻',sdk=fixtures.Model(plan))['intent_status'],'invalid_output')
        bad={**plan,'entities':['黄芪']}
        self.assertEqual(plan_question('白术',sdk=fixtures.Model(bad))['intent_status'],'invalid_output')
        for q in ('预测白术的新靶点','白术靶点相似药材，前30个','白术靶点相似药材，前3个'):
            self.assertEqual(plan_question(q,sdk=fixtures.Model(plan))['intent_status'],'invalid_output')

    def test_captured_provider_duplicate_entity_replays_without_losing_scope(self):
        # Exact arguments captured from the authorized provider diagnostic.
        plan={'action':'analysis','analysis':{'tool':'herb_target_similarity',
              'arguments':{'name':'白术','top_k':5}},'entities':['白术']}
        model=fixtures.Model(plan)
        result=plan_question('给白术找五个靶点方面的近邻',sdk=model)
        self.assertEqual(result['intent_status'],'ok')
        self.assertEqual(result['request']['call']['arguments'],{'name':'白术','top_k':5,'metric':'jaccard'})
        self.assertEqual(len(model.inputs),1)
        for extra in ({'entities':['白术','黄芪']},{'entities':['白术','白术']},
                      {'fields':['功效']},{'datasets':['etcm']},{'relation_kind':'target'}):
            self.assertEqual(plan_question('给白术找五个靶点方面的近邻',sdk=fixtures.Model({**plan,**extra}))['intent_status'],'invalid_output')
        self.assertEqual(plan_question('给炒白术找五个靶点方面的近邻',sdk=fixtures.Model(plan))['intent_status'],'invalid_output')


class GraphTests(unittest.TestCase):
    def setUp(self):fixtures.IntentGraphTests.setUp(self)

    def turn(self,q,**kwargs):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':q,**kwargs})

    def test_profile_similarity_and_replay_use_graph_without_model(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Known route called model')):
            profile=self.turn('测试甲草的靶点画像').json()['assistant']
            self.assertEqual(profile['analysis_result']['data']['profile']['target_count'],24)
            response=self.turn('与测试甲草靶点最相似的药材，前5个',answer_mode='evidence')
            self.assertEqual(response.status_code,200);data=response.json()
            self.assertNotIn('evidence_answer',data)
            self.assertEqual(data['assistant']['analysis_result']['data']['items'][0]['shared_count'],23)
            self.assertEqual(self.turn('回看分析结果').json()['assistant']['analysis_result']['data']['items'][0]['shared_count'],23)
            restored=self.client.get('/conversation').json()['assistant']
            self.assertEqual(restored['topic'],'target_analysis')
            self.assertEqual(restored['analysis_result']['data']['metric'],'jaccard')

    def test_model_selected_analysis_calls_existing_local_tool_once(self):
        model=fixtures.Model({'action':'analysis','analysis':{'tool':'herb_target_similarity','arguments':{'name':'测试甲草','top_k':3}},'entities':['测试甲草']})
        self.turn('介绍两个数据库')
        with patch('config.create_chat_model',return_value=model):
            result=self.turn('帮测试甲草找几个靶点方面的近邻').json()
        self.assertEqual(result['model_calls'],1)
        self.assertEqual(result['assistant']['analysis_result']['status'],'ok')
        self.assertEqual(len(model.inputs[0]),2);self.assertNotIn('介绍两个数据库',str(model.inputs))

    def test_memory_saves_parameters_not_target_lists_and_is_session_isolated(self):
        self.turn('与测试甲草靶点最相似的药材')
        cookie=self.client.cookies[self.app.SESSION_COOKIE_NAME]
        saved=self.memory.load(cookie)
        self.assertNotIn('P00000',str(saved));self.assertNotIn('shared_ids',str(saved))
        with patch.object(self.ad,'memory',type(self.memory)(self.memory.path)):
            self.assertEqual(self.client.get('/conversation').json()['assistant']['topic'],'target_analysis')
        from fastapi.testclient import TestClient
        with TestClient(self.app.app) as other:
            self.assertEqual(other.post('/chat',json={'query':'回看分析结果'}).json()['assistant']['status'],'clarification_needed')
        self.turn('我需要查看测试乙草的相关属性')
        self.assertNotEqual(self.client.get('/conversation').json().get('assistant',{}).get('topic'),'target_analysis')

    def test_unknown_ambiguous_prepared_and_uncovered_sources_are_explicit(self):
        for name in ('未知草','炒测试甲草'):
            self.assertEqual(self.turn(name+'的靶点画像').json()['assistant']['status'],'clarification_needed')
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO name_candidates SELECT herb_id,'测试甲草',entry_id,evidence FROM name_candidates WHERE name_zh='测试乙草'")
        r=self.turn('测试甲草的靶点画像').json()['assistant'];self.assertEqual(len(r['options']),2)
        chosen=self.turn(r['options'][0]['query']).json()['assistant'];self.assertEqual(chosen['status'],'ok')
        with sqlite3.connect(self.database) as db:db.execute("DELETE FROM edges WHERE kind='target'")
        empty=self.turn('Alpha的靶点画像').json()['assistant']
        self.assertEqual(empty['analysis_result']['status'],'empty');self.assertIn('暂不能计算',empty['message'])

    def test_replay_invalidates_changes_and_bad_commands_do_not_execute(self):
        self.turn('Alpha的靶点画像')
        with sqlite3.connect(self.database) as db:db.execute("DELETE FROM edges WHERE kind='target'")
        self.assertEqual(self.client.get('/conversation').json()['assistant']['status'],'unavailable')
        for q in ('/analysis similarity Alpha --top-k 21','/analysis exec Alpha'):
            self.assertEqual(self.turn(q).json()['assistant']['status'],'clarification_needed')
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':''}):
            self.assertEqual(self.turn('/analysis profile Alpha').status_code,503)


if __name__=='__main__':unittest.main()
