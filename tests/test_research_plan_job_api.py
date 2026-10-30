"""Owner isolation, durable submission, replayable SSE and evidence HTTP contracts."""
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from tests.research_report_fixture import ReportFixture,QUESTION
from tcm.research_job_api import create_router
from tcm.research_run_api import create_router as run_router
from tcm.research_jobs import JobStore


class PlanJobApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture=ReportFixture();self.f=fixture.__enter__();self.addCleanup(fixture.__exit__,None,None,None)
        self.path=self.f.root/'runs.sqlite3';self.queue=JobStore(self.f.root/'jobs.sqlite3')
        env=patch.dict(os.environ,{'ENABLE_RESEARCH_PLAN_JOBS':'1','ENABLE_RESEARCH_TASKS':'1',
            'RESEARCH_JOB_DB':str(self.queue.path),'RESEARCH_RUN_DB':str(self.path),
            'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':''})
        env.start();self.addCleanup(env.stop)
        class Memory:
            path=self.f.root/'chat.sqlite3'
            def valid_cookie(self,value):return value in ('alice','bob')
        app=FastAPI();self.router=create_router(Memory,lambda:'chat');app.include_router(self.router)
        app.include_router(run_router(Memory,lambda:'chat'));self.addAsyncCleanup(self.router.research_worker.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',cookies={'chat':'alice'})
        self.addAsyncCleanup(self.client.aclose);self.owner=hashlib.sha256(b'alice').hexdigest()

    async def submit(self,key='plan-api-one'):
        response=await self.client.post('/api/research/jobs/plan',json={'question':QUESTION,'request_key':key})
        self.assertEqual(response.status_code,202,response.text);return response.json()['run_id']

    async def test_submit_sse_replay_restore_and_existing_evidence_routes(self):
        identifier=await self.submit()
        duplicate=await self.client.post('/api/research/jobs/plan',json={'question':QUESTION,'request_key':'plan-api-one'})
        self.assertEqual(duplicate.json()['run_id'],identifier);self.assertFalse(duplicate.json()['created'])
        stream=await self.client.get('/api/research/jobs/'+identifier+'/events')
        self.assertIn('event: done',stream.text)
        ids=[int(line[4:]) for line in stream.text.splitlines() if line.startswith('id: ')]
        self.assertEqual(ids,sorted(set(ids)))
        replay=await self.client.get('/api/research/jobs/'+identifier+'/events',headers={'Last-Event-ID':str(ids[-1])})
        self.assertIn('event: done',replay.text);self.assertNotIn('event: progress',replay.text)
        doc=(await self.client.get('/api/research/jobs/'+identifier)).json()
        self.assertEqual(doc['status'],'complete');self.assertEqual(doc['result']['run']['tool_calls'],6)
        base='/api/research/runs/'+identifier+'/evidence/obs-1'
        part=await self.client.get(base+'?field=items&limit=20');self.assertEqual(part.status_code,200,part.text)
        self.assertLessEqual(len(part.json()['data']['items']),20)
        full=await self.client.get(base+'/download');self.assertEqual(full.status_code,200,full.text)
        self.assertEqual(len(full.json()['data']['report']['targets']['items']),23)
        self.assertEqual((await self.client.get('/api/research/runs/'+identifier)).status_code,200)
        self.assertEqual((await self.client.delete('/api/research/jobs/'+identifier)).status_code,200)
        self.assertEqual((await self.client.get(base+'/download')).status_code,404)

    async def test_cross_owner_and_csrf_cannot_access_mutate_or_subscribe(self):
        identifier=await self.submit();self.client.cookies.set('chat','bob')
        for method,suffix in [('get',''),('get','/events'),('post','/cancel'),('post','/resume'),('delete','')]:
            self.assertEqual((await getattr(self.client,method)('/api/research/jobs/'+identifier+suffix)).status_code,404)
        self.assertEqual((await self.client.get('/api/research/runs/'+identifier+'/evidence/obs-1/download')).status_code,404)
        self.client.cookies.set('chat','alice')
        self.assertEqual((await self.client.post('/api/research/jobs/'+identifier+'/cancel',headers={'Origin':'https://other.invalid'})).status_code,403)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/research/jobs')).status_code,401)

    async def test_resume_requires_current_revision_and_works_without_postgres(self):
        # Don't start the router worker until the durable resume action.
        from tcm.research_plan_jobs import make_payload
        from tcm.research_plan_run import compile_scope,signature
        from tcm.research_plan_intent import local_plan
        plan=local_plan(QUESTION);scope,_=compile_scope(plan,{})
        doc,_=self.queue.enqueue(self.owner,QUESTION,'manual-interruption',payload=make_payload(QUESTION,plan,{},signature(plan),scope))
        _,doc=self.queue.claim('old');doc=self.queue.save(self.owner,doc,'old',status='interrupted')
        base='/api/research/jobs/'+doc['id']+'/resume'
        self.assertEqual((await self.client.post(base)).status_code,422)
        self.assertEqual((await self.client.post(base,json={'revision':doc['revision']-1})).status_code,409)
        self.assertEqual((await self.client.post(base,json={'revision':str(doc['revision'])})).status_code,422)
        response=await self.client.post(base,json={'revision':doc['revision']});self.assertEqual(response.status_code,202,response.text)
        self.assertEqual((await self.client.post(base,json={'revision':doc['revision']})).status_code,409)
        await self.client.get('/api/research/jobs/'+doc['id']+'/events')
        saved=(await self.client.get('/api/research/jobs/'+doc['id'])).json();self.assertEqual(saved['status'],'complete')

    async def test_sources_changed_disabled_tools_and_untrusted_payload_are_blocked(self):
        with patch.dict(os.environ,{'ENABLE_RESEARCH_PLAN_JOBS':'0'}):
            self.assertEqual((await self.client.post('/api/research/jobs/plan',json={'question':QUESTION,'request_key':'test-disable'})).status_code,404)
        for extra in ({'scope':{}},{'path':'/tmp/source'},{'plan':{}},{'source_ids':[]}):
            response=await self.client.post('/api/research/jobs/plan',json={'question':QUESTION,'request_key':'test-invalid',**extra})
            self.assertEqual(response.status_code,422)
        identifier=await self.submit();await self.client.get('/api/research/jobs/'+identifier+'/events')
        path=self.f.pharma/'manifest.json';path.write_text(path.read_text()+' ')
        self.assertEqual((await self.client.get('/api/research/jobs/'+identifier)).status_code,409)
        self.assertEqual((await self.client.get('/api/research/runs/'+identifier+'/evidence/obs-1/download')).status_code,409)
