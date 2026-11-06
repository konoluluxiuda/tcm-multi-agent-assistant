"""Reported local queries, source boundaries and actionable public failures."""
import os
import unittest
from unittest.mock import patch

from tcm.assistant_router import resolve
from tcm.question_contract import field_tasks
from tcm.query_failures import with_failure
from tcm.mineru_chat import ResearchTool
from tcm.agent_research import status_result
from tests import test_mineru_chat as research_fixture
from tests import test_research_plan_chat as chat_fixture


class RoutingTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,{'ENABLE_NATURAL_ROUTING':'1','ENABLE_INTENT_PLANNER':'0'})
        p.start();self.addCleanup(p.stop)

    def test_desire_prefix_single_property_is_direct_and_consumes_the_name(self):
        for q in ('想知道连翘的药用部位','我想知道连翘的药用部位？','请查看连翘的药用部位。'):
            result=resolve(q)
            self.assertEqual(result['tool'],'herb',q)
            self.assertEqual(result['request'],{'name':'连翘','field':'medicinal_part','resolve_unique_name':True})

    def test_nature_and_flavor_both_retained_with_four_user_attributes(self):
        for q in ('查看白术的药用部位、科名、性味、归经','查看白术的药用部位和科名以及性味与归经。'):
            result=resolve(q)
            self.assertEqual(result['tool'],'herb',q)
            self.assertEqual(result['request']['name'],'白术')
            self.assertEqual(result['request']['fields'],['medicinal_part','family','properties','flavors','meridians'])
        task=field_tasks('ETCM白术的性味')[0]
        self.assertEqual(task['arguments']['fields'],['properties','flavors'])

    def test_explicit_source_and_shared_field_defaults_are_preserved(self):
        for q in ('白术的性味','白术的归经'):
            self.assertEqual(resolve(q)['tool'],'herb',q)
        for q in ('药典里白术的药用部位','查看白术的药典药用部位'):
            self.assertEqual(resolve(q)['tool'],'research',q)
        self.assertEqual(field_tasks('药典白术的性味')[0]['arguments']['fields'],['性味与归经'])

    def test_single_field_early_route_never_steals_context_pronouns(self):
        for q in ('它的药用部位','这味药的药用部位'):
            self.assertEqual(resolve(q,research_context={'name':'白术'})['tool'],'research')
            result=resolve(q,herb_context={'status':'selected'})
            self.assertEqual(result['tool'],'herb');self.assertTrue(result['request']['followup'])

    def test_unknown_requirements_negation_and_preparations_not_narrowed(self):
        for q in ('查看白术的科名和采购价格','查看白术的科名，预测新靶点','不要查询白术的药用部位'):
            self.assertIsNone(field_tasks(q),q)
            self.assertNotEqual((resolve(q) or {}).get('reason'),'structured_field_query')
        self.assertEqual(resolve('酒白术的药用部位')['request']['name'],'酒白术')
        self.assertEqual(resolve('白术提取物的药用部位')['request']['name'],'白术提取物')


class FailureTests(unittest.TestCase):
    setUp=research_fixture.ResearchChatTests.setUp
    tearDown=research_fixture.ResearchChatTests.tearDown

    def test_actual_retrieval_distinguishes_boundary_name_and_field_failures(self):
        tool=ResearchTool(self.service)
        cases=[('想知道示例叶炭的功效','name_boundary_unresolved','name_resolution'),
               ('未知药的功效','name_not_found','name_resolution'),
               ('示例叶炭的注意事项','field_not_recorded','field_resolution'),
               ('示例叶炭价格','field_not_resolved','field_resolution'),
               ('示例叶的用量','ambiguous_field','field_resolution'),
               ('酒示例叶的用量','preparation_scope_unresolved','name_resolution'),
               ('它的用量','entity_required','name_resolution')]
        for q,code,stage in cases:
            result,_=tool.execute(q,None)
            self.assertEqual(result['failure']['code'],code,(q,result))
            self.assertEqual(result['failure']['stage'],stage)
            self.assertFalse(result['failure']['automatic_retry']);self.assertEqual(result['llm_calls'],0)
        result,_=tool.execute('示例叶炭的功效',None)
        self.assertEqual(result['status'],'candidates');self.assertNotIn('failure',result)

    def test_empty_report_herb_is_not_itself_proof_of_parse_failure(self):
        result=with_failure({'status':'name_not_resolved','message':'test',
                             'tool_result':{'request_interpretation':{'herb':''}}})
        self.assertEqual(result['failure']['code'],'name_not_found')
        self.assertNotIn('failure',status_result('context_cleared','cleared'))
        self.assertEqual(status_result('research_disabled','disabled')['failure']['stage'],'routing')
        self.assertEqual(status_result('research_unavailable','unavailable')['failure']['stage'],'source_read')

    def test_one_candidate_confirmation_does_not_claim_multiple_sources(self):
        result=with_failure({'status':'name_mapping_candidates','name_candidate_count':1,'message':'select'})
        self.assertEqual(result['failure']['code'],'candidate_confirmation_required')
        result=with_failure({'status':'name_mapping_candidates','name_candidate_count':2,'message':'select'})
        self.assertEqual(result['failure']['code'],'ambiguous_source')


class ChatTests(unittest.TestCase):
    setUp=chat_fixture.ChatTests.setUp
    turn=chat_fixture.ChatTests.turn

    def test_local_attribute_results_do_not_queue_or_call_any_model(self):
        for q,expected in [('想知道Alpha的药用部位',['medicinal_part']),
                           ('查看Alpha的药用部位、科名、性味、归经',
                            ['medicinal_part','family','properties','flavors','meridians'])]:
            with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Provider called')), \
                 patch('tcm.research_plan_run.execute',side_effect=AssertionError('Simple fields queued')):
                response=self.turn(q);data=response.json()
            self.assertEqual(response.status_code,200,data)
            self.assertEqual(data['tcm']['status'],'ok',data)
            self.assertEqual(data['tcm']['requested_fields'],expected)
            self.assertNotIn('failure',data);self.assertNotIn('failure',data['tcm'])
            self.assertEqual(set(data['tcm']['records'][0]['fields']),{'name_pinyin',*expected})

    def test_regular_chat_failures_are_exposed_at_top_level_and_tool(self):
        helper=research_fixture.ResearchChatTests();helper.setUp();self.addCleanup(helper.tearDown)
        p=patch.object(self.ad.research_runtime,'get_service',return_value=helper.service)
        p.start();self.addCleanup(p.stop)
        for q,tool in [('/herb UnknownHerb','tcm'),('/research 未知药的功效','research')]:
            data=self.turn(q).json()
            self.assertIn('failure',data,(q,data))
            self.assertEqual(data['failure'],data[tool]['failure'],data)
            self.assertEqual(data['failure']['code'],'name_not_found')
            self.assertEqual(data['failure']['stage'],'name_resolution')
            self.assertFalse(data['failure']['automatic_retry'])
