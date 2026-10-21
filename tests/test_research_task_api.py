"""Task API authorization, conflict handling, recovery and synthetic-only behavior."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tcm.research_task_api import create_router
from tcm.research_task_store import ResearchTaskStore, TaskConflict
from tcm.research_task_fixtures import NAMES


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.path=root/'tasks.sqlite3'
        self.env=patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'1','RESEARCH_TASK_DB':str(self.path)})
        self.env.start();self.addCleanup(self.env.stop)
        self.memory=ConversationStore(root/'chat.sqlite3')
        self.app=FastAPI();self.app.include_router(create_router(lambda:self.memory,lambda:'test_chat'))
        self.client=TestClient(self.app);self.other=TestClient(self.app)
        self.addCleanup(self.client.close);self.addCleanup(self.other.close)

    def create(self,profile='retry'):
        r=self.client.post('/api/research/tasks',json={'profile':profile});self.assertEqual(r.status_code,200,r.text);return r.json()

    def choose(self,t,selection):
        return self.client.post('/api/research/tasks/'+t['id']+'/choose',json={'revision':t['revision'],'selection':selection})

    def targets(self,t):
        r=self.choose(t,{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'});self.assertEqual(r.status_code,200,r.text);return r.json()

    def finish(self,profile='retry'):
        t=self.targets(self.create(profile));r=self.choose(t,['SYN-T1']);self.assertEqual(r.status_code,200,r.text);return r.json()

    def test_two_pauses_refresh_and_reopened_server_resume_without_model(self):
        with patch('tcm.research_task.NativeToolModel.__init__',side_effect=AssertionError('No native calls')):
            t=self.create();self.assertEqual(t['status'],'waiting_sources')
            self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).json()['revision'],t['revision'])
            self.memory=ConversationStore(self.memory.path)
            t=self.targets(t);self.assertEqual(t['status'],'waiting_targets')
            t=self.choose(t,['SYN-T1']).json();self.assertEqual(t['status'],'complete')
            self.assertEqual(t['external_model_calls'],0);self.assertEqual(t['tool_calls'],7)
            self.assertEqual([e['args']['variant'] for e in t['actions'] if e.get('name')=='search_literature'],['initial','expanded'])
            self.assertEqual(self.client.get('/api/research/tasks').json()['items'][0]['id'],t['id'])

    def test_other_browser_cannot_read_choose_resume_delete_or_export(self):
        t=self.create();base='/api/research/tasks/'+t['id']
        self.assertEqual(self.other.get('/api/research/tasks').json()['items'],[])
        requests=[('get',base,None),('post',base+'/choose',{'revision':t['revision'],'selection':{}}),
                  ('post',base+'/resume',{'revision':t['revision']}),('delete',base+'?revision='+str(t['revision']),None),('get',base+'/report',None)]
        for method,url,body in requests:
            kwargs={'json':body} if body is not None else {}
            self.assertEqual(getattr(self.other,method)(url,**kwargs).status_code,404)
        self.assertEqual(ResearchTaskStore(self.path).get(t['id'])['revision'],t['revision'])

    def test_cookie_is_signed_private_and_not_exposed_in_state(self):
        t=self.create();cookie=self.client.cookies.get('test_chat_tasks')
        self.assertTrue(self.memory.valid_cookie(cookie))
        raw=ResearchTaskStore(self.path).get(t['id'])
        self.assertEqual(raw['owner'],hashlib.sha256(cookie.encode()).hexdigest())
        self.assertNotIn('owner',t);self.assertNotIn(cookie,str(t))
        self.client.cookies.clear();self.client.cookies.set('test_chat_tasks','forged')
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).status_code,404)
        self.assertEqual(self.client.get('/api/research/tasks').json()['items'],[])

    def test_chat_cookie_rotation_does_not_delete_tasks(self):
        t=self.create();self.client.cookies.set('test_chat',self.memory.new_cookie())
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).status_code,200)

    def test_stale_choices_and_delete_conflict_do_not_overwrite(self):
        t=self.create();updated=self.targets(t)
        self.assertEqual(self.choose(t,{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'}).status_code,409)
        self.assertEqual(self.client.delete('/api/research/tasks/'+t['id']+'?revision='+str(t['revision'])).status_code,409)
        self.assertEqual(self.client.delete('/api/research/tasks/'+t['id']+'?revision='+str(updated['revision'])).status_code,200)
        self.assertEqual(self.client.get('/api/research/tasks/'+t['id']).status_code,404)
        with self.assertRaises(TaskConflict):ResearchTaskStore(self.path).save(ResearchTaskStore(self.path).create({'owner':None}) | {'id':t['id']})

    def test_foreign_ids_extra_fields_and_unbounded_profiles_are_rejected(self):
        self.assertEqual(self.client.post('/api/research/tasks',json={'profile':'native'}).status_code,422)
        self.assertEqual(self.client.post('/api/research/tasks',json={'owner':'forged'}).status_code,422)
        t=self.create();self.assertEqual(self.choose(t,{NAMES[0]:'FOREIGN',NAMES[1]:'SYN-B1'}).status_code,422)
        self.assertEqual(ResearchTaskStore(self.path).get(t['id'])['revision'],t['revision'])
        t=self.targets(t)
        for selection in [['FOREIGN'],['SYN-T1','SYN-T1'],[],['SYN-T1','SYN-T2','SYN-T3']]:
            self.assertEqual(self.choose(t,selection).status_code,422)

    def test_partial_report_has_explicit_failures_and_download(self):
        for profile in ('ingredient_only','withdrawn','wrong_object','unavailable'):
            t=self.finish(profile);self.assertEqual(t['status'],'partial');self.assertTrue(t['report']['gaps'])
            r=self.client.get('/api/research/tasks/'+t['id']+'/report')
            self.assertEqual(r.status_code,200);self.assertIn('合成',r.text)
            self.assertIn('attachment',r.headers['content-disposition'])
        t=self.create();self.assertEqual(self.client.get('/api/research/tasks/'+t['id']+'/report').status_code,409)

    def test_get_is_read_only_and_interrupted_retry_retains_budgets(self):
        t=self.create();store=ResearchTaskStore(self.path);raw=store.get(t['id'])
        raw.update(status='running',pending=None,events=[],tool_calls=0,inflight={'kind':'model'},model_calls=2);store.save(raw)
        r=self.client.get('/api/research/tasks/'+t['id']);self.assertTrue(r.json()['recovery_required'])
        self.assertEqual(store.get(t['id'])['revision'],raw['revision'])
        r=self.client.post('/api/research/tasks/'+t['id']+'/resume',json={'revision':raw['revision'],'retry_interrupted':True})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'waiting_sources')
        self.assertEqual(store.get(t['id'])['model_calls'],3)

    def test_origin_rejection_and_feature_off_without_creating_database(self):
        self.assertEqual(self.client.post('/api/research/tasks',json={},headers={'origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/research/tasks',json={},headers={'sec-fetch-site':'cross-site'}).status_code,403)
        self.assertFalse(self.path.exists())
        with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'0'}):
            for url in ('/research-tasks','/research-tasks.js','/api/research/tasks'):
                self.assertEqual(self.client.get(url).status_code,404)
        self.assertFalse(self.path.exists())

    def test_per_owner_limit_is_atomic_and_other_owner_unaffected(self):
        for _ in range(20):self.create()
        self.assertEqual(self.client.post('/api/research/tasks',json={}).status_code,409)
        self.assertEqual(len(self.client.get('/api/research/tasks').json()['items']),20)
        self.assertEqual(self.other.post('/api/research/tasks',json={}).status_code,200)

    def test_page_assets_and_no_common_target_report(self):
        for url in ('/research-tasks','/research-tasks.js'):
            r=self.client.get(url);self.assertEqual(r.status_code,200);self.assertIn('no-store',r.headers['cache-control'])
        t=self.targets(self.create('no_common'));self.assertEqual(t['status'],'partial')
        self.assertTrue(t['report']['gaps'])

    def test_main_tools_only_middleware_allows_tasks_but_keeps_other_writes_blocked(self):
        import app
        with patch('agents.agent_decision.memory',self.memory), patch.dict(os.environ,{'RESEARCH_TOOLS_ONLY':'1'}), TestClient(app.app) as client:
            r=client.post('/api/research/tasks',json={})
            self.assertEqual(r.status_code,200,r.text)
            task=r.json()
            r=client.post('/api/research/tasks/'+task['id']+'/choose',json={
                'revision':task['revision'],'selection':{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'}})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(client.post('/generate-speech',json={}).status_code,403)
            self.assertEqual(client.post('/api/research/tasks-other',json={}).status_code,403)
            with patch.dict(os.environ,{'ENABLE_RESEARCH_TASKS':'0'}):
                self.assertEqual(client.post('/api/research/tasks',json={}).status_code,404)


if __name__=='__main__':unittest.main()
