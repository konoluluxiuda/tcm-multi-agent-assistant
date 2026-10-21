"""Question input must pause for inventory review before any executable plan."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests import test_research_task_api as fixtures
from agents.conversation_store import ConversationStore
from tcm.research_task import ResearchTaskEngine,ScriptedToolModel
from tcm.research_task_fixtures import NAMES,SyntheticResearchTools
from tcm.research_task_store import ResearchTaskStore
from tcm.request_coverage import assess_coverage
from tcm.task_request import draft_request

QUESTION='示例甲草和示例乙草有哪些共同靶点？'
PLAN={'intent':'source_relations','herb_one':NAMES[0],'herb_two':NAMES[1]}
SCOPE={'entities':NAMES,'deliverables':['source_relations']}


class TaskRequestTests(unittest.TestCase):
    def setUp(self):fixtures.TaskApiTests.setUp(self)

    def create(self,question=QUESTION):
        r=self.client.post('/api/research/tasks',json={'mode':'synthetic_question','question':question,'profile':'direct'})
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def review(self,t,scope=SCOPE,confirmed=True,client=None):
        return (client or self.client).post('/api/research/tasks/'+t['id']+'/request',json={
            'revision':t['revision'],'request_scope':scope,'confirm_complete':confirmed})

    def choose(self,t,selection=PLAN,accept=False):
        return self.client.post('/api/research/tasks/'+t['id']+'/choose',json={
            'revision':t['revision'],'selection':selection,'accept_reduced_scope':accept})

    def finish(self,t):
        t=self.choose(t,{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'}).json()
        t=self.choose(t,['SYN-T1']).json();return t

    def test_literal_draft_keeps_missing_entities_and_provenance_not_semantic_claim(self):
        question='示例甲草和它的来源关系，不要核查研究记录。'
        draft=draft_request(question)
        self.assertEqual(draft['entities'],[NAMES[0]])
        self.assertIn('evidence_review',draft['deliverables']) # Deliberately literal, user must correct negation.
        self.assertFalse(draft['semantic_completeness_verified'])
        for anchor in draft['anchors']:self.assertEqual(question[anchor['start']:anchor['end']],anchor['text'])

    def test_question_pauses_without_tools_and_cannot_skip_review(self):
        with patch.object(SyntheticResearchTools,'sources',side_effect=AssertionError('No data reads')), patch('tcm.task_planner.NativeTaskPlanner.__init__',side_effect=AssertionError('No model')):
            t=self.create();self.assertEqual(t['status'],'waiting_request');self.assertEqual(t['tool_calls'],0)
            self.assertEqual(t['names'],[]);self.assertEqual(t['external_model_calls'],0)
            self.assertNotIn('request_scope',t)
            before=ResearchTaskStore(self.path).get(t['id'])
            self.assertEqual(self.choose(t).status_code,422)
            self.assertEqual(self.client.post('/api/research/tasks/'+t['id']+'/resume',json={'revision':t['revision']}).status_code,422)
            self.assertEqual(self.client.get('/api/research/tasks/'+t['id']+'/report').status_code,409)
            for _ in range(2):self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),t)
            self.assertEqual(ResearchTaskStore(self.path).get(t['id']),before)

    def test_explicit_complete_inventory_ack_is_required_and_does_not_execute(self):
        t=self.create();before=ResearchTaskStore(self.path).get(t['id'])
        for value in (False,'true'):
            self.assertEqual(self.review(t,confirmed=value).status_code,422)
            self.assertEqual(ResearchTaskStore(self.path).get(t['id']),before)
        r=self.review(t);self.assertEqual(r.status_code,200,r.text);t=r.json()
        self.assertEqual(t['status'],'waiting_plan');self.assertEqual(t['tool_calls'],0)
        self.assertTrue(t['request_review']['confirmed_complete']);self.assertTrue(t['plan_review_required'])
        self.assertEqual(len(t['pending']['options']),3)

    def test_user_corrected_draft_and_other_requirements_stay_in_report(self):
        question='示例甲草和示例乙草的来源关系，不要看研究记录；另外还要价格比较。'
        t=self.create(question);draft=deepcopy(t['request_draft'])
        scope={**SCOPE,'other_requirements':['价格比较']}
        t=self.review(t,scope).json();self.assertEqual(t['request_draft'],draft)
        self.assertIn('evidence_review',t['request_review']['removed_suggestions']['deliverables'])
        self.assertEqual(self.choose(t).status_code,422)
        t=self.choose(t,accept=True).json();t=self.finish(t)
        self.assertEqual(t['status'],'partial');self.assertTrue(t['report']['confirmed_scope_complete'])
        self.assertFalse(t['report']['original_request_complete'])
        self.assertFalse(t['report']['raw_question_completeness_automatically_verified'])
        self.assertIn('价格比较',''.join(t['report']['gaps']))
        content=self.client.get('/api/research/tasks/'+t['id']+'/report').text
        self.assertIn(question,content);self.assertIn('原始问句、自动草稿与用户核对',content)
        self.assertIn('确认清单全部完成：False',content)

    def test_full_reviewed_request_completes_after_separate_plan_and_source_choices(self):
        t=self.review(self.create()).json();t=self.choose(t).json()
        self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['sources'],[])
        t=self.finish(t);self.assertEqual(t['status'],'complete')
        self.assertTrue(t['report']['original_request_complete']);self.assertTrue(t['report']['request_inventory_user_confirmed'])
        self.assertFalse(t['report']['raw_question_completeness_automatically_verified'])

    def test_three_entities_and_extra_deliverable_require_subset_ack(self):
        question='比较示例甲草、示例乙草和示例丙草的共同靶点，加药用部位并排表。'
        t=self.create(question)
        self.assertEqual(len(t['request_draft']['entities']),3)
        scope={'entities':[*NAMES,'示例丙草'],'deliverables':['source_relations','medicinal_part_table']}
        t=self.review(t,scope).json();self.assertEqual(self.choose(t).status_code,422)
        t=self.choose(t,accept=True).json();t=self.finish(t)
        self.assertEqual(t['status'],'partial');self.assertIn('示例丙草',''.join(t['report']['gaps']))
        self.assertIn('药用部位',''.join(t['report']['gaps']))

    def test_other_only_request_cannot_turn_into_unrequested_supported_goal(self):
        t=self.create('比较示例甲草和示例乙草的价格。')
        scope={'entities':NAMES,'deliverables':[],'other_requirements':['价格比较']}
        t=self.review(t,scope).json();self.assertIsNone(t['plan']['intent'])
        before=ResearchTaskStore(self.path).get(t['id'])
        self.assertEqual(self.choose(t,accept=True).status_code,422)
        self.assertEqual(ResearchTaskStore(self.path).get(t['id']),before)

    def test_invalid_or_bypassing_payloads_do_not_create_or_replace_inventory(self):
        for body in ({'mode':'synthetic_question','question':QUESTION,'plan':PLAN},
                     {'mode':'synthetic_question','question':QUESTION,'request_scope':SCOPE},
                     {'mode':'etcm_local','names':['白术','黄芪'],'question':QUESTION},
                     {'mode':'synthetic_question','question':'x'*401}):
            self.assertEqual(self.client.post('/api/research/tasks',json=body).status_code,422)
        t=self.create();before=ResearchTaskStore(self.path).get(t['id'])
        for scope in ({'entities':[NAMES[0]],'deliverables':['source_relations']},
                      {'entities':NAMES,'deliverables':[]},
                      {**SCOPE,'goal_to_confirm':True},{**SCOPE,'complete':True},
                      {**SCOPE,'other_requirements':[' ']}):
            self.assertEqual(self.review(t,scope).status_code,422)
            self.assertEqual(ResearchTaskStore(self.path).get(t['id']),before)

    def test_owner_revision_and_phase_prevent_inventory_replacement(self):
        t=self.create();self.assertEqual(self.review(t,client=self.other).status_code,404)
        accepted=self.review(t).json();self.assertEqual(self.review(t).status_code,409)
        self.assertEqual(self.review(accepted).status_code,422)
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),accepted)

    def test_question_draft_and_confirmed_inventory_survive_reopen_without_reextracting(self):
        t=self.create();self.memory=ConversationStore(self.memory.path)
        with patch('tcm.research_task.draft_request',side_effect=AssertionError('GET must not extract')):
            self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),t)
        accepted=self.review(t).json();self.memory=ConversationStore(self.memory.path)
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),accepted)

    def test_unreviewed_or_changed_inventory_is_blocked_before_dispatch(self):
        t=self.create();store=ResearchTaskStore(self.path);raw=store.get(t['id']);raw['status']='running';store.save(raw)
        engine=ResearchTaskEngine(store,ScriptedToolModel(),SyntheticResearchTools('direct'))
        with self.assertRaises(ValueError):engine.run(t['id'])
        self.assertEqual(store.get(t['id']),raw)
        t=self.review(self.create()).json();raw=store.get(t['id']);raw['request_scope']['other_requirements']=['新增未确认要求'];raw['status']='running';store.save(raw)
        with self.assertRaises(ValueError):engine.run(t['id'])
        self.assertEqual(store.get(t['id']),raw)

    def test_old_scope_acceptance_normalizes_new_optional_empty_requirements(self):
        task={'request_scope':{**SCOPE,'entities':[*NAMES,'示例丙草']},'plan':{'version':1,'data_mode':'synthetic_only',**PLAN}}
        from tcm.request_coverage import confirm_scope
        from tcm.task_plan import TaskPlan
        confirm_scope(task,TaskPlan(**PLAN),accept_reduced_scope=True)
        task['scope_acceptance']['effective_request'].pop('other_requirements')
        self.assertTrue(assess_coverage(task)['can_execute'])
        task['request_scope']['other_requirements']=['未确认的新要求']
        self.assertFalse(assess_coverage(task)['can_execute'])


if __name__=='__main__':unittest.main()
