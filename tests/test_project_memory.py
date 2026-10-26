import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from tcm.project_memory import ProjectStore, ProjectConflict
from tcm.project_memory_api import create_router

DATA = {'title':'双药研究','herbs':['当归','川芎'],'question':'功效及贮藏'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'projects.sqlite3'; self.store = ProjectStore(self.path)

    def test_reopen_and_owner_isolation(self):
        saved = self.store.save('alice', DATA)
        self.assertEqual(ProjectStore(self.path).get('alice',saved['id']),saved)
        self.assertEqual(self.store.list('bob'),[])
        with self.assertRaises(KeyError): self.store.get('bob',saved['id'])
        with self.assertRaises(KeyError): self.store.delete('bob',saved['id'],1)

    def test_conflict_delete_no_resurrection(self):
        d=self.store.save('a',DATA)
        self.store.save('a',{**DATA,'question':'贮藏'},identifier=d['id'],revision=1)
        with self.assertRaises(ProjectConflict): self.store.save('a',DATA,identifier=d['id'],revision=1)
        with self.assertRaises(ProjectConflict): self.store.delete('a',d['id'],1)
        self.store.delete('a',d['id'],2)
        self.assertEqual(self.store.list('a'),[])
        with self.assertRaises(KeyError): self.store.save('a',DATA,identifier=d['id'],revision=2)

    def test_concurrent_update_has_one_winner(self):
        d=self.store.save('a',DATA)
        def update(i):
            try: self.store.save('a',{**DATA,'title':str(i)},identifier=d['id'],revision=1); return True
            except ProjectConflict: return False
        with ThreadPoolExecutor(max_workers=4) as pool: self.assertEqual(sum(pool.map(update,range(4))),1)
        self.assertEqual(self.store.get('a',d['id'])['revision'],2)

    def test_limit_and_validation(self):
        for _ in range(20): self.store.save('a',DATA)
        with self.assertRaises(ProjectConflict): self.store.save('a',DATA)
        for changes in [{'herbs':['a','a']},{'title':' '},{'question':' '},{'herbs':[]},{'herbs':['a'*81]},{'extra':'value'}]:
            with self.assertRaises(ValueError): self.store.save('b',{**DATA,**changes})
        self.store.save('b',DATA)

    def test_wrong_database_rejected(self):
        with sqlite3.connect(self.path) as db: db.execute('CREATE TABLE unrelated(x)')
        with self.assertRaises(sqlite3.DatabaseError): self.store.list('a')


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        class Memory:
            path=Path(temp.name)/'chat.sqlite3'
            def valid_cookie(self,cookie): return cookie in ('alice','bob')
        env=patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','ETCM_DISEASE_DB':'fixture','RESEARCH_PROJECT_DB':str(Path(temp.name)/'projects.sqlite3')})
        env.start();self.addCleanup(env.stop)
        app=FastAPI();app.include_router(create_router(Memory,lambda:'chat'))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test',cookies={'chat':'alice'})
        self.addAsyncCleanup(self.client.aclose)

    async def test_crud_and_conflict(self):
        c=self.client;r=await c.post('/api/research/projects',json=DATA);self.assertEqual(r.status_code,201)
        path='/api/research/projects/'+r.json()['id']
        self.assertEqual(r.headers['cache-control'],'no-store')
        self.assertEqual((await c.put(path,json={**DATA,'question':'贮藏','revision':1})).json()['revision'],2)
        self.assertEqual((await c.put(path,json={**DATA,'revision':1})).status_code,409)
        self.assertEqual((await c.delete(path+'?revision=1')).status_code,409)
        self.assertEqual((await c.delete(path+'?revision=2')).status_code,200)
        self.assertEqual((await c.get(path)).status_code,404)
        self.assertEqual((await c.get('/api/research/projects')).json(),{'items':[]})

    async def test_auth_origin_and_owner(self):
        c=self.client;path='/api/research/projects'
        d=(await c.post(path,json=DATA)).json();url=path+'/'+d['id']
        c.cookies.set('chat','bob')
        self.assertEqual((await c.get(url)).status_code,404)
        self.assertEqual((await c.put(url,json={**DATA,'revision':1})).status_code,404)
        self.assertEqual((await c.delete(url+'?revision=1')).status_code,404)
        self.assertEqual((await c.post(path,json=DATA,headers={'Origin':'https://evil.example'})).status_code,403)
        c.cookies.clear();self.assertEqual((await c.get(path)).status_code,401)

    async def test_validation_and_disabled(self):
        for changes in [{'question':' '},{'herbs':['a','a']},{'owner':'bob'}]:
            self.assertEqual((await self.client.post('/api/research/projects',json={**DATA,**changes})).status_code,422)
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'0'}):
            self.assertEqual((await self.client.get('/api/research/projects')).status_code,404)
