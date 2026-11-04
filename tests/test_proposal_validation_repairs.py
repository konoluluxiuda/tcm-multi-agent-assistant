"""Saved-shape regression and adversarial constraints, not new model accuracy."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from tcm.intent_planner import QueryIntent,grounded,mapped
from tcm.research_plan_intent import validate_question,presentation
from tcm.dataops.research_question import request_proposal,compile_draft,all_complete_request
from tcm.dataops.goal_model import GoalResponseRejected
from tcm.dataops import diagnostics as d
from tcm.request_boundaries import personal_treatment_request
from tests.test_unified_request_harness import Model
from tests.test_research_question import draft
from tests import test_research_question_api as api_fixture


class ProposalTests(unittest.TestCase):
    def test_catalog_literal_objects_do_not_require_a_known_request_verb(self):
        cases=[('请列出茯苓的已有成分记录和出处。',['茯苓'],'ingredient'),
               ('我想核对甘草和大黄重合的靶点，给出两边的记录出处。',['甘草','大黄'],'common_target')]
        for q,names,kind in cases:
            model=Model({'action':'relation','entities':names,'relation_kind':kind},tool='submit_query_intent')
            result=request_proposal(model,q,{n:[str(i)] for i,n in enumerate(names)},authorized=True,allow_queries=True)
            self.assertEqual(result['status'],'query');self.assertEqual(model.calls,1)
            self.assertEqual(result['route']['request']['entities'],names)
            self.assertEqual(result['route']['request']['kind'],kind)

    def test_preparations_negation_compounds_and_longer_objects_remain_protected(self):
        for q in ('炒白术的成分','白术胶囊的靶点','白术提取物的靶点','不查白术的成分',
                  '不要列出白术的成分','复方白术的资料','虚构白术的资料'):
            self.assertFalse(grounded('白术',q,known_names={'白术':['a']}),q)
        self.assertFalse(grounded('红花','查西红花的属性',known_names=['红花','西红花']))
        self.assertTrue(grounded('西红花','列出西红花的属性',known_names=['红花','西红花']))
        self.assertFalse(grounded('Alpha','AlphaBeta的资料',known_names=['Alpha']))
        self.assertFalse(grounded('白术','查询黄芪的资料',known_names=['白术']))
        self.assertTrue(grounded('炒白术','查询炒白术的资料',known_names=['炒白术']))

    def test_omitting_a_second_named_object_still_stops_a_single_query(self):
        model=Model({'action':'relation','entities':['茯苓'],'relation_kind':'ingredient'},tool='submit_query_intent')
        with self.assertRaises(GoalResponseRejected) as exc:
            request_proposal(model,'请列出茯苓与陈皮的成分',{'茯苓':['a'],'陈皮':['b']},authorized=True,allow_queries=True)
        self.assertEqual(exc.exception.diagnostics['rejection']['code'],'mentioned_object_omitted')

    def test_stopping_action_can_explain_why_without_executable_slots(self):
        args={'action':'unsupported','entities':['黄芪'],'unresolved_requirements':['个体用药要求不支持']}
        intent=QueryIntent.model_validate(args)
        route=mapped(intent,'为患者安排用药')
        self.assertEqual(intent.entities,[]);self.assertTrue(route['request']['unsupported'])
        self.assertEqual(route['request']['unresolved_requirements'],args['unresolved_requirements'])
        from tcm.assistant_info import execute
        result=execute(route['request'])
        self.assertEqual(result['unresolved_requirements'],args['unresolved_requirements'])
        for extra in ({'fields':['注意']},{'relation_kind':'ingredient'}):
            with self.assertRaises(ValueError):QueryIntent.model_validate({**args,**extra})
        with self.assertRaises(ValueError):QueryIntent(action='relation',entities=['黄芪'],relation_kind='target',unresolved_requirements=['还要别的'])

    def test_presentation_is_not_an_action_but_unknown_requirements_cannot_disappear(self):
        q='查询陈皮与枸杞子各自有哪些成分与靶点，注明出处。'
        plan={'steps':[{'call':{'tool':'herb_relation_records','arguments':{'name':n,'kind':k,'page':1}},'clauses':[0]}
                       for n in ('陈皮','枸杞子') for k in ('ingredient','target')]}
        self.assertTrue(presentation('注明出处'));self.assertTrue(presentation('标明双方原始记录的位置'))
        self.assertEqual(len(validate_question(plan,q).steps),4)
        with self.assertRaises(ValueError):validate_question({'steps':plan['steps'][:-1]},q)
        for tail in ('注明出处并预测新靶点','注明出处再查价格','注明药材供应商'):
            with self.assertRaises(ValueError):validate_question(plan,q.split('，')[0]+'，'+tail)

    def test_whole_pool_selection_does_not_confuse_two_fields_with_two_rows(self):
        q='从甘草、大黄、陈皮中选出药典功效和注意两项都有记录的，做一张表。'
        catalog={'甘草':['a'],'大黄':['b'],'陈皮':['c']}
        self.assertTrue(all_complete_request(q))
        raw=draft(names=list(catalog),mode='filter_complete',document_fields=['功能与主治','注意'])
        value=compile_draft(raw,q,catalog)
        self.assertEqual(value['status'],'ready',value)
        self.assertEqual(value['goal']['research']['target_count'],3)
        for mode,count in [('audit_named',None),('select_complete',2)]:
            wrong=compile_draft({**raw,'mode':mode,'target_count':count},q,catalog)
            self.assertEqual(wrong['status'],'needs_input')
        explicit='从甘草、大黄、陈皮中选出两条功效和注意都有记录的，做表'
        value=compile_draft({**raw,'mode':'select_complete','target_count':2},explicit,catalog)
        self.assertEqual(value['status'],'ready');self.assertEqual(value['goal']['research']['target_count'],2)

    def test_third_person_treatment_directives_are_not_research_mentions(self):
        for q in ('我父亲正在化疗，请给他安排几味中药和每天的剂量。',
                  '请为我母亲推荐中药和剂量','给这位患者开药'):
            self.assertTrue(personal_treatment_request(q),q)
        for q in ('不要给我父亲安排中药，只查药典资料','查询化疗患者相关的药典研究资料',
                  '帮我查询药典的功能与主治','不要帮我治疗，只查药典资料'):
            self.assertFalse(personal_treatment_request(q),q)
        from tcm.request_boundaries import unsupported_requirement
        self.assertTrue(unsupported_requirement('给我黄芪的功效，再替我联系药店下单。'))
        self.assertFalse(unsupported_requirement('别替我下单，只查黄芪的功效'))


class DiagnosticTests(unittest.TestCase):
    def test_nested_parameters_survive_private_diagnostics_and_secrets_are_removed(self):
        calls=[{'id':'fixture','name':'submit_query_intent','args':{'action':'analysis_plan','plan':{
            'steps':[{'call':{'tool':'herb_relation_records','arguments':{'name':name,'kind':kind,'page':1}},'clauses':[0]}
                     for name in ('陈皮','枸杞子') for kind in ('ingredient','target')]}}}]
        reply=AIMessage(content='',tool_calls=calls)
        with tempfile.TemporaryDirectory() as root,d.request_context() as ctx:
            d.bind(root,'owner');d.record('model_return','received',**d.model_return(reply),authorization='Bearer secret-key')
            receipt=d.Store(root).get('owner',ctx['request_id'])
        detail=receipt['events'][0]['details']
        steps=detail['tool_calls'][0]['args']['plan']['steps']
        self.assertEqual([s['call']['arguments']['name'] for s in steps],['陈皮','陈皮','枸杞子','枸杞子'])
        self.assertEqual(len(detail['tool_calls_sha256']),64);self.assertNotIn('secret-key',json.dumps(detail))

    def test_concurrent_initialization_preserves_all_owner_bound_requests(self):
        with tempfile.TemporaryDirectory() as root:
            def worker(i):
                with d.request_context() as ctx:
                    d.bind(root,'owner');d.record('parallel','observed',marker=i)
                    return ctx['request_id'],i
            with ThreadPoolExecutor(8) as pool:rows=list(pool.map(worker,range(24)))
            for identifier,i in rows:
                self.assertEqual([e['details']['marker'] for e in d.Store(root).get('owner',identifier)['events']], [i])
                with self.assertRaises(KeyError):d.Store(root).get('other',identifier)


class ApiTests(unittest.TestCase):
    def test_third_person_treatment_never_reaches_model_or_queue(self):
        from tests import test_research_plan_chat as fixture
        helper=fixture.ChatTests();helper.setUp();self.addCleanup(helper.doCleanups)
        with patch('tcm.dataops.chat.execute',side_effect=AssertionError('Treatment reached task execution')), \
             patch('config.create_chat_model',side_effect=AssertionError('Treatment reached provider')):
            result=helper.turn('我父亲正在化疗，请给他安排几味中药和每天的剂量。')
        self.assertEqual(result.status_code,403,result.text)
