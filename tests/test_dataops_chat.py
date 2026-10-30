"""Chat and workbench share one queue, owner boundary and immutable deliveries."""
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tcm.dataops.chat import matches


class DataOpsChatTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); _, factory = make_sources(self.root / 'fixture')
        self.memory = ConversationStore(self.root / 'memory.sqlite3')
        environment = patch.dict('os.environ', {'ENABLE_DATAOPS': '1', 'ENABLE_DATAOPS_AGENT': '1',
            'DATAOPS_DRIVER': 'synthetic', 'DATAOPS_ROOT': str(self.root / 'work'),
            'RESEARCH_JOB_DB': str(self.root / 'jobs.sqlite3'), 'RESEARCH_JOB_DSN': '', 'RESEARCH_CHECKPOINT_DSN': '',
            'RESEARCH_TOOLS_ONLY': '1'})
        environment.start(); self.addCleanup(environment.stop)
        import app
        self.app = app
        for context in (patch.object(app.agent_runtime, 'memory', self.memory),
                        patch('tcm.dataops.api.configured_sources', factory),
                        patch.object(app, 'process_query', side_effect=AssertionError('legacy parser must not run'))):
            context.start(); self.addCleanup(context.stop)
        self.client = TestClient(app.app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.get('/conversation')

    def submit(self, question, key):
        response = self.client.post('/chat', json={'query': question, 'request_key': key})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['assistant']['topic'], 'dataops_task')
        return result['assistant']

    def wait(self, identifier):
        for _ in range(250):
            response = self.client.get('/api/dataops/agent-runs/' + identifier)
            self.assertEqual(response.status_code, 200, response.text)
            doc = response.json()
            if doc['job']['status'] not in ('queued', 'running'):
                return doc
            time.sleep(.02)
        self.fail('task did not release worker')

    def test_chat_question_download_reload_and_followup(self):
        first = self.submit('整理Alpha的功效、注意事项，保留出处并生成表格', 'chat-first-1')
        parent = self.wait(first['workspace_id'])['workspace']
        self.assertEqual(parent['status'], 'complete', parent)
        path = '/api/dataops/workspaces/' + parent['id'] + '/artifact'
        original = self.client.get(path).content
        history = self.client.get('/conversation').json()
        self.assertEqual(history['assistant']['workspace_id'], parent['id'])
        self.assertEqual(len(history['messages']), 2)
        with patch.object(self.app.agent_runtime, 'memory', ConversationStore(self.memory.path)):
            restored = self.client.get('/conversation').json()
            self.assertEqual(restored['assistant']['workspace_id'], parent['id'])
        second = self.submit('增加贮藏，保留旧字段', 'chat-second-2')
        child = self.wait(second['workspace_id'])['workspace']
        self.assertEqual((child['status'], child['revision']), ('complete', 2), child)
        self.assertEqual(child['progress']['leaf_reads'], 2)
        self.assertEqual(child['progress']['reused_cells'], 4)
        self.assertEqual(self.client.get(path).content, original)
        self.assertEqual(self.client.get('/conversation').json()['assistant']['workspace_id'], child['id'])
        repeated = self.submit('整理Alpha的功效、注意事项，保留出处并生成表格', 'chat-first-1')
        self.assertEqual(repeated['workspace_id'], parent['id'])
        self.assertEqual(len(self.client.get('/api/dataops/workspaces').json()['items']), 2)
        self.assertEqual(len(self.client.get('/conversation').json()['messages']), 4)

    def test_candidate_question_uses_the_same_workbench_answer_endpoint(self):
        output = self.submit('整理Alpha和Gamma的功效、注意，保留出处并生成表格', 'chat-ambiguous')
        doc = self.wait(output['workspace_id'])
        self.assertEqual(doc['job']['status'], 'waiting_input')
        p = doc['workspace']['pending']
        response = self.client.post('/api/dataops/agent-runs/' + output['workspace_id'] + '/answer', json={
            'job_revision': doc['job']['revision'], 'question_id': p['question_id'], 'revision': p['revision'],
            'operation': 'choose', 'candidate_id': 'entry-2'})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.wait(output['workspace_id'])['workspace']['status'], 'complete')

    def test_unrecognized_requirement_is_kept_and_never_sent_to_legacy_parser(self):
        question = '整理Alpha功效并预测靶点，生成表格'
        output = self.submit(question, 'chat-unknown')
        self.assertEqual(output['status'], 'clarification_needed')
        self.assertEqual(output['question'], question)
        self.assertIn('预测靶点', output['message'])
        self.assertNotIn('未确认药名', output['message'])
        self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'], [])
        self.assertEqual(self.client.get('/conversation').json()['assistant']['question'], question)

    def test_owner_and_client_history_cannot_select_another_users_parent(self):
        first = self.submit('整理Alpha功效并生成表格', 'chat-private')
        self.wait(first['workspace_id'])
        other = TestClient(self.app.app); self.addCleanup(other.close)
        other.get('/conversation')
        response = other.post('/chat', json={'query': '增加贮藏', 'request_key': 'fake-parent',
            'conversation_history': [{'dataops': {'workspace_id': first['workspace_id']}}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['assistant']['status'], 'clarification_needed')
        self.assertEqual(other.get('/api/dataops/workspaces/' + first['workspace_id']).status_code, 404)

    def test_csrf_driver_and_storage_errors(self):
        body = {'query': '整理Alpha功效并生成表格', 'request_key': 'chat-guards'}
        self.assertEqual(self.client.post('/chat', json=body, headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        with patch.dict('os.environ', {'DATAOPS_DRIVER': 'deepseek'}):
            self.assertEqual(self.client.post('/chat', json=body).status_code, 409)
        with patch.object(self.app.dataops_service, 'store', side_effect=sqlite3.OperationalError('synthetic storage failure')):
            self.assertEqual(self.client.post('/chat', json=body).status_code, 503)
        self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'], [])

    def test_request_key_cannot_be_reused_for_another_question(self):
        self.wait(self.submit('整理Alpha功效并生成表格', 'shared-request-key')['workspace_id'])
        response = self.client.post('/chat', json={'query': '整理Beta功效并生成表格', 'request_key': 'shared-request-key'})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.client.get('/api/dataops/workspaces').json()['items']), 1)

    def test_ordinary_queries_and_explicit_commands_stay_on_existing_routes(self):
        for question in ('白术的功效', '有多少药材', '/research 白术的功效', '当归和川芎共同靶点'):
            self.assertFalse(matches(question), question)
        with patch.object(self.app, 'process_query', return_value={
                'messages': [AIMessage(content='legacy query retained')], 'agent_name': 'local'}):
            response = self.client.post('/chat', json={'query': '/research 白术的功效'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['response'], 'legacy query retained')


if __name__ == '__main__': unittest.main()
