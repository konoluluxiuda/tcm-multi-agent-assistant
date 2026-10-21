"""Fixture ETCM task integration: real local readers, provenance, paging, no provider."""
from copy import deepcopy
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests import test_relation_queries as fixtures
from tcm.research_task_local import LocalRelationTaskEngine,LocalSnapshotChanged,LocalUnavailable,LocalBudget,snapshot
from tcm.research_task_store import ResearchTaskStore,TaskConflict
from tcm.research_task_report import markdown
from tcm.research_task_api import create_router


class LocalTaskTests(unittest.TestCase):
    def setUp(self):
        fixtures.RelationQueryTests.setUp(self)
        self.store=ResearchTaskStore(self.root/'tasks.sqlite3');self.engine=LocalRelationTaskEngine(self.store)

    def create(self,names=None):return self.engine.run(self.engine.create(owner='owner',names=names or ['测试甲草','测试乙草'])['id'])
    def sources(self,t):return {g['name']:g['options'][0]['id'] for g in t['pending']['options']}
    def to_targets(self,t=None):
        t=t or self.create();return self.engine.choose(t['id'],self.sources(t),revision=t['revision'])

    def test_persistent_two_pauses_true_local_provenance_and_no_fake_studies(self):
        before=snapshot()
        with patch('tcm.research_task.NativeToolModel.__init__',side_effect=AssertionError('No provider')):
            t=self.create();self.assertEqual(t['status'],'waiting_sources')
            self.engine=LocalRelationTaskEngine(ResearchTaskStore(self.store.path))
            t=self.to_targets(t);self.assertEqual(t['status'],'waiting_targets');self.assertEqual(t['pending']['total'],23)
            target=t['pending']['options'][0]['id'];t=self.engine.choose(t['id'],[target],revision=t['revision'])
        self.assertEqual(t['status'],'complete');self.assertEqual(t['data_mode'],'etcm_local')
        r=t['report'];self.assertTrue(r['local_steps_complete']);self.assertTrue(r['complete']);self.assertFalse(r['completion']['research_evidence_verified']);self.assertEqual(r['study_evidence'],[])
        witnesses=r['relation_evidence'][0]['witnesses'];self.assertEqual({w['source_id'] for w in witnesses},set(t['sources']))
        self.assertTrue(all(w['raw_row'] and w['json_pointer'].startswith('/data/') for w in witnesses))
        self.assertTrue(all(w['capture_status'][0]['status']=='partial' for w in witnesses))
        self.assertEqual(before,snapshot());self.assertNotIn('合成',markdown(t));self.assertIn('raw_row',markdown(t));self.assertIn('文献',markdown(t))

    def test_page_two_preserves_total_and_limits_selection_to_current_page(self):
        t=self.to_targets();first={o['id'] for o in t['pending']['options']};old=deepcopy(t)
        t=self.engine.page(t['id'],2,revision=t['revision']);self.assertEqual(t['pending']['returned_count'],3)
        self.assertEqual(t['common_target_total'],23);second={o['id'] for o in t['pending']['options']}
        self.assertFalse(first&second);self.assertFalse(t['pending']['has_next'])
        with self.assertRaises(TaskConflict):self.engine.page(t['id'],1,revision=old['revision'])
        with self.assertRaises(ValueError):self.engine.choose(t['id'],[sorted(first)[0]],revision=t['revision'])
        with self.assertRaises(ValueError):self.engine.page(t['id'],3,revision=t['revision'])
        self.assertEqual(self.store.get(t['id'])['revision'],t['revision'])
        t=self.engine.choose(t['id'],sorted(second)[:2],revision=t['revision']);self.assertEqual(len(t['report']['relation_evidence']),2)

    def test_unlisted_sources_extra_names_duplicate_sources_and_targets_refused(self):
        t=self.create();selection=self.sources(t)
        with self.assertRaises(ValueError):self.engine.choose(t['id'],{**selection,'other':'x'},revision=t['revision'])
        with self.assertRaises(ValueError):self.engine.choose(t['id'],{**selection,t['names'][0]:'foreign'},revision=t['revision'])
        self.assertEqual(self.store.get(t['id'])['revision'],t['revision'])
        t=self.to_targets(t);offered=t['pending']['options'][0]['id']
        for invalid in ([],['foreign'],[offered,offered]):
            with self.assertRaises(ValueError):self.engine.choose(t['id'],invalid,revision=t['revision'])
        t=self.create(['Alpha','alpha'])
        with self.assertRaises(ValueError):self.engine.choose(t['id'],self.sources(t),revision=t['revision'])

    def test_relation_row_change_with_unchanged_metadata_invalidates_snapshot(self):
        t=self.create();old=deepcopy(t)
        with sqlite3.connect(self.database) as con:con.execute("UPDATE edges SET raw_row='{}' WHERE edge_id=(SELECT edge_id FROM edges LIMIT 1)")
        with self.assertRaises(LocalSnapshotChanged):self.engine.choose(t['id'],self.sources(t),revision=t['revision'])
        self.assertEqual(self.store.get(t['id']),old)

    def test_version_mismatch_and_missing_catalog_are_not_empty_results(self):
        with patch.dict(os.environ,{'TCM_DATASET_VERSION':'wrong'}),self.assertRaises(LocalUnavailable):self.create()
        missing=self.root/'absent.sqlite3'
        with patch.dict(os.environ,{'ETCM_ENRICHMENT_DB':str(missing)}),self.assertRaises(LocalUnavailable):self.create()
        self.assertFalse(missing.exists())

    def test_unresolved_source_is_partial_but_empty_query_is_completed(self):
        t=self.create(['未知药材','测试乙草']);self.assertEqual(t['status'],'partial');self.assertIsNone(t['pending'])
        self.assertIn('未返回来源候选',t['report']['gaps'][0])
        with sqlite3.connect(self.database) as con:con.execute('DELETE FROM edges')
        t=self.to_targets();self.assertEqual(t['status'],'complete');self.assertEqual(t['common_target_total'],0)
        self.assertEqual(t['report']['completion']['outcome'],'empty');self.assertEqual(t['report']['gaps'],[])
        self.assertIn('不表示真实世界不存在',''.join(t['report']['limitations']))

    def test_page_budget_leaves_one_slot_to_finish_and_stale_completion_fails(self):
        t=self.to_targets()
        for _ in range(9):t=self.engine.page(t['id'],1,revision=t['revision'])
        self.assertEqual(t['tool_calls'],11)
        with self.assertRaises(LocalBudget):self.engine.page(t['id'],2,revision=t['revision'])
        old=deepcopy(t);target=t['pending']['options'][0]['id']
        t=self.engine.choose(t['id'],[target],revision=t['revision']);self.assertEqual(t['tool_calls'],12)
        with self.assertRaises(TaskConflict):self.engine.choose(t['id'],[target],revision=old['revision'])

    def test_unchanged_catalog_fingerprints_are_cached_without_caching_source_contents(self):
        from tcm.research_task_local import file_sha256
        file_sha256.cache_clear();first=snapshot();initial=file_sha256.cache_info()
        self.assertEqual(snapshot(),first)
        later=file_sha256.cache_info();self.assertEqual(later.misses,initial.misses)
        self.assertGreaterEqual(later.hits-initial.hits,2)

    def test_local_api_modes_isolation_report_and_paging(self):
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.store.path)}):
            memory=ConversationStore(self.root/'chat.sqlite3');app=FastAPI();app.include_router(create_router(lambda:memory,lambda:'chat'))
            with TestClient(app) as client,TestClient(app) as other:
                for body in ({'mode':'etcm_local'},{'mode':'etcm_local','names':['Alpha','Alpha']},{'names':['Alpha','Beta']},{'mode':'native'}):
                    self.assertEqual(client.post('/api/research/tasks',json=body).status_code,422)
                r=client.post('/api/research/tasks',json={'mode':'etcm_local','names':['测试甲草','测试乙草']});self.assertEqual(r.status_code,200,r.text)
                t=r.json();self.assertEqual(t['controller'],'local_readonly');self.assertEqual(t['external_model_calls'],0)
                url='/api/research/tasks/'+t['id']
                self.assertEqual(other.post(url+'/choose',json={'revision':t['revision'],'selection':self.sources(t)}).status_code,404)
                r=client.post(url+'/choose',json={'revision':t['revision'],'selection':self.sources(t)});self.assertEqual(r.status_code,200,r.text);t=r.json()
                r=client.post(url+'/targets-page',json={'revision':t['revision'],'page':2});self.assertEqual(r.status_code,200,r.text);t=r.json()
                target=t['pending']['options'][0]['id'];t=client.post(url+'/choose',json={'revision':t['revision'],'selection':[target]}).json()
                self.assertEqual(t['status'],'complete');self.assertEqual(len(t['report']['relation_evidence']),1)
                r=client.get(url+'/report');self.assertEqual(r.status_code,200);self.assertIn('etcm-local-task.md',r.headers['content-disposition'])
                self.assertIn('raw_row',r.text);self.assertNotIn('synthetic://',r.text)
                self.assertEqual(other.get(url+'/report').status_code,404)

    def test_pending_legacy_task_keeps_old_status_and_report_semantics(self):
        t=self.create();del t['result_version'];t.pop('task_scope');t['question']='历史任务原问题'
        self.store.save(t);t=self.to_targets(t)
        t=self.engine.choose(t['id'],[t['pending']['options'][0]['id']],revision=t['revision'])
        self.assertEqual(t['status'],'partial');self.assertFalse(t['report']['complete'])
        self.assertNotIn('completion',t['report']);self.assertNotIn('result_version',t['report'])
        self.assertEqual(t['question'],'历史任务原问题');self.assertTrue(t['report']['gaps'])
        self.assertEqual(self.engine.run(t['id']),t)

    def test_completion_requires_both_sides_and_does_not_trust_old_status_label(self):
        from tcm.task_contracts import local_relation_completion,TaskCompletion
        t=self.to_targets();target=t['pending']['options'][0]['id']
        t=self.engine.choose(t['id'],[target],revision=t['revision'])
        r=t['report'];self.assertEqual(r['completion']['outcome'],'matched');self.assertEqual(r['gaps'],[])
        self.assertIn('文献',''.join(r['limitations']))
        t['relations'][0]['witnesses']=t['relations'][0]['witnesses'][:1]
        self.assertEqual(local_relation_completion(t).status,'partial')
        with self.assertRaises(ValueError):TaskCompletion.model_validate({**r['completion'],'fulfilled_steps':[]})
        with self.assertRaises(ValueError):TaskCompletion.model_validate({**r['completion'],'research_evidence_verified':True})

    def test_get_legacy_report_does_not_rewrite_revision_or_completion(self):
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.store.path)}):
            memory=ConversationStore(self.root/'chat.sqlite3');app=FastAPI();app.include_router(create_router(lambda:memory,lambda:'chat'))
            with TestClient(app) as client:
                t=client.post('/api/research/tasks',json={'mode':'etcm_local','names':['Alpha','Beta']}).json()
                raw=self.store.get(t['id']);raw.pop('result_version');raw.pop('task_scope');self.store.save(raw)
                url='/api/research/tasks/'+t['id']
                raw=self.store.get(t['id'])
                t=client.post(url+'/choose',json={'revision':raw['revision'],'selection':self.sources(raw)}).json()
                t=client.post(url+'/choose',json={'revision':t['revision'],'selection':[t['pending']['options'][0]['id']]}).json()
                self.assertEqual(t['status'],'partial');self.assertFalse(t['report']['complete'])
                before=deepcopy(self.store.get(t['id']))
                frozen_export=markdown(before)
                response=client.get(url)
                self.assertEqual(response.status_code,200);self.assertNotIn('result_version',response.json())
                self.assertNotIn('completion',response.json()['report'])
                self.assertEqual(client.get(url+'/report').text,frozen_export)
                self.assertEqual(client.get(url+'/report').text,frozen_export)
                self.assertEqual(self.store.get(t['id']),before)

    def test_local_api_snapshot_change_returns_conflict_without_overwrite(self):
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.store.path)}):
            memory=ConversationStore(self.root/'chat.sqlite3');app=FastAPI();app.include_router(create_router(lambda:memory,lambda:'chat'))
            with TestClient(app) as client:
                t=client.post('/api/research/tasks',json={'mode':'etcm_local','names':['Alpha','Beta']}).json()
                with sqlite3.connect(self.database) as con:con.execute('DELETE FROM edges')
                r=client.post('/api/research/tasks/'+t['id']+'/choose',json={'revision':t['revision'],'selection':self.sources(t)})
                self.assertEqual(r.status_code,409,r.text)
                self.assertEqual(self.store.get(t['id'])['revision'],t['revision'])
                self.assertEqual(client.get('/api/research/tasks/'+t['id']).status_code,200)


if __name__=='__main__':unittest.main()
