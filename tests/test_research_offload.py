"""Production graph, local subprocess, private HTTP reads and backup consistency."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from fastapi import FastAPI
from langchain_core.messages import AIMessage,ToolMessage
from tests.large_observation_fixture import fixture,MARKER
from tests.research_offload_fixture import LargeBusinessFixture
from tcm.research_run import run,Limits
from tcm.research_run_durable import run_durable
from tcm.research_run_local import DeclaredActionsDriver,run_local_actions
from tcm.research_run_scope import RunScope
from tcm.research_evidence_store import RunEvidence,for_run,canonical,EvidencePolicy
from tcm.research_run_store import RunStore
from tcm.research_run_api import create_router
from tcm.research_evidence_read import read

SID='etcm-disease:'+'a'*64
def scope():return RunScope.model_validate({'requirements':[{'id':'profile','call':{'tool':'disease_gene_profile','arguments':{'name':SID}}}]})


class Driver:
    def __init__(self):self.input_bytes=[];self.marker=None
    def bind_tools(self,*a,**kw):return self
    async def ainvoke(self,messages):
        self.input_bytes.append(len(canonical([m.model_dump(mode='json') for m in messages]).encode()))
        values=[json.loads(m.content) for m in messages if isinstance(m,ToolMessage)]
        if not values:name,args='disease_gene_profile',{'name':SID}
        elif len(values)==1:name,args='read_run_evidence',{'evidence_id':'obs-1','field':'target_ids','offset':7000,'limit':1}
        else:
            self.marker=values[-1]['data']['items']
            name,args='finish_research',{'status':'complete','evidence_ids':['obs-1'],'unresolved':[]}
        return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':'step-'+str(len(values))}])


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_loop_reads_middle_without_requery_and_counts_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            session=RunEvidence(Path(tmp)/'evidence.sqlite3','alice','native-run')
            model=Driver();backend=AsyncMock(return_value=fixture(14000))
            r=await run('Synthetic source',model,backend,scope=scope(),evidence_store=session)
            self.assertEqual(r['status'],'complete',r);self.assertEqual(model.marker,[MARKER])
            self.assertEqual((r['model_calls'],r['tool_calls'],backend.await_count),(3,2,1))
            self.assertLess(max(model.input_bytes),24000)
            self.assertTrue(r['evidence']['obs-1']['externalized'])
            self.assertEqual(r['evidence']['obs-2']['data']['content_sha256'],r['evidence']['obs-1']['content_sha256'])

    async def test_unbound_runs_keep_legacy_guard_and_write_failure_is_not_success(self):
        backend=AsyncMock(return_value=fixture(14000))
        r=await run('q',DeclaredActionsDriver(scope()),backend,scope=scope())
        self.assertEqual(r['status'],'partial');self.assertEqual(r['evidence']['obs-1']['status'],'error')
        with tempfile.TemporaryDirectory() as tmp:
            session=RunEvidence(Path(tmp)/'evidence.sqlite3','alice','run')
            with patch.object(session.store,'put',side_effect=sqlite3.OperationalError('synthetic disk failure')):
                r=await run('q',DeclaredActionsDriver(scope()),backend,scope=scope(),evidence_store=session)
            self.assertEqual(r['reason'],'evidence_write_failed');self.assertEqual(r['evidence'],{})

    async def test_saved_reference_and_saved_action_resume_without_repeat(self):
        class Crash(BaseException):pass
        for boundary in ('reference','action'):
            with tempfile.TemporaryDirectory() as tmp:
                options=dict(path=Path(tmp)/'journal.sqlite3',owner='alice',identifier='run-resume-001',contract='synthetic-v1',scope=scope())
                backend=AsyncMock(return_value=fixture(14000));model=Driver()
                async def stop(s):
                    if (boundary=='reference' and s['last_node']=='execute' and s['tool_calls']==1 or
                        boundary=='action' and s['last_node']=='decide' and s['model_calls']==2):raise Crash()
                with self.assertRaises(Crash):await run_durable('q',model,backend,on_state=stop,**options)
                r=await run_durable('q',model,backend,resume=True,**options)
                self.assertEqual(r['status'],'complete');self.assertEqual(backend.await_count,1)
                self.assertEqual(model.marker,[MARKER])

    async def test_missing_terminal_evidence_invalidates_completion_without_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            options=dict(path=Path(tmp)/'journal.sqlite3',owner='alice',identifier='terminal-run-001',contract='synthetic-v1',scope=scope())
            backend=AsyncMock(return_value=fixture(14000));model=Driver()
            await run_durable('q',model,backend,**options)
            with sqlite3.connect(Path(tmp)/'evidence.sqlite3') as db:db.execute('DELETE FROM evidence')
            r=await run_durable('q',model,backend,resume=True,**options)
            self.assertEqual((r['status'],r['reason']),('partial','evidence_unavailable'))
            self.assertEqual(r['evidence'],{});self.assertFalse(r['coverage']['complete']);self.assertEqual(backend.await_count,1)

    async def test_host_supplied_store_does_not_create_an_unused_default_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);custom=RunEvidence(root/'private/evidence.sqlite3','alice','custom-store-001')
            r=await run_durable('q',Driver(),AsyncMock(return_value=fixture(14000)),
                path=root/'journal.sqlite3',owner='alice',identifier='custom-store-001',
                contract='synthetic-v1',scope=scope(),evidence_store=custom)
            self.assertEqual(r['status'],'complete');self.assertFalse((root/'evidence.sqlite3').exists())

    async def test_five_actual_local_tools_with_large_profile_and_ranking(self):
        with LargeBusinessFixture() as f:
            s=f.scope().model_dump();s['requirements'][1]['all_pages']=False
            session=for_run(f.root/'runs.sqlite3','alice','real-local-run')
            before={p:p.read_bytes() for p in (f.diseases,f.herbs.base,f.herbs.database)}
            r=await run_local_actions(f.diseases,s,herbs=f.herbs.base,enrichment=f.herbs.database,evidence_store=session)
            self.assertEqual(r['status'],'complete',r)
            external=[(eid,p) for eid,p in r['run']['evidence'].items() if p.get('externalized')]
            self.assertEqual({p['tool'] for _,p in external},{'disease_gene_profile','rank_herbs_for_gene_set'})
            for eid,p in external:self.assertEqual(len(session.resolve(eid,p)['data']['target_ids']),14000)
            self.assertTrue(all(p.read_bytes()==v for p,v in before.items()))
            self.assertLess(len(canonical(r).encode()),48000)

    async def test_nested_candidate_reads_preserve_identity_and_bounds(self):
        with LargeBusinessFixture() as f:
            from tcm.analysis_tools import invoke
            from tcm.disease_catalog import lookup
            sid=lookup(f.diseases,'Synthetic disease')['record_id']
            payload=invoke({'tool':'rank_herbs_for_gene_set','arguments':{'name':sid}}).model_dump()
            first=read({'obs-1':payload},{'evidence_id':'obs-1','field':'query_only_ids','item_index':0,'offset':7000,'limit':2})
            self.assertEqual(first['status'],'ok');self.assertEqual(first['data']['item_index'],0)
            self.assertEqual(first['data']['items'],payload['data']['items'][0]['query_only_ids'][7000:7002])
            self.assertEqual(read({'obs-1':payload},{'evidence_id':'obs-1','field':'rows','item_index':0})['status'],'error')


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=LargeBusinessFixture();self.f=self.fixture.__enter__();self.addCleanup(self.fixture.__exit__,None,None,None)
        self.run_db=self.f.root/'runs.sqlite3'
        self.env=patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_RUN_DB':str(self.run_db)})
        self.env.start();self.addCleanup(self.env.stop)
        class Memory:
            def valid_cookie(self,value):return value in ('alice','bob')
        app=FastAPI();app.include_router(create_router(Memory,lambda:'chat'))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',cookies={'chat':'alice'})
        self.addAsyncCleanup(self.client.aclose)

    async def start(self):
        response=await self.client.post('/api/research/runs/profile',json={'disease':'Synthetic disease'})
        self.assertEqual(response.status_code,200,response.text)
        r=response.json();self.assertEqual(r['status'],'complete',r)
        self.assertTrue(r['run']['evidence']['obs-1']['externalized'])
        return r,'/api/research/runs/'+r['run_id']+'/evidence/obs-1'

    async def test_private_read_download_and_refresh_without_tool_dispatch(self):
        result,url=await self.start()
        with patch('tcm.research_run_api.run_local_profile',new=AsyncMock(side_effect=AssertionError('must not requery'))):
            page=await self.client.get(url,params={'field':'target_ids','offset':7000,'limit':1})
            self.assertEqual(page.json()['data']['items'],['P07000'])
            self.assertEqual(page.headers['cache-control'],'no-store')
            downloaded=await self.client.get(url+'/download')
            self.assertEqual(len(downloaded.json()['data']['target_ids']),14000)
            self.assertIn('attachment',downloaded.headers['content-disposition'])
            self.assertEqual((await self.client.get('/api/research/runs/'+result['run_id'])).status_code,200)
        for params in ({'field':'target_ids','limit':21},{'field':'rows','offset':-1},{'field':'rows','path':'/tmp'},{'field':'rows','evidence_id':'obs-2'}):
            self.assertEqual((await self.client.get(url,params=params)).status_code,422)
        self.client.cookies.set('chat','bob')
        self.assertEqual((await self.client.get(url,params={'field':'target_ids'})).status_code,404)
        self.assertEqual((await self.client.get(url+'/download')).status_code,404)

    async def test_source_change_and_missing_evidence_cannot_return_stale_success(self):
        result,url=await self.start()
        evidence=self.run_db.with_name('evidence.sqlite3')
        with sqlite3.connect(evidence) as db:db.execute('DELETE FROM evidence')
        for suffix in ('?field=target_ids','/download'):
            self.assertEqual((await self.client.get(url+suffix)).status_code,409)
        self.assertEqual((await self.client.get('/api/research/runs/'+result['run_id'])).status_code,409)

    async def test_changed_database_requires_new_query(self):
        result,url=await self.start()
        with sqlite3.connect(self.f.diseases) as db:db.execute('PRAGMA user_version=42')
        self.assertEqual((await self.client.get(url,params={'field':'target_ids'})).status_code,409)

    async def test_owner_can_delete_terminal_history_and_its_payload(self):
        result,url=await self.start()
        self.assertEqual((await self.client.delete('/api/research/runs/'+result['run_id'])).status_code,200)
        with sqlite3.connect(self.run_db.with_name('evidence.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)


class BackupTests(unittest.TestCase):
    def setUp(self):
        from tests.test_runtime_backup import RuntimeBackupTests
        self.f=RuntimeBackupTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.run_db=self.f.root/'runs.sqlite3';store=RunStore(self.run_db)
        self.doc=store.create(self.f.owner,'Synthetic large evidence')
        session=for_run(self.run_db,self.f.owner,self.doc['id'])
        self.ref=session.prepare('obs-1',fixture(14000))
        store.save(self.f.owner,self.doc,status='complete',result={'run':{'evidence':{'obs-1':self.ref}}})
        self.f.files.update(runs=self.run_db,evidence=session.store.path)

    def test_bundle_contains_both_published_refs_and_content_after_restore(self):
        from tcm.runtime_backup import restore_bundle,inspect_bundle
        self.f.backup();target=self.f.root/'restored'
        restore_bundle(self.f.bundle,target)
        restored=RunStore(target/'runs.sqlite3').get(self.f.owner,self.doc['id'])
        session=for_run(target/'runs.sqlite3',self.f.owner,self.doc['id'])
        self.assertEqual(session.resolve('obs-1',restored['result']['run']['evidence']['obs-1'])['data']['target_ids'][7000],MARKER)
        self.assertIn('evidence.sqlite3',inspect_bundle(self.f.bundle)['files'])

    def test_backup_rejects_missing_or_wrong_evidence_and_cleans_partial_bundle(self):
        from tcm.runtime_backup import RuntimeBackupError
        self.f.files.pop('evidence')
        with self.assertRaisesRegex(RuntimeBackupError,'referenced_evidence_required'):self.f.backup()
        self.assertFalse(self.f.bundle.exists())
        other=self.f.root/'wrong-evidence.sqlite3';RunEvidence(other,'another','different')
        self.f.files['evidence']=other
        with self.assertRaisesRegex(RuntimeBackupError,'published_evidence_unavailable'):self.f.backup()
        self.assertFalse(self.f.bundle.exists())
