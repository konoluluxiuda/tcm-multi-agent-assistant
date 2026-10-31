from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.dataops_fixture import make_sources
from tests.rag_release_fixture import fixture
from tcm.data_release import build
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine,ResearchLimits
from tcm.dataops.research_model import ResearchSynthetic,ResearchFixed,ResearchDriver,valid
from tcm.dataops.research_sources import ReleaseSources
from tcm.dataops.workspace import Workspaces,Conflict
from tcm.dataops.artifacts import verify


class Script:
    label='research_test_script';max_response_corrections=0
    def __init__(self,actions):self.actions=actions;self.calls=[]
    def decide(self,context):
        self.calls.append(deepcopy(context));a=self.actions[min(len(self.calls)-1,len(self.actions)-1)]
        if callable(a):return a(context)
        return {'tool':a[0],'arguments':{'purpose':'测试指定动作',**a[1]}}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.records,self.sources=make_sources(self.root);self.store=Workspaces(self.root/'work')
    def create(self,names=('Alpha','Beta'),fields=('功能与主治',),target=1,strict=False,**kw):
        g=ResearchGoal(query={'names':list(names),'limit':20},attributes=['medicinal_part'],
                       document_fields=list(fields),research={'target_count':target,'monograph_only':strict})
        with self.sources() as s:
            return self.store.reserve_agent('owner',kw.pop('key','research-request'),g.model_dump(),s.revision,
                                           question='核对字段齐全的候选并保留出处',**kw)[0]
    def run_doc(self,d,driver=None,**kw):
        return ResearchEngine(self.store,'owner',d['id'],self.sources,driver or ResearchSynthetic(),**kw).run()
    def sid(self,name):return self.records[name]['source_record_id']

    def test_observation_skips_preparation_scope_and_preserves_every_cell(self):
        d=self.create(strict=True);r=self.run_doc(d)
        self.assertEqual(r['status'],'complete');self.assertEqual(r['result']['research']['selected_ids'],[self.sid('Beta')])
        self.assertFalse(r['result']['rows'][0]['identity_verified']);self.assertEqual(r['result']['research']['provider_requests'],0)
        receipt=verify(self.store.root/d['id']/r['artifact']['filename'],r['result'])
        self.assertTrue(receipt);self.assertIn('候选核对',__import__('openpyxl').load_workbook(self.store.root/d['id']/r['artifact']['filename']).sheetnames)
        self.assertEqual(r['progress']['events'][-1]['tool'],'deliver_result')

    def test_incomplete_target_is_explicit_partial_not_fabrication(self):
        r=self.run_doc(self.create(fields=('注意',),target=2))
        self.assertEqual(r['status'],'partial');self.assertEqual(r['result']['rows'],[])
        self.assertEqual(r['result']['research']['qualified_count'],0);self.assertTrue(r['artifact'])

    def test_persisted_multi_record_workbook_remains_cell_verifiable(self):
        d=self.create(target=2);r=self.run_doc(d)
        stored=self.store.get('owner',d['id'])
        self.assertEqual(stored['result']['research']['artifact_format'],'sorted-evidence-v1')
        self.assertEqual(verify(self.store.root/d['id']/r['artifact']['filename'],stored['result'])['status'],'passed')

    def test_delivery_rejects_premature_success_and_scope_escape(self):
        d=self.create();driver=Script([('list_candidates',{}),('deliver_result',{'source_ids':[self.sid('Alpha')],'allow_partial':False}),
            ('inspect_candidates',{'source_ids':[self.sid('Delta')]})])
        r=self.run_doc(d,driver)
        self.assertEqual(r['error'],'no_progress');self.assertIsNone(r['artifact'])
        self.assertEqual([e.get('reason') for e in r['progress']['events'][-2:]],['target_or_fields_incomplete','outside_candidate_scope'])

    def test_parent_v2_preserves_records_reuses_old_cells_and_keeps_v1_immutable(self):
        first=self.run_doc(self.create());p=self.store.root/first['id']/first['artifact']['filename'];before=p.read_bytes()
        second=self.create(fields=('功能与主治','注意'),key='research-v2',parent_id=first['id'],expected_revision=1)
        r=self.run_doc(second)
        self.assertEqual(r['status'],'partial');self.assertEqual(p.read_bytes(),before)
        self.assertEqual(r['result']['research']['selected_ids'],first['result']['research']['selected_ids'])
        self.assertEqual(r['result']['rows'][0]['cells']['注意']['status'],'missing')
        self.assertGreater(r['progress']['reused_cells'],0);self.assertIn('diff',r['result'])

    def test_ambiguity_wait_and_checkpoint_resume(self):
        d=self.create(('Gamma',));sid=self.sid('Gamma')
        actions=[('list_candidates',{}),('inspect_candidates',{'source_ids':[sid]}),('ask_user',{'source_id':sid}),
                 ('inspect_candidates',{'source_ids':[sid]}),('deliver_result',{'source_ids':[sid],'allow_partial':False})]
        driver=Script(actions);engine=ResearchEngine(self.store,'owner',d['id'],self.sources,driver)
        w=engine.run();self.assertEqual(w['status'],'waiting_input');p=w['pending']
        answer={'question_id':p['question_id'],'revision':w['revision'],'operation':'choose','candidate_id':p['candidates'][0]['entry_id']}
        r=engine.run(answer);self.assertEqual(r['status'],'complete');self.assertFalse(r['result']['rows'][0]['identity_verified'])

    def test_unknown_dispatch_is_not_reissued(self):
        class Broken(ResearchSynthetic):
            def __init__(self):self.requests=0
            def decide(self,ctx):self.requests+=1;raise TimeoutError('unknown')
        d=self.create();driver=Broken();r=self.run_doc(d,driver)
        self.assertEqual(r['status'],'interrupted');self.assertEqual(driver.requests,1)
        r=self.run_doc(d,driver);self.assertEqual(r['status'],'interrupted');self.assertEqual(driver.requests,1)

    def test_budget_does_not_publish_unverified_artifact(self):
        limits=ResearchLimits();limits.model_calls=1
        r=self.run_doc(self.create(),limits=limits)
        self.assertEqual(r['error'],'model_budget');self.assertIsNone(r['artifact'])

    def test_read_evidence_returns_only_task_owned_handles(self):
        d=self.create(('Beta',));sid=self.sid('Beta')
        def read(ctx):return {'tool':'read_evidence','arguments':{'purpose':'核对原文','evidence_ids':ctx['evidence_refs'][:1]}}
        driver=Script([('list_candidates',{}),('inspect_candidates',{'source_ids':[sid]}),read,
                       ('deliver_result',{'source_ids':[sid],'allow_partial':False})])
        r=self.run_doc(d,driver);self.assertEqual(r['status'],'complete')
        self.assertTrue(driver.calls[-1]['observation']['fragments'][0]['text'])
        self.assertLessEqual(len(driver.calls[-1]['observation']['fragments'][0]['text']),800)

    def test_native_contract_rejects_multi_action_reply(self):
        from langchain_core.messages import AIMessage
        class Model:
            max_retries=0;max_tokens=1200;model_name='offline'
            def bind_tools(self,*a,**kw):return self
            def invoke(self,messages):return AIMessage(content='',tool_calls=[{'name':'list_candidates','args':{'purpose':'核对'},'id':str(i)} for i in range(2)])
        d=self.create();r=self.run_doc(d,ResearchDriver(Model(),authorized=True))
        self.assertEqual(r['error'],'model_response_rejected');self.assertIsNone(r['artifact'])

    def test_contract_requires_bounded_root_and_exact_tool_args(self):
        with self.assertRaises(ValueError):ResearchGoal(query={'limit':20})
        self.assertFalse(valid('inspect_candidates',{'purpose':'核对','source_ids':['a','a']}))
        self.assertFalse(valid('deliver_result',{'purpose':'交付','source_ids':[],'allow_partial':'true'}))


class ReleaseResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        c=fixture(self.root/'sources')
        with sqlite3.connect(c['enrichment']) as db:
            db.execute('ALTER TABLE name_candidates ADD COLUMN evidence TEXT')
            for herb,eid,name in db.execute('SELECT herb_id,entry_id,name_zh FROM name_candidates').fetchall():
                db.execute('UPDATE name_candidates SET evidence=? WHERE herb_id=?',(json.dumps({'entry_id':eid,'name_zh':name}),herb))
        self.release=self.root/'release';build(c,self.release);self.sources=lambda:ReleaseSources(self.release)
    def test_release_bound_engine_and_search_filters_before_top_k(self):
        with self.sources() as s:
            g=ResearchGoal(query={'names':['Alpha','Beta','Gamma'],'limit':20},attributes=['medicinal_part'],
                           document_fields=['贮藏'],research={'target_count':2})
            store=Workspaces(self.root/'work');d,_=store.reserve_agent('owner','release-research',g.model_dump(),s.revision,question='核对两条贮藏')
            hits=s.search.search('贮藏','bm25',6,names=['甲草'],fields=['贮藏'])['hits']
            self.assertTrue(hits);self.assertTrue(all(h['name']=='甲草' and h['label']=='贮藏' for h in hits))
            with self.assertRaises(ValueError):s.search.search('乙草贮藏',names=['甲草'],fields=['贮藏'])
            with self.assertRaises(ValueError):s.search.search('甲草的注意',names=['甲草'],fields=['贮藏'])
        r=ResearchEngine(store,'owner',d['id'],self.sources,ResearchFixed()).run()
        self.assertEqual(r['status'],'complete');self.assertEqual(r['source_revision']['release_content_id'],s.revision['release_content_id'])

    def test_search_and_reread_share_exact_task_evidence(self):
        with self.sources() as s:
            g=ResearchGoal(query={'names':['Alpha'],'limit':20},attributes=['medicinal_part'],document_fields=['贮藏'],research={'target_count':1})
            sid=s.query(g.query)['selected_ids'][0];store=Workspaces(self.root/'work');d,_=store.reserve_agent('owner','release-search',g.model_dump(),s.revision,question='回读甲草贮藏')
        actions=[('list_candidates',{}),('inspect_candidates',{'source_ids':[sid]}),
                 ('search_documents',{'source_ids':[sid],'query':'甲草贮藏'}),
                 lambda c:{'tool':'read_evidence','arguments':{'purpose':'核对出处','evidence_ids':c['observation']['evidence_ids'][:1]}},
                 ('deliver_result',{'source_ids':[sid],'allow_partial':False})]
        r=ResearchEngine(store,'owner',d['id'],self.sources,Script(actions)).run()
        self.assertEqual(r['status'],'complete');self.assertEqual(r['progress']['events'][3]['fragments'][0]['text'],'【贮藏】置干燥处。')
