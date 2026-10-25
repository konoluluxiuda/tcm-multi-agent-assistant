"""Offline API boundary and execution tests."""
import asyncio
import os
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from fastapi import FastAPI
from tcm.research_run_api import create_router
from tests import test_disease_catalog as fixtures


class Memory:
    def valid_cookie(self, value): return value in ('signed-test-cookie','another-signed-cookie')


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture = fixtures.CatalogTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.db = fixture.build()[0]
        self.env = patch.dict(os.environ, {'ENABLE_RESEARCH_TASKS': '1', 'ETCM_DISEASE_DB': str(self.db), 'RESEARCH_RUN_DB': str(self.db.with_name('runs.sqlite3'))})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI(); app.include_router(create_router(Memory, lambda: 'chat'))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', cookies={'chat': 'signed-test-cookie'})
        self.addAsyncCleanup(self.client.aclose)

    async def post(self, **kwargs):
        return await self.client.post('/api/research/runs/profile', json={'disease': 'Synthetic disease'}, **kwargs)

    async def test_actual_local_profile_and_no_match(self):
        result = await self.post()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'complete')
        self.assertEqual(result.json()['run']['evidence']['obs-1']['data']['target_ids'], ['P12345','Q12345'])
        result = await self.client.post('/api/research/runs/profile', json={'disease': 'unknown'})
        self.assertEqual(result.json()['status'], 'needs_input')

    async def test_origin_cookie_and_disabled(self):
        self.assertEqual((await self.post(headers={'Origin':'https://evil.example'})).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual((await self.post()).status_code, 401)
        with patch.dict(os.environ, {'ENABLE_RESEARCH_TASKS':'0'}):
            self.assertEqual((await self.post()).status_code, 404)

    async def test_body_cannot_choose_database_and_error_is_sanitized(self):
        result = await self.client.post('/api/research/runs/profile', json={'disease':'x','database':'/tmp/x'})
        self.assertEqual(result.status_code, 422)
        with patch('tcm.research_run_api.run_local_profile', new=AsyncMock(side_effect=RuntimeError('secret-path'))):
            result = await self.post()
            self.assertEqual(result.status_code, 503); self.assertNotIn('secret-path', result.text)
        self.assertEqual((await self.post()).status_code, 200)

    async def test_concurrency_and_cancellation_release_slot(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def blocked(*args,**kwargs):
            started.set()
            try: await asyncio.Future()
            finally: stopped.set()
        with patch('tcm.research_run_api.run_local_profile', new=blocked):
            task = asyncio.create_task(self.post()); await started.wait()
            self.assertEqual((await self.post()).status_code, 429)
            history=await self.client.get('/api/research/runs')
            self.assertEqual(history.headers['cache-control'],'no-store')
            identifier=history.json()['items'][0]['id']
            self.assertEqual((await self.client.delete('/api/research/runs/'+identifier)).status_code,409)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertTrue(stopped.is_set())
            saved=(await self.client.get('/api/research/runs/'+identifier)).json()
            self.assertEqual(saved['status'],'cancelled')
        self.assertEqual((await self.post()).status_code, 200)

    async def test_history_reopen_isolation_and_delete(self):
        r=(await self.post()).json();identifier=r['run_id']
        rows=(await self.client.get('/api/research/runs')).json()['items']
        self.assertEqual(rows[0]['id'],identifier)
        self.assertNotIn('snapshot',rows[0])
        saved=(await self.client.get('/api/research/runs/'+identifier)).json()
        self.assertEqual(saved['result']['status'],'complete')
        self.client.cookies.set('chat','another-signed-cookie')
        self.assertEqual((await self.client.get('/api/research/runs')).json()['items'],[])
        self.assertEqual((await self.client.get('/api/research/runs/'+identifier)).status_code,404)
        self.assertEqual((await self.client.delete('/api/research/runs/'+identifier)).status_code,404)
        self.client.cookies.set('chat','signed-test-cookie')
        self.assertEqual((await self.client.delete('/api/research/runs/'+identifier,headers={'Origin':'https://evil.example'})).status_code,403)
        self.assertEqual((await self.client.delete('/api/research/runs/'+identifier)).status_code,200)
        self.assertEqual((await self.client.get('/api/research/runs/'+identifier)).status_code,404)

    async def test_script_available(self):
        r = await self.client.get('/research-runs.js')
        self.assertEqual(r.status_code, 200); self.assertIn('EventSource', r.text)
