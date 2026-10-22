"""Attribute queries and one-call model routing use actual graph and local fixtures."""
from contextlib import redirect_stdout
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from agents.conversation_store import ConversationStore
from features import can_plan_intent
from tcm.attribute_intent import parse
from tcm.assistant_router import resolve
from tcm.intent_planner import plan_question
from tests import test_relation_queries as fixtures


class Model:
    def __init__(self,plan=None,error=None):self.plan=plan;self.error=error;self.inputs=[];self.binds=[]
    def bind_tools(self,*args,**kwargs):self.binds.append((args,kwargs));return self
    def invoke(self,messages):
        self.inputs.append(messages)
        if self.error:raise self.error
        return SimpleNamespace(tool_calls=[{'name':'submit_query_intent','args':self.plan}],invalid_tool_calls=[])


class IntentTests(unittest.TestCase):
    def test_model_can_describe_collections_without_a_herb(self):
        for datasets in ([],['etcm'],['pharmacopoeia'],['etcm','pharmacopoeia']):
            model=Model({'action':'dataset_info','datasets':datasets})
            result=plan_question('说说咱们装的那两套资料各擅长什么',sdk=model)
            self.assertEqual(result['intent_status'],'ok')
            self.assertEqual(result['request'],{'topic':'dataset_info','datasets':datasets})
            self.assertEqual(result['intent_model_calls'],1)
        for plan in ({'action':'dataset_info','datasets':['private_db']},
                     {'action':'dataset_info','entities':['白术']},
                     {'action':'herb_profile','entities':['白术'],'datasets':['etcm']}):
            self.assertEqual(plan_question('白术资料',sdk=Model(plan))['intent_status'],'invalid_output')

    def test_attribute_paraphrases_keep_complete_literal_name(self):
        for question in ('我需要查看白术的相关属性','帮我看看白术的基本信息','白术有哪些属性','我想了解白术的属性','查询白术的资料'):
            self.assertEqual(parse(question)['name'],'白术')
        self.assertEqual(parse('查询炒白术的基本属性')['name'],'炒白术')
        self.assertEqual(parse('看看未知测试草的资料')['name'],'未知测试草')
        self.assertTrue(parse('它的相关属性呢')['followup'])
        for question in ('白术和黄芪的属性','白术的属性，还有文献和价格','查询白术的资料并帮我配药'):
            self.assertIsNone(parse(question))
        with patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1'}):
            self.assertEqual(resolve('什么是属性')['request']['topic'],'concept')
            self.assertEqual(resolve('这个助手能提供什么资料')['request']['topic'],'capabilities')
            self.assertEqual(resolve('在ETCM查询白术的相关属性')['request']['name'],'白术')
            self.assertEqual(resolve('在ETCM白术的基本信息')['request']['name'],'白术')

    def test_opt_in_lengths_commands_and_images_are_bounded(self):
        with patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1','ENABLE_INTENT_PLANNER':'1'}):
            self.assertTrue(can_plan_intent('给我看看示例草资料'))
            for q in ('',' '*3,'例'*1001,'/unknown 示例草',{'image':'private','text':'示例草'}):self.assertFalse(can_plan_intent(q))
        with patch.dict(os.environ,{'ENABLE_INTENT_PLANNER':'0'}):self.assertFalse(can_plan_intent('示例草'))

    def test_model_gets_current_question_only_and_returns_whitelisted_route(self):
        model=Model({'action':'herb_profile','entities':['示例甲草']})
        question='能给我一张示例甲草的资料卡吗'
        route=plan_question(question,sdk=model)
        self.assertEqual(route['tool'],'herb');self.assertEqual(route['request']['name'],'示例甲草')
        self.assertEqual(route['intent_model_calls'],1);self.assertEqual(route['intent_status'],'ok')
        self.assertEqual(len(model.inputs),1);self.assertEqual(len(model.inputs[0]),2)
        self.assertEqual(model.inputs[0][1].content,question)
        self.assertFalse(model.binds[0][1]['parallel_tool_calls'])

    def test_fields_relations_and_comparison_reuse_existing_tools(self):
        for plan,q,tool in [
            ({'action':'research','entities':['示例草'],'fields':['注意']},'示例草有哪些使用风险','research'),
            ({'action':'compare','entities':['甲草','乙草'],'fields':['功能与主治','用法与用量']},'对照甲草和乙草能干啥及用多少','research'),
            ({'action':'relation','entities':['甲草','乙草'],'relation_kind':'common_target'},'甲草与乙草的交集标识','assistant'),
            ({'action':'herb_field','entities':['Alpha'],'fields':['family']},'Alpha是什么科的','herb')]:
            route=plan_question(q,sdk=Model(plan));self.assertEqual(route['intent_status'],'ok');self.assertEqual(route['tool'],tool)

    def test_invented_names_preparations_extra_actions_and_invalid_schemas_stop(self):
        for plan,q in [
            ({'action':'herb_profile','entities':['乙草']},'甲草的资料卡'),
            ({'action':'herb_profile','entities':['甲草']},'炒甲草的资料卡'),
            ({'action':'herb_profile','entities':['甲草']},'甲草的提取物资料卡'),
            ({'action':'herb_profile','entities':['甲草']},'假甲草的资料卡'),
            ({'action':'herb_profile','entities':['Alpha']},'Alphabet的资料卡'),
            ({'action':'herb_profile','entities':['甲草']},'甲草的资料和采购价格'),
            ({'action':'herb_field','entities':['甲草'],'fields':['DROP TABLE records']},'甲草的信息'),
            ({'action':'herb_profile','entities':['甲草'],'reasoning':'private chain'},'甲草的信息'),
            ({'action':'relation','entities':['甲草'],'relation_kind':'common_target'},'甲草信息')]:
            result=plan_question(q,sdk=Model(plan));self.assertEqual(result['intent_status'],'invalid_output')
            self.assertEqual(result['request']['topic'],'intent_clarification')

    def test_timeout_and_unsupported_never_retry_or_expose_provider_error(self):
        model=Model(error=TimeoutError('secret provider details'));result=plan_question('示例草资料卡',sdk=model)
        self.assertEqual(len(model.inputs),1);self.assertEqual(result['intent_status'],'unavailable')
        self.assertNotIn('secret',str(result))
        result=plan_question('给我开药',sdk=Model({'action':'unsupported'}))
        self.assertTrue(result['request']['unsupported'])


