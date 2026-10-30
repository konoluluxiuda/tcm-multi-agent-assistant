"""Semantic slots, request coverage, and real chat execution with synthetic data."""
import copy
import os
import unittest
from unittest.mock import patch
from tcm.question_contract import spans,clauses,field_tasks
from tcm.research_plan_intent import local_plan,validate_question
from tcm.intent_planner import plan_question
from tests.test_intent_planner import Model
from tests import test_research_plan_chat as chat_fixture


class ContractTests(unittest.TestCase):
    def test_original_spans_preserve_latin_punctuation_versions_and_parentheses(self):
        q=' 查找Diabetes Mellitus, Insulin-Dependent, 20的来源，读取全部靶点原始记录 '
        parts=spans(q)
        self.assertEqual(len(parts),2)
        for p in parts:self.assertEqual(q[p.start:p.end],p.text)
        self.assertIn('Insulin-Dependent, 20',parts[0].text)
        self.assertEqual(clauses('查找Type 2.0 (A, B)的来源，保留出处'),['查找Type 2.0 (A, B)的来源','保留出处'])

    def test_modifiers_are_parameters_and_not_new_actions(self):
        from tcm.analysis_chat import parse
        call=parse('哪些药材与Alpha的靶点最接近？按Jaccard列前5项')['call']
        self.assertEqual(call,{'tool':'herb_target_similarity','arguments':{'name':'Alpha','top_k':5,'metric':'jaccard'}})
        self.assertEqual(parse('帮我看一下Beta的靶点画像')['call']['arguments']['name'],'Beta')
        self.assertIsNone(parse('哪些药材与Alpha的靶点最接近？按Jaccard列前5项，写一首诗'))

    def test_two_structural_fields_use_etcm_contract(self):
        p=local_plan('查看Alpha的药用部位和科名')
        self.assertEqual(p.steps[0].call.tool,'herb_attributes')
        self.assertEqual(p.steps[0].call.arguments.fields,['medicinal_part','family'])
        self.assertIsNone(field_tasks('查看Alpha的药用部位和采购价格'))
        self.assertIsNone(field_tasks('Alpha的归经'))

    def test_inline_objects_cannot_be_lost_or_duplicated(self):
        q='帮我把Alpha的药典功效和Beta在ETCM的药用部位整理到一起，附各自出处'
        p=local_plan(q);self.assertEqual(len(p.steps),2)
        self.assertEqual([s.clauses for s in p.steps],[[0],[0]])
        for steps in (p.steps[:1],[p.steps[0],p.steps[0],p.steps[1]]):
            with self.assertRaises(ValueError):validate_question({'steps':[s.model_dump() for s in steps]},q)

    def test_model_word_order_need_not_match_local_leaf_grammar(self):
        # Fields are grounded within their own clause, never borrowed from a
        # different subject or source. This accepted phrasing has no leaf regex.
        q='请告诉我ETCM中关于Alpha的药用部位和科名'
        self.assertIsNone(field_tasks(q))
        raw={'action':'analysis_plan','plan':{'steps':[{'call':{'tool':'herb_attributes','arguments':{'name':'Alpha','fields':['medicinal_part','family']}},'clauses':[0]}]}}
        m=Model(raw);result=plan_question(q,sdk=m)
        self.assertEqual(result['intent_status'],'ok',result)
        self.assertEqual(len(m.inputs),1);self.assertEqual(m.inputs[0][1].content,q)
        for field in (['family'],['medicinal_part','family','habitat']):
            bad=copy.deepcopy(raw);bad['plan']['steps'][0]['call']['arguments']['fields']=field
            self.assertEqual(plan_question(q,sdk=Model(bad))['intent_status'],'invalid_output')

    def test_unknown_fields_and_extra_tasks_reject_entire_plan(self):
        for q in ('查询药典Alpha的光谱仪器编号，查询Beta的ETCM药用部位',
                  '查看Alpha的药用部位和科名，预测治疗方案',
                  '帮我把Alpha的药典功效和Beta在ETCM的光谱仪器编号整理到一起'):
            self.assertIsNone(local_plan(q),q)
        q='请告诉我ETCM中关于Alpha的科名以及新靶点预测'
        raw={'action':'analysis_plan','plan':{'steps':[{'call':{'tool':'herb_attributes','arguments':{'name':'Alpha','fields':['family']}},'clauses':[0]}]}}
        self.assertEqual(plan_question(q,sdk=Model(raw))['intent_status'],'invalid_output')

    def test_context_pair_keeps_objects_fields_and_partial_page_scope(self):
        q='我正在整理Alpha与Beta的资料，请分别对照药典中的功效、用量，然后给出两者共同的靶点和原始出处'
        p=local_plan(q);a=p.steps[0].call.arguments
        self.assertEqual(a.herbs,['Alpha','Beta']);self.assertFalse(a.all_targets)
        self.assertEqual(a.fields,['功能与主治','用法与用量'])
        for key,value in (('all_targets',True),('fields',['功能与主治']),('herbs',['Alpha','Gamma'])):
            bad=p.model_dump();bad['steps'][0]['call']['arguments'][key]=value
            with self.assertRaises(ValueError):validate_question(bad,q)

    def test_disease_identifier_and_english_name_lower_to_existing_tools(self):
        identifier='etcm-disease:'+'a'*64
        p=local_plan('查看疾病'+identifier+'的全部靶点原始记录')
        self.assertTrue(p.steps[0].all_pages);self.assertEqual(p.steps[0].call.arguments.name,identifier)
        p=local_plan('查找Diabetes Mellitus, Insulin-Dependent, 20的来源，读取全部靶点原始记录，再按Jaccard列出前五个药材候选，保留出处')
        self.assertEqual(len(p.steps),3);self.assertEqual(p.steps[0].call.arguments.name,'Diabetes Mellitus, Insulin-Dependent, 20')

    def test_dataset_help_does_not_require_a_herb(self):
        from tcm.assistant_router import resolve
        with patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1','ENABLE_RESEARCH_TASKS':'1'}):
            self.assertEqual(resolve('请给我当前资料库的数据概览')['request']['topic'],'overview')
            self.assertEqual(resolve('药典的数据能查到哪些字段')['request'],{'topic':'dataset_info','datasets':['pharmacopoeia']})
            r=resolve('核对Alpha和Beta的共同靶点，给出双方原始记录位置')['request']
            self.assertEqual(r['topic'],'etcm_relations');self.assertEqual(r['entities'],['Alpha','Beta'])


