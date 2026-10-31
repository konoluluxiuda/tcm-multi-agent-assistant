from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tcm.rag_api import create_router,Runs


class ApiTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.memory=ConversationStore(self.root/'chat.sqlite3');self.calls=[]
        def runner(q,compose):self.calls.append((q,compose));return {'status':'original','message':'合成原文','stats':{'requests':0}}
        app=FastAPI();app.include_router(create_router(lambda:self.memory,lambda:'cookie',runner=runner))
        env=patch.dict('os.environ',{'ENABLE_RAG_WORKBENCH':'1','ENABLE_RAG_GENERATION':'0',
                                   'RAG_RELEASE_PATH':str(self.root/'synthetic-release'),
                                   'RAG_RUN_DB':str(self.root/'answers.sqlite3')})
        env.start();self.addCleanup(env.stop)
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.client.get('/rag')

    def test_same_key_replayed_across_restart_and_owner_does_not_share_result(self):
        body={'question':'比较合成资料','compose':False,'request_key':'question-key-001'}
        first=self.client.post('/api/rag/answers',json=body)
        self.assertEqual(first.status_code,200);self.assertFalse(first.json()['replayed'])
        second=self.client.post('/api/rag/answers',json=body)
        self.assertTrue(second.json()['replayed']);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.client.post('/api/rag/answers',json={**body,'question':'另一个问题'}).status_code,409)
        other=TestClient(self.client.app);self.addCleanup(other.close);other.get('/rag')
        self.assertFalse(other.post('/api/rag/answers',json=body).json()['replayed'])
        self.assertEqual(len(self.calls),2)

    def test_feature_auth_csrf_schema_and_models_disabled(self):
        self.assertFalse(self.client.get('/api/rag/capabilities').json()['generation_enabled'])
        body={'question':'合成资料','request_key':'csrf-test-key'}
        self.assertEqual(self.client.post('/api/rag/answers',json=body,headers={'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/rag/answers',json={**body,'path':'/etc/passwd'}).status_code,422)
        self.assertEqual(self.client.post('/api/rag/answers',json={**body,'question':'  '}).status_code,422)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/rag/answers',json=body).status_code,401)
        with patch.dict('os.environ',{'ENABLE_RAG_WORKBENCH':'0'}):
            self.assertEqual(self.client.get('/rag').status_code,404)

    def test_old_cached_composed_answer_gets_display_note_without_redispatch_or_rewrite(self):
        from tcm.rag_pipeline import assemble, render
        from tests.test_rag_pipeline import result
        bundle = assemble(result())
        cached = {'status': 'composed', 'message': render(bundle), 'bundle': bundle, 'stats': {'requests': 1}}
        body = {'question': '合成资料', 'request_key': 'old-composed-answer', 'compose': True}
        with patch.object(Runs, 'reserve', return_value=cached), patch.object(Runs, 'complete') as complete:
            reply = self.client.post('/api/rag/answers', json=body).json()
        self.assertTrue(reply['replayed']); self.assertEqual(reply['composition_effect'], 'unchanged')
        self.assertEqual(reply['stats']['requests'], 1); self.assertEqual(self.calls, [])
        self.assertNotIn('composition_effect', cached); complete.assert_not_called()

    def test_inflight_or_unknown_state_is_not_implicitly_reexecuted(self):
        store=Runs(self.root/'runs.sqlite3')
        self.assertIsNone(store.reserve('owner','key','signature'))
        with self.assertRaises(Exception):Runs(self.root/'runs.sqlite3').reserve('owner','key','signature')

    def test_failed_dispatch_stays_reserved_and_storage_failure_does_not_call_runner(self):
        app=FastAPI();calls=[]
        def failing(*args):calls.append(1);raise TimeoutError('never expose this')
        app.include_router(create_router(lambda:self.memory,lambda:'cookie',runner=failing))
        with TestClient(app) as client:
            client.get('/rag');body={'question':'比较合成资料','request_key':'failed-rag-key'}
            self.assertEqual(client.post('/api/rag/answers',json=body).status_code,503)
            self.assertEqual(client.post('/api/rag/answers',json=body).status_code,409)
            self.assertEqual(len(calls),1)
            with patch('tcm.rag_api.Runs',side_effect=OSError('private path')):
                self.assertEqual(client.post('/api/rag/answers',json={**body,'request_key':'store-error-key'}).status_code,503)
            self.assertEqual(len(calls),1)

    def test_restricted_main_app_allows_rag_without_changing_chat_mode(self):
        import app
        with patch.object(app.agent_runtime,'memory',self.memory),patch.dict('os.environ',{'RESEARCH_TOOLS_ONLY':'1'}), \
             patch('tcm.rag_retrieval.ReleaseSearch') as search,TestClient(app.app) as client:
            source=search.return_value
            from tests.test_rag_pipeline import result
            source.search.return_value=result()
            client.get('/rag')
            response=client.post('/api/rag/answers',json={'question':'比较合成资料','request_key':'main-rag-key-01'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['stats']['requests'],0)
            self.assertIn('rag',client.get('/api/runtime').json())


if __name__=='__main__':unittest.main()
