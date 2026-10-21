"""Whole-request completion and explicit scope reduction are independent of a model."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests import test_research_task_api as fixtures
from agents.conversation_store import ConversationStore
from tcm.research_task import ResearchTaskEngine,ScriptedToolModel
from tcm.research_task_fixtures import NAMES,SyntheticResearchTools
from tcm.research_task_store import ResearchTaskStore
from tcm.request_coverage import RequestScope,assess_coverage
from tcm.task_planner import PlanningResult,create_proposal
from tcm.task_plan import TaskPlan

PLAN={'intent':'source_relations','herb_one':NAMES[0],'herb_two':NAMES[1]}
FULL={'entities':NAMES,'deliverables':['source_relations']}
REDUCED={'entities':[*NAMES,'示例丙草'],'deliverables':['source_relations','medicinal_part_table']}


class RequestCoverageTests(unittest.TestCase):
    def setUp(self):fixtures.TaskApiTests.setUp(self)

    def create(self,request=REDUCED,plan=PLAN,profile='direct'):
        r=self.client.post('/api/research/tasks',json={'mode':'synthetic_plan','plan':plan,'request_scope':request,'profile':profile})
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def choose(self,t,selection,accept=False,client=None):
        return (client or self.client).post('/api/research/tasks/'+t['id']+'/choose',
            json={'revision':t['revision'],'selection':selection,'accept_reduced_scope':accept})

    def finish(self,t):
        t=self.choose(t,{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'}).json()
        if t['status']=='waiting_targets':t=self.choose(t,['SYN-T1']).json()
        return t

    def test_missing_scope_requires_explicit_choice_before_any_tools_and_get_is_read_only(self):
        with patch.object(SyntheticResearchTools,'sources',side_effect=AssertionError('Do not execute')):
            t=self.create();self.assertEqual(t['status'],'waiting_plan');self.assertEqual(t['tool_calls'],0)
            store=ResearchTaskStore(self.path);before=store.get(t['id'])
            r=self.choose(t,PLAN);self.assertEqual(r.status_code,422);self.assertIn('未覆盖',r.text)
            self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),t)
            self.assertEqual(store.get(t['id']),before)
            self.assertEqual(self.client.post('/api/research/tasks/'+t['id']+'/resume',json={'revision':t['revision']}).status_code,422)

    def test_accepted_reduction_persists_and_whole_request_remains_partial(self):
        t=self.create();t=self.choose(t,PLAN,True).json();self.assertEqual(t['status'],'waiting_sources')
        self.assertTrue(t['request_coverage']['reduction_accepted']);self.assertEqual(t['request_scope']['entities'],REDUCED['entities'])
        self.memory=ConversationStore(self.memory.path);t=self.finish(t)
        self.assertEqual(t['status'],'partial');r=t['report']
        self.assertTrue(r['confirmed_scope_complete']);self.assertFalse(r['original_request_complete']);self.assertFalse(r['complete'])
        self.assertIn('示例丙草',''.join(r['gaps']));self.assertIn('药用部位',''.join(r['gaps']))
        self.assertEqual(r['request_coverage']['original_request']['entities'],REDUCED['entities'])
        confirmation=next(e for e in t['actions'] if e.get('scope_acceptance'))
        self.assertTrue(confirmation['scope_acceptance']['reduced'])
        export=self.client.get('/api/research/tasks/'+t['id']+'/report').text
        self.assertIn('原请求全部完成：False',export);self.assertIn('示例丙草',export)

    def test_full_coverage_completes_without_scope_reduction(self):
        t=self.create(FULL);self.assertEqual(t['status'],'waiting_sources')
        t=self.finish(t);self.assertEqual(t['status'],'complete')
        self.assertTrue(t['report']['confirmed_scope_complete']);self.assertTrue(t['report']['original_request_complete'])
        self.assertFalse(t['request_coverage']['needs_reduction'])

    def test_empty_subset_does_not_mark_original_request_complete(self):
        t=self.choose(self.create(profile='no_common'),PLAN,True).json();t=self.finish(t)
        self.assertEqual(t['status'],'partial');self.assertEqual(t['report']['outcome'],'empty')
        self.assertTrue(t['report']['confirmed_scope_complete']);self.assertFalse(t['report']['original_request_complete'])

    def test_evidence_gaps_stay_separate_from_scope_coverage(self):
        plan={**PLAN,'intent':'evidence_review'}
        t=self.create({'entities':NAMES,'deliverables':['evidence_review']},plan,'wrong_object');t=self.finish(t)
        self.assertEqual(t['status'],'partial');self.assertFalse(t['report']['confirmed_scope_complete'])
        self.assertFalse(t['request_coverage']['needs_reduction']);self.assertTrue(t['report']['gaps'])

    def test_scope_acknowledgement_cannot_authorize_extra_tasks_or_entities(self):
        for request,plan in [(FULL,{**PLAN,'intent':'evidence_review'}),
                             ({'entities':[NAMES[0],'示例丙草'],'deliverables':['source_relations']},PLAN)]:
            t=self.create(request,plan);before=ResearchTaskStore(self.path).get(t['id'])
            self.assertEqual(self.choose(t,plan,True).status_code,422)
            self.assertEqual(ResearchTaskStore(self.path).get(t['id']),before)

    def test_plan_change_invalidates_prior_scope_acceptance_before_dispatch(self):
        request={'entities':NAMES,'deliverables':['evidence_review']}
        t=self.create(request);t=self.choose(t,PLAN,True).json()
        store=ResearchTaskStore(self.path);raw=store.get(t['id']);raw['plan']['intent']='evidence_review';raw['status']='running';store.save(raw)
        self.assertFalse(assess_coverage(raw)['acceptance_matches_plan']);self.assertFalse(assess_coverage(raw)['can_execute'])
        engine=ResearchTaskEngine(store,ScriptedToolModel(),SyntheticResearchTools('direct'))
        with patch.object(SyntheticResearchTools,'sources',side_effect=AssertionError('Must stop before dispatch')):
            with self.assertRaises(ValueError):engine.run(t['id'])
        self.assertEqual(store.get(t['id']),raw)

    def test_scope_payload_is_strict_and_client_cannot_forge_coverage_result(self):
        for request in ({**FULL,'complete':True},{**FULL,'entities':[NAMES[0],NAMES[0]]},
                        {**FULL,'data_mode':'etcm_local'},{**FULL,'deliverables':['diagnosis']}):
            r=self.client.post('/api/research/tasks',json={'mode':'synthetic_plan','plan':PLAN,'request_scope':request})
            self.assertEqual(r.status_code,422)
        t=self.create()
        self.assertEqual(self.choose(t,PLAN,'true').status_code,422)
        self.assertEqual(self.client.post('/api/research/tasks',json={'mode':'etcm_local','names':['白术','黄芪'],'request_scope':FULL}).status_code,422)

    def test_stale_or_other_owner_cannot_approve_scope(self):
        t=self.create();self.assertEqual(self.choose(t,PLAN,True,self.other).status_code,404)
        accepted=self.choose(t,PLAN,True).json()
        self.assertEqual(self.choose(t,PLAN,True).status_code,409)
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),accepted)
        r=self.choose(accepted,{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},True)
        self.assertEqual(r.status_code,422)

    def test_goal_clarification_resolves_request_without_erasing_original(self):
        request={'entities':NAMES,'deliverables':[],'goal_to_confirm':True}
        t=self.create(request,{'herb_one':NAMES[0],'herb_two':NAMES[1]})
        t=self.choose(t,{'intent':'source_relations'}).json();t=self.finish(t)
        self.assertTrue(t['report']['original_request_complete'])
        self.assertEqual(t['request_scope']['deliverables'],[])
        self.assertEqual(t['request_coverage']['effective_request']['deliverables'],['source_relations'])

    def test_recorded_bad_proposals_are_blocked_with_independent_user_scope(self):
        for plan,request in [(TaskPlan(intent='source_relations'),{'entities':[*NAMES,'示例丙草'],'deliverables':['source_relations']}),
                             (TaskPlan(**PLAN),{'entities':NAMES,'deliverables':['source_relations','medicinal_part_table']})]:
            store=ResearchTaskStore(self.path);engine=ResearchTaskEngine(store,ScriptedToolModel(),SyntheticResearchTools('direct'))
            result=PlanningResult(status='ok',plan=plan,planner='model',calls=1,duration_ms=1.0)
            t=create_proposal(engine,'合成请求由用户另行声明，不从提案反推。',result,request_scope=request)
            self.assertEqual(engine.run(t['id'])['tool_calls'],0)
            with self.assertRaises(ValueError):engine.choose(t['id'],PLAN,revision=t['revision'])
            self.assertEqual(store.get(t['id']),t)
            t=engine.choose(t['id'],PLAN,revision=t['revision'],accept_reduced_scope=True)
            self.assertTrue(assess_coverage(t)['reduction_accepted'])


if __name__=='__main__':unittest.main()
