"""Plan clarification persists without tools and cannot expand confirmed scope."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests import test_research_task_api as api_fixtures
from agents.conversation_store import ConversationStore
from tcm.research_task import ResearchTaskEngine, ScriptedToolModel
from tcm.research_task_fixtures import SyntheticResearchTools, NAMES
from tcm.research_task_store import ResearchTaskStore
from tcm.task_plan import TaskPlan


class TaskPlanTests(unittest.TestCase):
    def setUp(self):api_fixtures.TaskApiTests.setUp(self)

    def create(self,plan=None,profile='direct'):
        r=self.client.post('/api/research/tasks',json={'mode':'synthetic_plan','plan':plan or {},'profile':profile})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def choose(self,t,selection,client=None):
        return (client or self.client).post('/api/research/tasks/'+t['id']+'/choose',json={'revision':t['revision'],'selection':selection})

    def ready(self,intent='source_relations',profile='direct'):
        return self.create({'intent':intent,'herb_one':NAMES[0],'herb_two':NAMES[1]},profile)

    def targets(self,t):
        return self.choose(t,{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'}).json()

    def test_missing_plan_pauses_with_zero_tools_and_refresh_does_not_mutate(self):
        with patch.object(SyntheticResearchTools,'sources',side_effect=AssertionError('Must pause')):
            t=self.create();self.assertEqual(t['status'],'waiting_plan');self.assertEqual(t['tool_calls'],0)
            self.assertEqual(len(t['pending']['options']),3)
            store=ResearchTaskStore(self.path);before=store.get(t['id'])
            for _ in range(2):self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),t)
            self.assertEqual(store.get(t['id']),before);self.assertEqual(before['model_calls'],0)

    def test_confirm_only_saved_missing_slots_and_reopen_store_before_continuing(self):
        t=self.create({'intent':'source_relations','herb_one':NAMES[0]})
        self.assertEqual([g['name'] for g in t['pending']['options']],['herb_two'])
        store=ResearchTaskStore(self.path);before=store.get(t['id'])
        for selection in ({'herb_two':NAMES[1],'intent':'evidence_review'}, {'herb_two':NAMES[0]}, {}, []):
            self.assertEqual(self.choose(t,selection).status_code,422)
            self.assertEqual(store.get(t['id']),before)
        self.memory=ConversationStore(self.memory.path)
        t=self.choose(t,{'herb_two':NAMES[1]}).json()
        self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['tool_calls'],1)
        self.assertEqual(t['plan']['intent'],'source_relations');self.assertEqual(t['names'],NAMES)

    def test_complete_plan_does_not_auto_choose_sources_or_targets(self):
        t=self.ready();self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['sources'],[])
        t=self.targets(t);self.assertEqual(t['status'],'waiting_targets');self.assertEqual(t['targets'],[])

    def test_invalid_plan_inputs_cannot_enable_real_data_or_arbitrary_tools(self):
        for payload in ({'data_mode':'etcm_local'},{'intent':'diagnose'}, {'herb_one':'白术'},
                {'tool_names':['delete_data']},{'version':2},{'herb_one':NAMES[0],'herb_two':NAMES[0]}):
            r=self.client.post('/api/research/tasks',json={'mode':'synthetic_plan','plan':payload})
            self.assertEqual(r.status_code,422,r.text)
        for body in ({'mode':'etcm_local','names':['白术','黄芪'],'plan':{}},
                     {'mode':'synthetic','plan':{}}, {'mode':'synthetic_plan'},
                     {'mode':'synthetic_plan','plan':{},'names':['白术','黄芪']}):
            self.assertEqual(self.client.post('/api/research/tasks',json=body).status_code,422)

    def test_relation_plan_never_searches_and_exports_selected_scope(self):
        with patch.object(SyntheticResearchTools,'search',side_effect=AssertionError('Outside plan')):
            t=self.targets(self.ready());t=self.choose(t,['SYN-T1']).json()
        self.assertEqual(t['status'],'complete');self.assertEqual(t['report']['gaps'],[])
        self.assertEqual([r['id'] for r in t['report']['relation_evidence']],['SYN-T1'])
        self.assertEqual(t['report']['study_evidence'],[]);self.assertFalse(t['report']['research_evidence_verified'])
        self.assertEqual([a['name'] for a in t['actions'] if a['kind']=='tool'],['resolve_sources','common_targets','finish_task'])
        exported=self.client.get('/api/research/tasks/'+t['id']+'/report').text
        self.assertIn('用户确认的任务计划',exported);self.assertIn('source_relations',exported)
        self.assertIn('raw_row',exported)

    def test_empty_relation_query_completes_but_empty_research_task_does_not(self):
        t=self.targets(self.ready(profile='no_common'))
        self.assertEqual(t['status'],'complete');self.assertEqual(t['report']['outcome'],'empty')
        t=self.targets(self.ready('evidence_review','no_common'));self.assertEqual(t['status'],'partial')
        old=self.client.post('/api/research/tasks',json={'profile':'no_common'}).json()
        old=self.targets(old);self.assertEqual(old['status'],'partial');self.assertNotIn('plan',old)

    def test_existing_evidence_review_and_ingredient_boundary_remain(self):
        for profile,status in [('direct','complete'),('ingredient_only','partial')]:
            t=self.targets(self.ready('evidence_review',profile));t=self.choose(t,['SYN-T1']).json()
            self.assertEqual(t['status'],status);self.assertTrue(t['report']['study_evidence'])

    def test_stale_confirmation_and_other_owner_cannot_change_plan(self):
        t=self.create({'herb_one':NAMES[0],'herb_two':NAMES[1]})
        selection={'intent':'source_relations'}
        self.assertEqual(self.choose(t,selection,self.other).status_code,404)
        confirmed=self.choose(t,selection).json();self.assertEqual(confirmed['status'],'waiting_sources')
        self.assertEqual(self.choose(t,selection).status_code,409)
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json(),confirmed)

    def test_resume_cannot_bypass_clarification(self):
        t=self.create();url='/api/research/tasks/'+t['id']
        self.assertEqual(self.client.post(url+'/resume',json={'revision':t['revision']}).status_code,422)
        self.assertEqual(self.client.get(url).json(),t)
        self.assertEqual(self.client.get(url+'/report').status_code,409)

    def test_engine_enforces_tool_scope_even_for_bad_controller(self):
        store=ResearchTaskStore(self.path);engine=ResearchTaskEngine(store,ScriptedToolModel(),SyntheticResearchTools('direct'))
        draft=engine.create(plan={})
        with self.assertRaises(ValueError):engine.execute(draft,'resolve_sources',{'names':NAMES})
        t=engine.create(plan=TaskPlan(intent='source_relations',herb_one=NAMES[0],herb_two=NAMES[1]).model_dump())
        t=engine.run(t['id']);t=engine.choose(t['id'],{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},revision=t['revision'])
        t=engine.run(t['id']);t=engine.choose(t['id'],['SYN-T1'],revision=t['revision'])
        before=deepcopy(t)
        with self.assertRaises(ValueError):engine.execute(t,'search_literature',{'target_id':'SYN-T1','variant':'initial'})
        self.assertEqual(t,before)


if __name__=='__main__':unittest.main()