class IntentGraphTests(unittest.TestCase):
    def setUp(self):
        fixtures.RelationQueryTests.setUp(self)
        import app
        from agents import agent_decision as ad
        self.app=app;self.ad=ad;self.memory=ConversationStore(self.root/'chat.sqlite3')
        def forbidden(*args,**kwargs):raise AssertionError('Unexpected legacy model')
        patches=[patch.dict(os.environ,{'RESEARCH_TOOLS_ONLY':'1','ENABLE_NATURAL_ROUTING':'1','ENABLE_INTENT_PLANNER':'1',
                       'ENABLE_EVIDENCE_ASSISTANT':'0','ENABLE_EVIDENCE_AUTO_ROUTING':'0'}),patch.object(ad,'memory',self.memory),
                 patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)),
                 patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)),
                 patch.object(ad.LocalGuardrails,'check_input',forbidden),patch.object(ad.LocalGuardrails,'check_output',forbidden)]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app);self.addCleanup(self.client.close)

    def turn(self,q,**kwargs):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':q,**kwargs})

    def test_user_attribute_question_uses_no_model_and_recovers_source(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Matched attributes should be local')):
            self.assertEqual(resolve('我需要查看白术的相关属性')['tool'],'herb')
            data=self.turn('我需要查看测试甲草的相关属性').json()
            self.assertEqual(data['tcm']['status'],'ok');self.assertEqual(data['tcm']['selection_method'],'unique_local_candidate')
            self.assertEqual(data['routing']['reason'],'herb_attribute_query')
            self.assertEqual(data['tcm']['records'][0]['fields']['name_pinyin']['value'],'Alpha')
            self.assertEqual(self.turn('它的基本信息呢').json()['tcm']['status'],'ok')
            self.assertEqual(self.client.get('/conversation').json()['tcm']['status'],'ok')
            self.assertEqual(self.turn('查看未知测试草的属性').json()['tcm']['status'],'unsupported_name_mapping')
            self.assertNotEqual(self.turn('查看炒测试甲草的属性').json()['tcm']['status'],'ok')

    def test_unmatched_question_has_one_model_call_and_local_result_no_history_egress(self):
        self.turn('你能做什么')
        model=Model({'action':'herb_profile','entities':['测试甲草']})
        with patch('config.create_chat_model',return_value=model) as factory:
            response=self.turn('能给我一张测试甲草的资料卡吗')
        self.assertEqual(response.status_code,200,response.text);data=response.json()
        self.assertEqual(data['routing']['mode'],'model');self.assertEqual(data['model_calls'],1)
        self.assertEqual(data['tcm']['status'],'ok');self.assertEqual(len(model.inputs),1)
        self.assertEqual(model.inputs[0][1].content,'能给我一张测试甲草的资料卡吗')
        self.assertNotIn('你能做什么',str(model.inputs));self.assertNotIn('Root',str(model.inputs))
        self.assertEqual(factory.call_args.kwargs['max_retries'],0)
        snapshot=self.memory.load_serialized(self.client.cookies[self.app.SESSION_COOKIE_NAME])
        self.assertTrue(all(not m['data'].get('tool_calls') for m in snapshot.messages))
        self.assertNotIn('submit_query_intent',str(snapshot.messages))

    def test_model_description_intent_executes_the_local_description_tool(self):
        question='说说咱们装的那两套资料各擅长什么'
        model=Model({'action':'dataset_info'})
        self.turn('数据概览')
        with patch('config.create_chat_model',return_value=model):response=self.turn(question)
        self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(result['model_calls'],1)
        self.assertEqual(result['assistant']['topic'],'dataset_info')
        self.assertEqual(len(result['assistant']['datasets']),2)
        self.assertEqual(len(model.inputs),1);self.assertEqual(len(model.inputs[0]),2)
        self.assertEqual(model.inputs[0][1].content,question)
        self.assertNotIn('数据概览',str(model.inputs));self.assertNotIn('2076',str(model.inputs))

    def test_model_failure_stops_without_tools_or_legacy_fallback(self):
        with patch('config.create_chat_model',return_value=Model(error=TimeoutError('private'))), \
             patch('tcm.chat.execute_turn',side_effect=AssertionError('No data query allowed')):
            response=self.turn('给我一张测试甲草的资料卡吗')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['assistant']['status'],'clarification_needed')
        self.assertEqual(response.json()['routing']['intent_status'],'unavailable')
        self.assertNotIn('private',response.text)

    def test_known_rules_and_explicit_commands_are_not_sent_to_planner(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('No fallback for rules')):
            for q in ('数据概览','我需要查看测试甲草的相关属性','测试甲草与测试乙草有哪些共同靶点','/herb Alpha','我想查共同靶点，再帮我配药'):
                self.assertEqual(self.turn(q).status_code,200,q)
            self.assertEqual(self.turn('/unknown 测试甲草').status_code,403)

    def test_multiple_candidates_require_selection_and_ai_toggle_cannot_change_profile_path(self):
        import sqlite3
        with sqlite3.connect(self.database) as con:con.execute("INSERT INTO name_candidates SELECT herb_id,'测试甲草',entry_id,evidence FROM name_candidates WHERE name_zh='测试乙草'")
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Rule query called a model')), \
             patch.dict(os.environ,{'ENABLE_EVIDENCE_ASSISTANT':'1'}):
            data=self.turn('查看测试甲草的属性',answer_mode='evidence').json()
        self.assertEqual(data['tcm']['status'],'name_mapping_candidates');self.assertEqual(len(data['tcm']['records']),2)
        self.assertNotIn('evidence_answer',data)


if __name__=='__main__':unittest.main()
