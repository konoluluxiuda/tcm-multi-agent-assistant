import asyncio
import os
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from tests import test_disease_catalog as fixtures
from tcm.research_job_api import create_router


class Memory:
    def valid_cookie(self,value):return value in ('alice-cookie','bob-cookie')


class JobApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture=fixtures.CatalogTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        db=fixture.build()[0]
        env=patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','ETCM_DISEASE_DB':str(db),'RESEARCH_JOB_DB':str(db.with_name('jobs.sqlite3'))})
        env.start();self.addCleanup(env.stop)
        app=FastAPI();router=create_router(Memory,lambda:'chat');app.include_router(router)
        self.addAsyncCleanup(router.research_worker.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',cookies={'chat':'alice-cookie'})
        self.addAsyncCleanup(self.client.aclose)

    async def test_async_submit_idempotency_terminal_stream_and_replay(self):
        r=await self.client.post('/api/research/jobs',json={'disease':'Synthetic disease','request_key':'request-123'})
        self.assertEqual(r.status_code,202);identifier=r.json()['run_id']
        duplicate=await self.client.post('/api/research/jobs',json={'disease':'Synthetic disease','request_key':'request-123'})
        self.assertEqual(duplicate.json()['run_id'],identifier);self.assertFalse(duplicate.json()['created'])
        # Finite SSE response includes persisted progress and completion.
        response=await self.client.get('/api/research/jobs/'+identifier+'/events')
        self.assertIn('event: done',response.text)
        ids=[int(x[4:]) for x in response.text.splitlines() if x.startswith('id: ')]
        self.assertEqual(ids,sorted(set(ids)))
        saved=(await self.client.get('/api/research/jobs/'+identifier)).json()
        self.assertEqual(saved['status'],'complete');self.assertEqual(saved['result']['run']['events'][-1]['evidence_ids'],['obs-1'])
        replay=await self.client.get('/api/research/jobs/'+identifier+'/events',headers={'Last-Event-ID':str(ids[-2])})
        self.assertNotIn('id: '+str(ids[-2])+'\n',replay.text);self.assertIn('id: '+str(ids[-1])+'\n',replay.text)
        self.client.cookies.set('chat','bob-cookie')
        for suffix in ('','/events'):
            self.assertEqual((await self.client.get('/api/research/jobs/'+identifier+suffix)).status_code,404)
        self.assertEqual((await self.client.post('/api/research/jobs/'+identifier+'/cancel')).status_code,404)
        self.client.cookies.set('chat','alice-cookie')
        self.assertEqual((await self.client.delete('/api/research/jobs/'+identifier)).status_code,200)

    async def test_authorization_and_key_conflict(self):
        body={'disease':'unknown','request_key':'request-123'}
        self.assertEqual((await self.client.post('/api/research/jobs',json=body,headers={'Origin':'https://other.example'})).status_code,403)
        self.assertEqual((await self.client.post('/api/research/jobs',json={**body,'database':'/tmp'})).status_code,422)
        await self.client.post('/api/research/jobs',json=body)
        self.assertEqual((await self.client.post('/api/research/jobs',json={**body,'disease':'other'})).status_code,409)
        self.client.cookies.clear()
        self.assertEqual((await self.client.get('/api/research/jobs')).status_code,401)

    async def test_report_payload_dispatch_validation_and_owner(self):
        async def report(payload, *, on_state=None):
            await on_state({'last_node':'assemble','tool_calls':2,'events':[]})
            return {'kind':'cross_source','status':'complete','herb':payload['herb']}
        body={'herb':'示例叶','question':'功效','request_key':'report-123'}
        with patch('tcm.cross_source_report.run_report',side_effect=report):
            submitted=await self.client.post('/api/research/jobs/report',json=body)
            self.assertEqual(submitted.status_code,202);identifier=submitted.json()['run_id']
            await self.client.get('/api/research/jobs/'+identifier+'/events')
            saved=(await self.client.get('/api/research/jobs/'+identifier)).json()
            self.assertEqual(saved['result']['herb'],'示例叶')
            self.assertEqual(saved['payload']['question'],'功效')
            self.assertEqual((await self.client.post('/api/research/jobs/report',json={**body,'question':'产地'})).status_code,409)
            self.assertEqual((await self.client.post('/api/research/jobs/report',json={**body,'herb':' '})).status_code,422)
            self.assertEqual((await self.client.post('/api/research/jobs/report',json={**body,'database':'/etc/passwd'})).status_code,422)
            self.client.cookies.set('chat','bob-cookie')
            self.assertEqual((await self.client.get('/api/research/jobs/'+identifier)).status_code,404)

    async def test_pair_job_validates_and_preserves_order(self):
        async def pair(payload, *, on_state=None):
            return {'kind':'pair_report','status':'complete','herbs':payload['herbs']}
        body={'herbs':['a','b'],'question':'功效','request_key':'pair-1234'}
        with patch('tcm.pair_research_report.run_pair_report',side_effect=pair):
            submitted=await self.client.post('/api/research/jobs/compare',json=body)
            self.assertEqual(submitted.status_code,202);identifier=submitted.json()['run_id']
            await self.client.get('/api/research/jobs/'+identifier+'/events')
            saved=(await self.client.get('/api/research/jobs/'+identifier)).json()
            self.assertEqual(saved['result']['herbs'],['a','b'])
            duplicate=await self.client.post('/api/research/jobs/compare',json=body)
            self.assertFalse(duplicate.json()['created'])
            self.assertEqual((await self.client.post('/api/research/jobs/compare',json={**body,'herbs':['b','a']})).status_code,409)
            for names in [['a','a'],['a'],['a','b','c'],['','b'],['x'*81,'b']]:
                self.assertEqual((await self.client.post('/api/research/jobs/compare',json={**body,'herbs':names})).status_code,422)
            self.client.cookies.set('chat','bob-cookie')
            self.assertEqual((await self.client.get('/api/research/jobs/'+identifier)).status_code,404)
