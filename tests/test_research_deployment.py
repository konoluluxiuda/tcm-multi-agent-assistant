"""Research deployment readiness and opt-in tool restriction fail before providers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_mineru_retrieval import build
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture
from tcm.mineru_research import SnapshotChanged


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import app
        self.app = app
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); fixture(root / 'view'); build(root / 'view', root / 'index')
        from agents.conversation_store import ConversationStore
        memory_patch = patch.object(app.agent_runtime, 'memory', ConversationStore(root / 'chat.sqlite3'))
        memory_patch.start(); self.addCleanup(memory_patch.stop)
        self.service = Service(root / 'index')
        self.client = TestClient(app.app); self.addCleanup(self.client.close)

    def test_cookie_name_configuration_preserves_default_and_separate_namespace(self):
        from features import session_cookie_name
        from starlette.responses import Response
        with patch.dict('os.environ', {'CHAT_COOKIE_NAME': 'research_chat_session'}):
            self.assertEqual(session_cookie_name(), 'research_chat_session')
        for value in ('bad;name', 'bad\nname', ''):
            with patch.dict('os.environ', {'CHAT_COOKIE_NAME': value}), self.assertRaises(ValueError):
                session_cookie_name()
        with patch.object(self.app, 'SESSION_COOKIE_NAME', 'research_chat_session'):
            response = Response(); self.app.set_session_cookie(response, 'fixture')
            self.assertTrue(response.headers['set-cookie'].startswith('research_chat_session='))

    def test_readiness_requires_enabled_and_validated_research_sources(self):
        with patch.object(self.app.research_runtime, 'get_service', return_value=self.service):
            data = self.client.get('/ready/research')
            self.assertEqual(data.status_code, 200)
            self.assertEqual(data.json()['index_sha256'], self.service.receipt['index_sha256'])
            self.service.available = False
            self.assertEqual(self.client.get('/ready/research').status_code, 503)
        with patch.object(self.app.research_runtime, 'get_service', return_value=None):
            self.assertEqual(self.client.get('/ready/research').json()['reason'], 'research_disabled')

    def test_readiness_errors_do_not_leak_private_paths(self):
        for error in (FileNotFoundError('/private/data.pdf'), ValueError('/private/index'), SnapshotChanged('private')):
            with patch.object(self.app.research_runtime, 'get_service', side_effect=error):
                response = self.client.get('/ready/research')
                self.assertEqual(response.status_code, 503); self.assertNotIn('private', response.text)

    def test_herb_readiness_uses_actual_snapshot_and_never_creates_missing_database(self):
        from tcm.herbs import import_snapshot
        root=Path(self.temp.name);source=root/'source';(source/'etcm_herbs').mkdir(parents=True)
        (source/'etcm_herbs/Example.json').write_text(json.dumps({'code':1,'data':[
            {'id':'base_information','value':[{'key':'Herb Name in Pinyin','value':'ExampleHerb'}]}]}))
        database=root/'herbs.sqlite3';import_snapshot(source,database,'herb-test-v1')
        before=database.read_bytes()
        with patch.dict('os.environ',{'TCM_DB':str(database),'TCM_DATASET_VERSION':'herb-test-v1'}):
            result=self.client.get('/ready/herbs')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json(),{'status':'ready','dataset_version':'herb-test-v1',
                                           'source_record_count':1,'count_unit':'source_record'})
        self.assertEqual(database.read_bytes(),before)
        with patch.dict('os.environ',{'TCM_DB':str(database),'TCM_DATASET_VERSION':'missing'}):
            self.assertEqual(self.client.get('/ready/herbs').status_code,503)
        missing=root/'private-missing.sqlite3'
        with patch.dict('os.environ',{'TCM_DB':str(missing)}):
            response=self.client.get('/ready/herbs')
            self.assertEqual(response.status_code,503);self.assertNotIn(str(root),response.text)
        self.assertFalse(missing.exists())

    def test_restricted_profile_remembers_chat_rejection_without_calling_provider(self):
        with patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY': '1'}), \
             patch.object(self.app, 'process_query', side_effect=AssertionError('provider path reached')):
            response = self.client.post('/chat', json={'query': 'hello'})
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIn('/research', response.json()['detail'])
            self.assertEqual(self.client.get('/conversation').json()['messages'],[
                {'role':'user','content':'hello'},
                {'role':'assistant','content':response.json()['detail']},
            ])
            cookie=self.client.cookies.get(self.app.SESSION_COOKIE_NAME)
            saved=self.app.agent_runtime.memory.load(cookie)
            self.assertEqual(saved.messages[-1].additional_kwargs['status'],'not_executed')
            for route in ('/validate', '/upload', '/transcribe', '/generate-speech'):
                self.assertEqual(self.client.post(route, json={}).status_code, 403, route)
            self.assertEqual(self.app.agent_runtime.memory.load(cookie).revision,saved.revision)
            html = self.client.get('/').text
            self.assertIn('中草药证据研究', html); self.assertNotIn('Computer Vision Agents', html)

    def test_restricted_profile_keeps_deterministic_tools_and_followups(self):
        with patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY': '1'}), \
             patch.object(self.app, 'process_query', return_value={'messages': [type('Message', (), {'content': 'fixture'})()], 'agent_name': 'fixture'}) as run:
            for text in ('/research 甘草的功效', '/research', '/research-clear', '/herb BaiBu', '/herb-clear', '它的药用部位是什么？'):
                self.assertEqual(self.client.post('/chat', json={'query': text}).status_code, 200, text)
            self.assertEqual(run.call_count, 6)

    def test_existing_profile_does_not_block_ordinary_queries(self):
        with patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY': '0'}), \
             patch.object(self.app, 'process_query', return_value={'messages': [type('Message', (), {'content': 'fixture'})()], 'agent_name': 'fixture'}):
            self.assertEqual(self.client.post('/chat', json={'query': 'hello'}).status_code, 200)

    def test_project_writes_in_restricted_profile(self):
        from agents.conversation_store import ConversationStore
        root = Path(self.temp.name)
        memory = ConversationStore(root / 'memory.sqlite3')
        with patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY':'1', 'ENABLE_RESEARCH_TASKS':'1',
                                      'ETCM_DISEASE_DB':'fixture', 'RESEARCH_PROJECT_DB':str(root/'projects.sqlite3')}), \
             patch.object(self.app.agent_runtime, 'memory', memory):
            self.client.cookies.set(self.app.SESSION_COOKIE_NAME, memory.new_cookie())
            result = self.client.post('/api/research/projects', json={'title':'研究', 'herbs':['示例叶'], 'question':'贮藏'})
            self.assertEqual(result.status_code, 201, result.text)
            identifier = result.json()['id']
            self.assertEqual(self.client.delete('/api/research/projects/'+identifier+'?revision=1').status_code, 200)
            self.assertEqual(self.client.post('/api/research/projects-other',json={}).status_code,403)
