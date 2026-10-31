from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tcm.dataops.api import create_router


class ResearchApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        _,sources=make_sources(self.root);self.memory=ConversationStore(self.root/'chat.sqlite3')
        for p in (patch.dict('os.environ',{'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_AGENT':'0',
                    'ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'synthetic',
                    'DATAOPS_ROOT':str(self.root/'work'),'RESEARCH_JOB_DB':str(self.root/'jobs.sqlite3'),
                    'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':''}),
                  patch('tcm.dataops.jobs.research_sources',sources)):
            p.start();self.addCleanup(p.stop)
        app=FastAPI();app.include_router(create_router(lambda:self.memory,lambda:'cookie'))
        self.client=TestClient(app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.assertEqual(self.client.get('/dataops/research').status_code,200)
    def body(self,**kw):
        return {'request_key':'research-api-request','question':'核对一条功效齐全的候选',
                'goal':{'query':{'names':['Alpha','Beta'],'limit':20},'attributes':['medicinal_part'],
                        'document_fields':['功能与主治'],'research':{'target_count':1}},**kw}
    def wait(self,id):
        for _ in range(200):
            value=self.client.get('/api/dataops/agent-runs/'+id).json()
            if value['job']['status'] not in ('queued','running'):return value
            time.sleep(.02)
        self.fail('task did not finish')
    def test_queue_owner_replay_and_export_without_legacy_agent_gate(self):
        created=self.client.post('/api/dataops/research-tasks',json=self.body())
        self.assertEqual(created.status_code,202,created.text);id=created.json()['workspace']['id'];r=self.wait(id)
        self.assertEqual(r['job']['status'],'complete',r)
        self.assertEqual(r['workspace']['result']['research']['provider_requests'],0)
        self.assertEqual(self.client.post('/api/dataops/research-tasks',json=self.body()).json()['workspace']['id'],id)
        artifact=self.client.get('/api/dataops/workspaces/'+id+'/artifact');self.assertEqual(artifact.status_code,200)
        self.assertTrue(artifact.content.startswith(b'PK'))
        other=TestClient(self.client.app);self.addCleanup(other.close)
        other.get('/dataops/research');self.assertEqual(other.get('/api/dataops/agent-runs/'+id).status_code,404)
        self.assertEqual(other.get('/api/dataops/workspaces/'+id+'/artifact').status_code,404)
    def test_off_mode_csrf_and_invalid_goal_fail_before_enqueue(self):
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESEARCH':'0'}):
            self.assertEqual(self.client.post('/api/dataops/research-tasks',json=self.body()).status_code,409)
        self.assertEqual(self.client.post('/api/dataops/research-tasks',json=self.body(),headers={'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/dataops/research-tasks',json=self.body(goal={'sql':'SELECT *'})).status_code,422)
    def test_main_app_restricted_mode_allows_only_valid_task_route(self):
        import app
        with patch.object(app.agent_runtime,'memory',self.memory),patch.dict('os.environ',{'RESEARCH_TOOLS_ONLY':'1'}),TestClient(app.app) as c:
            c.get('/dataops/research');r=c.post('/api/dataops/research-tasks',json=self.body(request_key='research-main-app'))
            self.assertEqual(r.status_code,202,r.text)
