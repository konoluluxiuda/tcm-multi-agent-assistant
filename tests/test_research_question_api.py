from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tests.test_research_question import draft
from langchain_core.messages import AIMessage
from tcm.dataops.api import create_router


class QuestionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        _,sources=make_sources(self.root);self.memory=ConversationStore(self.root/'memory.sqlite3')
        for p in (patch.dict('os.environ',{'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'synthetic',
                    'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'0','DATAOPS_ROOT':str(self.root/'work'),
                    'RESEARCH_JOB_DB':str(self.root/'jobs.sqlite3'),'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':''}),
                  patch('tcm.dataops.jobs.research_sources',sources)):
            p.start();self.addCleanup(p.stop)
        app=FastAPI();app.include_router(create_router(lambda:self.memory,lambda:'cookie'))
        self.client=TestClient(app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.client.get('/dataops/research')
    def wait(self,id):
        for _ in range(200):
            r=self.client.get('/api/dataops/agent-runs/'+id).json()
            if r['job']['status'] not in ('queued','running'):return r['workspace']
            time.sleep(.02)
        self.fail('Task did not finish')
    def body(self,**kw):return {'question':'整理Alpha和Beta的功效和注意，生成表格','request_key':'question-api-one',**kw}

    def test_question_to_queue_export_revision_and_idempotent_replay(self):
        r=self.client.post('/api/dataops/research-questions',json=self.body());self.assertEqual(r.status_code,202,r.text)
        id=r.json()['workspace']['id'];parent=self.wait(id)
        self.assertEqual(parent['status'],'complete');self.assertEqual(len(parent['result']['rows']),2)
        self.assertEqual(parent['goal_understanding']['provider_requests'],0)
        repeated=self.client.post('/api/dataops/research-questions',json=self.body()).json()
        self.assertTrue(repeated['replayed']);self.assertEqual(repeated['workspace']['id'],id)
        child=self.client.post('/api/dataops/research-questions',json=self.body(question='增加贮藏，保留旧字段',
            request_key='question-api-v2',parent_id=id,expected_revision=1))
        self.assertEqual(child.status_code,202,child.text);d=self.wait(child.json()['workspace']['id'])
        self.assertEqual(d['goal']['document_fields'],['功能与主治','注意','贮藏'])
        self.assertEqual(self.client.get('/api/dataops/workspaces/'+id+'/artifact').status_code,200)

    def test_ambiguity_scope_and_feature_controls_do_not_create_task(self):
        for question in ('Alpha的来源做成表','整理它们的功效','Alpha的功效并预测疗效'):
            r=self.client.post('/api/dataops/research-questions',json=self.body(question=question,request_key='question-'+str(len(question))))
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['proposal']['status'],'needs_input')
            self.assertNotIn('workspace',r.json())
        r=self.client.post('/api/dataops/research-questions',json=self.body(use_model=True));self.assertEqual(r.status_code,409)
        self.assertEqual(self.client.post('/api/dataops/research-questions',json=self.body(),headers={'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/dataops/research-questions',json=self.body(sql='DELETE FROM records')).status_code,422)

    def test_fixed_executor_uses_same_queue_and_preserves_missing_named_records(self):
        with patch.dict('os.environ',{'DATAOPS_RESEARCH_DRIVER':'fixed'}):
            r=self.client.post('/api/dataops/research-questions',json=self.body())
            self.assertEqual(r.status_code,202,r.text)
            d=self.wait(r.json()['workspace']['id'])
            self.assertEqual(d['status'],'complete');self.assertEqual(len(d['result']['rows']),2)
            self.assertEqual(d['progress']['driver'],'research_fixed_field_policy')
            self.assertEqual(d['result']['research']['provider_requests'],0)

    def test_one_model_proposal_is_persisted_and_not_resent_after_replay(self):
        class Model:
            max_retries=0;max_tokens=1200;model_name='fixture';calls=0
            http_client=SimpleNamespace(close=lambda:None)
            def bind_tools(self,*a,**kw):return self
            def invoke(self,m):
                self.calls+=1;return AIMessage(content='',tool_calls=[{'id':'x','name':'propose_research_goal','args':draft()}])
        m=Model()
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','RAG_MODEL':'fixture','RAG_BASE_URL':'https://api.deepseek.com','RAG_API_KEY':'test-only'}),patch('tcm.dataops.research_model.configured_model',return_value=m):
            first=self.client.post('/api/dataops/research-questions',json=self.body(use_model=True));self.assertEqual(first.status_code,202,first.text)
            self.wait(first.json()['workspace']['id'])
            second=self.client.post('/api/dataops/research-questions',json=self.body(use_model=True));self.assertEqual(second.status_code,202,second.text)
            self.assertEqual(m.calls,1);self.assertTrue(second.json()['replayed'])
            self.assertEqual(second.json()['workspace']['goal_understanding']['provider_requests'],1)

    def test_unknown_model_outcome_is_not_resent(self):
        class Broken:
            max_retries=0;max_tokens=1200;calls=0
            http_client=SimpleNamespace(close=lambda:None)
            def bind_tools(self,*a,**kw):return self
            def invoke(self,m):self.calls+=1;raise TimeoutError('unknown')
        m=Broken()
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','RAG_MODEL':'fixture','RAG_BASE_URL':'https://api.deepseek.com','RAG_API_KEY':'test-only'}),patch('tcm.dataops.research_model.configured_model',return_value=m):
            self.assertEqual(self.client.post('/api/dataops/research-questions',json=self.body(use_model=True)).status_code,503)
            self.assertEqual(self.client.post('/api/dataops/research-questions',json=self.body(use_model=True)).status_code,409)
            self.assertEqual(m.calls,1)

    def test_wrong_explicit_model_goal_never_creates_workspace_and_is_not_retried(self):
        class Wrong:
            max_retries=0;max_tokens=1200;calls=0;model_name='fixture'
            http_client=SimpleNamespace(close=lambda:None)
            def bind_tools(self,*a,**kw):return self
            def invoke(self,m):
                self.calls+=1
                return AIMessage(content='',tool_calls=[{'id':'wrong','name':'propose_research_goal',
                    'args':draft(document_fields=['功能与主治'])}])
        m=Wrong()
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','RAG_MODEL':'fixture','RAG_BASE_URL':'https://api.deepseek.com','RAG_API_KEY':'test-only'}),patch('tcm.dataops.research_model.configured_model',return_value=m):
            body=self.body(use_model=True,question='整理Alpha和Beta的功效和贮藏，生成表格')
            for _ in range(2):
                r=self.client.post('/api/dataops/research-questions',json=body)
                self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['proposal']['status'],'needs_input')
                self.assertNotIn('workspace',r.json())
            self.assertEqual(m.calls,1);self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'],[])

    def test_owner_cannot_reference_another_workspace_and_restricted_main_app_accepts_route(self):
        r=self.client.post('/api/dataops/research-questions',json=self.body());id=r.json()['workspace']['id'];self.wait(id)
        other=TestClient(self.client.app);self.addCleanup(other.close);other.get('/dataops/research')
        self.assertEqual(other.post('/api/dataops/research-questions',json=self.body(parent_id=id,expected_revision=1)).status_code,404)
        import app
        with patch.object(app.agent_runtime,'memory',self.memory),patch.dict('os.environ',{'RESEARCH_TOOLS_ONLY':'1'}),TestClient(app.app) as c:
            c.get('/dataops/research');r=c.post('/api/dataops/research-questions',json=self.body(request_key='main-question-api'))
            self.assertEqual(r.status_code,202,r.text)

    def test_chat_shares_question_service_and_parent_without_enabling_legacy_agent(self):
        import app
        with patch.object(app.agent_runtime,'memory',self.memory),patch.dict('os.environ',{'RESEARCH_TOOLS_ONLY':'1','ENABLE_DATAOPS_AGENT':'0'}),TestClient(app.app) as c:
            c.get('/conversation')
            r=c.post('/chat',json={'query':'整理Alpha和Beta的功效和注意，生成表格','request_key':'question-chat-one'})
            self.assertEqual(r.status_code,200,r.text);first=r.json()['assistant']['workspace_id']
            for _ in range(200):
                value=c.get('/api/dataops/agent-runs/'+first).json()
                if value['job']['status'] not in ('queued','running'):break
                time.sleep(.02)
            self.assertEqual(value['workspace']['goal']['research']['mode'],'audit_named')
            self.assertEqual(len(value['workspace']['result']['rows']),2)
            r=c.post('/chat',json={'query':'增加贮藏，保留旧字段','request_key':'question-chat-two'})
            self.assertEqual(r.status_code,200,r.text)
            child=r.json()['assistant']['workspace_id']
            self.assertEqual(c.get('/api/dataops/workspaces/'+child).json()['parent_id'],first)