class ChatTests(unittest.TestCase):
    setUp=chat_fixture.ChatTests.setUp
    turn=chat_fixture.ChatTests.turn

    def test_multi_field_etcm_keeps_missing_field_and_restores_same_run(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('No provider')):
            data=self.turn('查看Alpha的药用部位和科名').json()
        result=data['assistant'];self.assertEqual(result['status'],'partial',data)
        self.assertEqual(result['execution']['tool_calls'],1)
        fields=result['blocks'][0]['field_rows']
        self.assertEqual(len(fields),1);self.assertEqual(result['blocks'][0]['missing'],['科'])
        self.assertEqual(self.client.get('/conversation').json()['assistant']['run_id'],result['run_id'])

    def test_question_only_model_fields_execute_local_workers(self):
        raw={'action':'analysis_plan','plan':{'steps':[{'call':{'tool':'herb_attributes','arguments':{'name':'Alpha','fields':['medicinal_part']}},'clauses':[0]}]}}
        model=Model(raw)
        with patch('config.create_chat_model',return_value=model):
            result=self.turn('请告诉我ETCM中关于Alpha的药用部位').json()
        self.assertEqual(result['model_calls'],1);self.assertEqual(result['assistant']['status'],'ok',result)
        self.assertEqual(len(model.inputs[0]),2)

    def test_no_partial_dispatch_when_one_inline_field_is_unknown(self):
        with patch.dict(os.environ,{'ENABLE_INTENT_PLANNER':'0'}),patch('tcm.research_plan_run.run_local_actions',side_effect=AssertionError('Partial plan dispatched')):
            r=self.turn('查询药典Alpha的光谱仪器编号，查询Beta的ETCM药用部位').json()
        self.assertNotEqual(r['assistant'].get('status'),'ok')
