"""Reviewed requests over real ETCM readers using small, offline source fixtures."""
from copy import deepcopy
import os
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests import test_relation_queries as fixtures
from tcm.research_task_api import create_router
from tcm.research_task_local import LocalRelationTaskEngine
from tcm.research_task_store import ResearchTaskStore
from tcm.task_tools import LocalTaskTools

NAMES=['测试甲草','测试乙草']
QUESTION='测试甲草和测试乙草有哪些共同靶点？请保留双方来源记录。'
SCOPE={'data_mode':'etcm_local','entities':NAMES,'deliverables':['source_relations']}
PLAN={'intent':'source_relations','herb_one':NAMES[0],'herb_two':NAMES[1]}


class LocalRequestTests(unittest.TestCase):
    def setUp(self):
        fixtures.RelationQueryTests.setUp(self)
        self.store=ResearchTaskStore(self.root/'tasks.sqlite3')
        env=patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.store.path)})
        env.start();self.addCleanup(env.stop)
        memory=ConversationStore(self.root/'chat.sqlite3');app=FastAPI()
        app.include_router(create_router(lambda:memory,lambda:'chat'))
        self.client=TestClient(app);self.other=TestClient(app)
        self.addCleanup(self.client.close);self.addCleanup(self.other.close)

    def url(self,t):return '/api/research/tasks/'+t['id']

    def create(self,question=QUESTION):
        r=self.client.post('/api/research/tasks',json={'mode':'etcm_question','question':question})
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def review(self,t,scope=SCOPE,complete=True,client=None):
        return (client or self.client).post(self.url(t)+'/request',json={
            'revision':t['revision'],'request_scope':scope,'confirm_complete':complete})

    def choose(self,t,selection=PLAN,accept=False,client=None):
        return (client or self.client).post(self.url(t)+'/choose',json={
            'revision':t['revision'],'selection':selection,'accept_reduced_scope':accept})

    def finish(self,t):
        sources={g['name']:g['options'][0]['id'] for g in t['pending']['options']}
        r=self.choose(t,sources);self.assertEqual(r.status_code,200,r.text);t=r.json()
        if t['status']=='waiting_targets':
            r=self.choose(t,[t['pending']['options'][0]['id']]);self.assertEqual(r.status_code,200,r.text);t=r.json()
        return t

    def test_local_literal_names_and_offsets_do_not_claim_semantic_understanding(self):
        t=self.create('测试甲草和测试乙草的共同靶点，不要文献。')
        draft=t['request_draft'];self.assertEqual(draft['entities'],NAMES)
        self.assertIn('evidence_review',draft['deliverables']) # The user must correct negation.
        self.assertFalse(draft['semantic_completeness_verified'])
        for a in draft['anchors']:self.assertEqual(t['question'][a['start']:a['end']],a['text'])
        t=self.create('ALPHA 和 Beta 的共同靶点')
        self.assertEqual(t['request_draft']['entities'],['alpha','beta'])
        t=self.create('Alphabet和未知草的共同靶点')
        self.assertEqual(t['request_draft']['entities'],[])

    def test_no_relation_tool_or_model_before_inventory_and_plan_confirmation(self):
        with patch.object(LocalTaskTools,'invoke',side_effect=AssertionError('No tool before confirmation')), \
             patch('tcm.task_planner.NativeTaskPlanner.__init__',side_effect=AssertionError('No provider')):
            t=self.create();self.assertEqual(t['status'],'waiting_request');self.assertEqual(t['tool_calls'],0)
            self.assertEqual(t['external_model_calls'],0);self.assertEqual(t['names'],[])
            self.assertEqual(self.choose(t).status_code,422)
            self.assertEqual(self.client.post(self.url(t)+'/resume',json={'revision':t['revision']}).status_code,422)
            self.assertEqual(self.client.get(self.url(t)+'/report').status_code,409)
            before=self.store.get(t['id'])
            self.assertEqual(self.client.get(self.url(t)).json(),t)
            self.assertEqual(self.store.get(t['id']),before)
            r=self.review(t);self.assertEqual(r.status_code,200,r.text);t=r.json()
            self.assertEqual(t['status'],'waiting_plan');self.assertEqual(t['tool_calls'],0)
            self.assertEqual(t['pending']['options'][0]['options'][0]['id'],'source_relations')
            self.assertEqual(len(t['pending']['options'][0]['options']),1)

    def test_separate_plan_sources_targets_and_persistent_raw_provenance(self):
        t=self.review(self.create()).json();old=deepcopy(self.store.get(t['id']))
        engine=LocalRelationTaskEngine(ResearchTaskStore(self.store.path))
        self.assertEqual(engine.run(t['id']),old)
        r=self.choose(t);self.assertEqual(r.status_code,200,r.text);t=r.json()
        self.assertEqual(t['status'],'waiting_sources');self.assertEqual(t['sources'],[])
        t=self.finish(t);r=t['report']
        self.assertEqual(t['status'],'complete');self.assertTrue(r['original_request_complete'])
        self.assertTrue(r['request_inventory_user_confirmed']);self.assertFalse(r['raw_question_completeness_automatically_verified'])
        self.assertEqual(r['common_target_total'],23);self.assertEqual(r['study_evidence'],[])
        self.assertEqual({w['source_id'] for w in r['relation_evidence'][0]['witnesses']},set(t['sources']))
        export=self.client.get(self.url(t)+'/report').text
        for text in (QUESTION,'raw_row','file_sha256','确认清单全部完成：True'):self.assertIn(text,export)
        self.assertNotIn('synthetic://',export);self.assertNotIn('合成',export)

    def test_subset_keeps_third_herb_literature_table_and_manual_requirement(self):
        scope={**SCOPE,'entities':[*NAMES,'未知第三草'],
            'deliverables':['source_relations','evidence_review','medicinal_part_table'],'other_requirements':['比较价格']}
        t=self.review(self.create(QUESTION+'还要未知第三草、文献、药用部位与价格。'),scope).json()
        before=deepcopy(self.store.get(t['id']))
        self.assertEqual(self.choose(t).status_code,422);self.assertEqual(self.store.get(t['id']),before)
        r=self.choose(t,accept=True);self.assertEqual(r.status_code,200,r.text);t=self.finish(r.json())
        self.assertEqual(t['status'],'partial');self.assertTrue(t['report']['confirmed_scope_complete'])
        self.assertTrue(t['report']['local_steps_complete']);self.assertFalse(t['report']['original_request_complete'])
        for text in ('未知第三草','文献核查','药用部位','比较价格'):self.assertIn(text,' '.join(t['report']['gaps']))
        self.assertEqual(t['request_scope']['entities'],scope['entities'])
        r=self.client.post(self.url(t)+'/references',json={'revision':t['revision']})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'partial')
        self.assertFalse(r.json()['report']['original_request_complete'])

    def test_corrected_negation_and_manual_unknown_name_remain_auditable(self):
        t=self.create('测试甲草和它的共同靶点，不要文献。')
        scope={**SCOPE,'entities':[NAMES[0],'用户手填未知草']}
        t=self.review(t,scope).json()
        self.assertIn('evidence_review',t['request_review']['removed_suggestions']['deliverables'])
        selection={**PLAN,'herb_two':'用户手填未知草'}
        r=self.choose(t,selection);self.assertEqual(r.status_code,200,r.text);t=r.json()
        self.assertEqual(t['status'],'partial');self.assertIsNone(t['pending'])
        self.assertIsNone(t['report']['common_target_total']);self.assertFalse(t['report']['confirmed_scope_complete'])
        self.assertIn('未返回来源候选',' '.join(t['report']['gaps']))

    def test_empty_supported_query_complete_but_empty_subset_still_partial(self):
        with sqlite3.connect(self.database) as con:con.execute('DELETE FROM edges')
        t=self.choose(self.review(self.create()).json()).json();t=self.finish(t)
        self.assertEqual(t['status'],'complete');self.assertEqual(t['report']['completion']['outcome'],'empty')
        self.assertTrue(t['report']['original_request_complete'])
        t=self.review(self.create(),{**SCOPE,'other_requirements':['价格比较']}).json()
        t=self.finish(self.choose(t,accept=True).json())
        self.assertEqual(t['status'],'partial');self.assertTrue(t['report']['confirmed_scope_complete'])
        self.assertIn('价格比较',' '.join(t['report']['gaps']))

    def test_extra_names_unsupported_plan_and_unrequested_goal_cannot_execute(self):
        t=self.review(self.create()).json();before=self.store.get(t['id'])
        for plan in ({**PLAN,'intent':'evidence_review'},{**PLAN,'herb_two':'外来药材'},
                     {**PLAN,'herb_two':NAMES[0]}):
            self.assertEqual(self.choose(t,plan,True).status_code,422)
            self.assertEqual(self.store.get(t['id']),before)
        t=self.review(self.create(),{**SCOPE,'deliverables':[],'other_requirements':['价格比较']}).json()
        self.assertIsNone(t['plan']['intent']);self.assertEqual(self.choose(t,accept=True).status_code,422)

    def test_strict_inventory_confirmation_modes_and_size_limits(self):
        t=self.create();before=self.store.get(t['id'])
        for invalid in ({**SCOPE,'data_mode':'synthetic_only'},{**SCOPE,'entities':[NAMES[0],NAMES[0]]},
                        {**SCOPE,'entities':[' ',NAMES[1]]},{**SCOPE,'entities':['x'*101,NAMES[1]]},
                        {**SCOPE,'entities':[str(i) for i in range(9)]},{**SCOPE,'complete':True}):
            self.assertEqual(self.review(t,invalid).status_code,422)
        for complete in (False,'true'):self.assertEqual(self.review(t,complete=complete).status_code,422)
        self.assertEqual(self.store.get(t['id']),before)
        for invalid in ({'mode':'etcm_question','question':QUESTION,'names':NAMES},
                        {'mode':'etcm_question','question':QUESTION,'profile':'direct'},
                        {'mode':'synthetic_question','question':QUESTION,'request_scope':SCOPE}):
            self.assertEqual(self.client.post('/api/research/tasks',json=invalid).status_code,422)
        synthetic=self.client.post('/api/research/tasks',json={'mode':'synthetic_question','question':'示例甲草和示例乙草'}).json()
        self.assertEqual(self.review(synthetic).status_code,422)

    def test_owner_stale_revision_and_cross_site_cannot_replace_inventory(self):
        t=self.create()
        self.assertEqual(self.review(t,client=self.other).status_code,404)
        r=self.review(t);self.assertEqual(r.status_code,200,r.text);reviewed=r.json()
        self.assertEqual(self.review(t).status_code,409)
        self.assertEqual(self.choose(reviewed,client=self.other).status_code,404)
        self.assertEqual(self.client.get(self.url(t)).json(),reviewed)
        self.assertEqual(self.client.post('/api/research/tasks',json={'mode':'etcm_question','question':QUESTION},
            headers={'Origin':'https://foreign.invalid'}).status_code,403)

    def test_snapshot_change_before_review_or_plan_preserves_original_task(self):
        first=self.create();second=self.review(self.create()).json()
        before=[self.store.get(t['id']) for t in (first,second)]
        with sqlite3.connect(self.database) as con:con.execute('DELETE FROM edges')
        self.assertEqual(self.review(first).status_code,409)
        self.assertEqual(self.choose(second).status_code,409)
        for old in before:self.assertEqual(self.store.get(old['id']),old)

    def test_changed_inventory_or_plan_cannot_dispatch_after_restart(self):
        t=self.choose(self.review(self.create()).json()).json()
        raw=self.store.get(t['id']);raw['status']='running';raw['request_scope']['other_requirements']=['未确认新增项']
        self.store.save(raw);engine=LocalRelationTaskEngine(self.store)
        with patch.object(LocalTaskTools,'invoke',side_effect=AssertionError('Must not dispatch')):
            with self.assertRaises(ValueError):engine.run(t['id'])
        raw['request_scope']=deepcopy(raw['request_review']['confirmed_scope']);raw['plan']['herb_two']='外来药材'
        self.store.save(raw)
        with patch.object(LocalTaskTools,'invoke',side_effect=AssertionError('Must not dispatch')):
            with self.assertRaises(ValueError):engine.run(t['id'])


if __name__=='__main__':unittest.main()
