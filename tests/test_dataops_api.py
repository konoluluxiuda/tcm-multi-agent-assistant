from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tcm.dataops.api import create_router


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records, self.sources = make_sources(self.root)
        self.memory = ConversationStore(self.root / 'chat.sqlite3')
        self.env = patch.dict('os.environ', {'ENABLE_DATAOPS': '1', 'DATAOPS_ROOT': str(self.root / 'state')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.source_patch = patch('tcm.dataops.api.configured_sources', self.sources)
        self.source_patch.start(); self.addCleanup(self.source_patch.stop)
        app = FastAPI(); app.include_router(create_router(lambda: self.memory, lambda: 'test_cookie'))
        self.client = TestClient(app); self.addCleanup(self.client.close)

    def test_page_initializes_owner_and_disables_feature(self):
        self.assertEqual(self.client.get('/api/dataops/workspaces').status_code, 401)
        page = self.client.get('/dataops')
        self.assertEqual(page.status_code, 200)
        self.assertIn('HttpOnly', page.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/dataops/workspaces').json(), {'items': []})
        with patch.dict('os.environ', {'ENABLE_DATAOPS': '0'}):
            self.assertEqual(self.client.get('/dataops').status_code, 404)
            self.assertEqual(self.client.post('/api/dataops/query', json={}).status_code, 404)

    def test_complete_api_artifact_replay_and_other_owner(self):
        self.client.get('/dataops')
        body = {'request_key': 'test-request', 'goal': {'title': '测试资料核对'}}
        first = self.client.post('/api/dataops/workspaces', json=body)
        self.assertEqual(first.status_code, 201, first.text)
        doc = first.json(); self.assertEqual(doc['result']['coverage']['selected'], 4)
        replay = self.client.post('/api/dataops/workspaces', json=body)
        self.assertEqual(replay.status_code, 200); self.assertEqual(replay.json()['id'], doc['id'])
        path = '/api/dataops/workspaces/' + doc['id']
        self.assertEqual(self.client.get(path).json()['result'], doc['result'])
        file = self.client.get(path + '/artifact')
        self.assertEqual(file.status_code, 200); self.assertTrue(file.content.startswith(b'PK'))
        self.assertEqual(file.headers['cache-control'], 'no-store')
        other = TestClient(self.client.app); self.addCleanup(other.close); other.get('/dataops')
        self.assertEqual(other.get(path).status_code, 404)
        self.assertEqual(other.get(path + '/artifact').status_code, 404)
        self.assertEqual(other.get('/api/dataops/workspaces').json()['items'], [])

    def test_csrf_contract_query_and_missing_ids(self):
        self.client.get('/dataops')
        self.assertEqual(self.client.post('/api/dataops/query', json={}, headers={'Origin': 'http://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/dataops/query', json={'sql': 'drop table'}).status_code, 422)
        self.assertEqual(self.client.post('/api/dataops/query', json={'limit': 1000}).status_code, 422)
        data = self.client.post('/api/dataops/query', json={'group_by': 'medicinal_part'}).json()
        self.assertEqual(data['total'], 4); self.assertEqual(len(data['records']), 4)
        self.assertNotIn('raw_basic_section', str(data))

    def test_main_app_permits_dataops_in_restricted_profile(self):
        import app
        with patch.object(app.agent_runtime, 'memory', self.memory), patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY': '1'}), TestClient(app.app) as client:
            page = client.get('/dataops'); self.assertEqual(page.status_code, 200)
            self.assertEqual(client.post('/api/dataops/query', json={}).status_code, 200)
            result = client.post('/api/dataops/workspaces', json={'request_key': 'main-app-request', 'goal': {}})
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(client.post('/api/dataops/not-a-tool', json={}).status_code, 403)
            with patch.dict('os.environ', {'SHOW_RESEARCH_EXTENSIONS':'0'}):
                html = client.get('/').text
            self.assertNotIn('href="/dataops"', html)
            with patch.dict('os.environ', {'SHOW_RESEARCH_EXTENSIONS':'1'}):
                self.assertIn('href="/dataops"', client.get('/').text)
            self.assertNotIn('workspace-tab-literature', html)


if __name__ == '__main__': unittest.main()
