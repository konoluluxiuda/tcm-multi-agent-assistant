import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tcm.research_loop_api import create_router
from tcm.research_run_durable import RunJournal
from tcm.project_semantic import owner_key
from tcm.runtime_backup import create_bundle,restore_bundle
from tcm.project_memory import ProjectStore
from tcm.research_jobs import JobStore


class LoopApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.memory=ConversationStore(self.root/'memory.sqlite3');self.cookie=self.memory.new_cookie();self.other=self.memory.new_cookie()
        self.path=self.root/'loop.sqlite3'
        env=patch.dict('os.environ',{'ENABLE_SYNTHETIC_LOOP':'1','RESEARCH_LOOP_DB':str(self.path)})
        env.start();self.addCleanup(env.stop)
        self.app=FastAPI();self.app.include_router(create_router(lambda:self.memory,lambda:'session'))
        self.client=TestClient(self.app);self.addCleanup(self.client.close);self.client.cookies.set('session',self.cookie)

    def create(self):
        r=self.client.post('/api/research/loops',json={'pause_after_observation':True})
        self.assertEqual(r.status_code,201,r.text);return r.json()

    def test_pause_refresh_resume_terminal_and_delete(self):
        doc=self.create();url='/api/research/loops/'+doc['id'];self.assertEqual(doc['phase'],'ready')
        self.assertEqual((doc['model_calls'],doc['tool_calls']),(1,1))
        self.assertNotIn('messages',doc);self.assertNotIn('evidence',doc)
        self.assertEqual(self.client.get(url).json(),doc)
        r=self.client.post(url+'/resume',json={'revision':doc['revision']});self.assertEqual(r.status_code,200,r.text)
        final=r.json();self.assertEqual((final['status'],final['model_calls'],final['tool_calls']),('complete',4,3))
        self.assertEqual(self.client.request('DELETE',url,json={'revision':doc['revision']}).status_code,409)
        self.assertEqual(self.client.request('DELETE',url,json={'revision':final['revision']}).status_code,200)
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(url+'/resume',json={'revision':final['revision']}).status_code,404)

    def test_auth_scope_origin_and_input_contract(self):
        doc=self.create();url='/api/research/loops/'+doc['id']
        self.client.cookies.set('session',self.other)
        self.assertEqual(self.client.get('/api/research/loops').json()['items'],[])
        for method,path in [('GET',url),('POST',url+'/resume'),('DELETE',url)]:
            self.assertEqual(self.client.request(method,path,**({'json':{'revision':doc['revision']}} if method!='GET' else {})).status_code,404)
        self.client.cookies.clear();self.assertEqual(self.client.get(url).status_code,401)
        self.client.cookies.set('session',self.cookie)
        self.assertEqual(self.client.post(url+'/resume',json={'revision':doc['revision']},headers={'Origin':'https://other.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/research/loops',json={'owner':'forged'}).status_code,422)
        self.assertEqual(self.client.post(url+'/resume',json={'revision':0}).status_code,422)
        with patch.dict('os.environ',{'ENABLE_SYNTHETIC_LOOP':'0'}):self.assertEqual(self.client.get(url).status_code,404)

    def test_locked_run_cannot_delete_and_stale_resume_rejected(self):
        doc=self.create();url='/api/research/loops/'+doc['id']
        with RunJournal(self.path,owner_key(self.cookie),doc['id'],'test').locked():
            self.assertEqual(self.client.request('DELETE',url,json={'revision':doc['revision']}).status_code,409)
            self.assertEqual(self.client.post(url+'/resume',json={'revision':doc['revision']}).status_code,409)
        self.assertEqual(self.client.post(url+'/resume',json={'revision':doc['revision']+1}).status_code,409)
        self.assertEqual(self.client.get(url).json(),doc)

    def test_bundle_restores_cookie_and_paused_loop_then_resume(self):
        doc=self.create();owner=owner_key(self.cookie)
        projects=ProjectStore(self.root/'projects.sqlite3');projects.save(owner,{'title':'fixture','herbs':['a'],'question':'q'})
        JobStore(self.root/'jobs.sqlite3')
        files={'memory':self.memory.path,'projects':projects.path,'jobs':self.root/'jobs.sqlite3','loops':self.path}
        manifest=create_bundle(files,self.root/'bundle',workers_stopped=True,cookie_name='session')
        self.assertIn('loops.sqlite3',manifest['files'])
        restore_bundle(self.root/'bundle',self.root/'restored')
        self.memory=ConversationStore(self.root/'restored/memory.sqlite3')
        with patch.dict('os.environ',{'RESEARCH_LOOP_DB':str(self.root/'restored/loops.sqlite3')}):
            url='/api/research/loops/'+doc['id'];self.assertEqual(self.client.get(url).json(),doc)
            final=self.client.post(url+'/resume',json={'revision':doc['revision']})
            self.assertEqual(final.status_code,200,final.text);self.assertEqual(final.json()['status'],'complete')
        self.memory=ConversationStore(self.root/'memory.sqlite3')
        self.assertEqual(self.client.get(url).json(),doc)

    def test_uncertain_dispatch_resume_never_calls_model(self):
        import sqlite3,json
        doc=self.create()
        with sqlite3.connect(self.path) as db:
            raw=db.execute('SELECT document FROM runs WHERE id=?',(doc['id'],)).fetchone()[0]
            saved=json.loads(raw);saved.update(phase='pending',operation='model')
            db.execute('UPDATE runs SET document=? WHERE id=?',(json.dumps(saved),doc['id']))
        with patch('tcm.research_loop_api.OfflineModel.ainvoke',side_effect=AssertionError('must not dispatch')):
            response=self.client.post('/api/research/loops/'+doc['id']+'/resume',json={'revision':doc['revision']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['reason'],'uncertain_model_dispatch')
        self.assertEqual(response.json()['phase'],'terminal')
