"""Lost responses and session fencing, using fixture sources and no model."""
import unittest
from unittest.mock import patch

from tests import test_preset_requests as fixtures


class ChatRecoveryTests(unittest.TestCase):
    setUp = fixtures.PresetHTTPTests.setUp
    wait = fixtures.PresetHTTPTests.wait

    def body(self, key='browser-request-1', query='分别告诉我Alpha和Beta的ETCM药用部位。'):
        return {'query': query, 'request_key': key,
                'request_scope': self.client.get('/conversation').json()['request_scope']}

    def test_lost_query_response_is_restored_and_replayed_without_execution(self):
        import app
        body = self.body()
        with patch.object(app, 'process_query', wraps=app.process_query) as execute:
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 200, first.text)
            calls = execute.call_count
            self.assertEqual(calls, 2)
            # The browser did not receive first; restoring history is read-only.
            history = self.client.get('/conversation').json()
            self.assertIn(body['request_key'], history['acknowledged_request_keys'])
            self.assertEqual(history['answer_delivery']['request_key'], body['request_key'])
            replay = self.client.post('/chat', json=body)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json()['replayed'])
            self.assertEqual(execute.call_count, calls)
            self.assertEqual(len(history['messages']), 2)

    def test_lost_table_response_preserves_single_workspace_and_artifact(self):
        body = self.body(query='把Alpha和Beta的ETCM药用部位生成表格。')
        first = self.client.post('/chat', json=body)
        self.assertEqual(first.status_code, 200, first.text)
        identifier = first.json()['assistant']['workspace_id']
        doc = self.wait(identifier)
        path = '/api/dataops/workspaces/' + identifier + '/artifact'
        artifact = self.client.get(path).content
        history = self.client.get('/conversation').json()
        self.assertIn(body['request_key'], history['acknowledged_request_keys'])
        self.assertEqual(history['assistant']['workspace_id'], identifier)
        replay = self.client.post('/chat', json=body)
        self.assertEqual(replay.json()['assistant']['workspace_id'], identifier)
        self.assertEqual(len(self.client.get('/api/dataops/workspaces').json()['items']), 1)
        self.assertEqual(self.client.get(path).content, artifact)
        self.assertEqual(doc['status'], 'complete')

    def test_clear_rotates_scope_and_rejects_request_from_other_old_tab(self):
        import app
        body = self.body()
        cookie = self.client.cookies.get(app.SESSION_COOKIE_NAME)
        self.assertNotEqual(body['request_scope'], cookie)
        self.assertEqual(self.client.get('/conversation').json()['request_scope'], body['request_scope'])
        cleared = self.client.delete('/conversation').json()
        self.assertNotEqual(cleared['request_scope'], body['request_scope'])
        self.assertEqual(self.client.get('/conversation').json()['request_scope'], cleared['request_scope'])
        with patch.object(app, 'process_query', side_effect=AssertionError('must not execute')):
            response = self.client.post('/chat', json=body)
        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(self.client.get('/conversation').json()['acknowledged_request_keys'], [])

    def test_expired_session_does_not_silently_move_pending_request(self):
        import app
        body = self.body()
        self.assertEqual(self.client.post('/chat', json=body).status_code, 200)
        memory = app.agent_runtime.memory
        with patch.object(memory, 'clock', return_value=memory.clock() + memory.ttl_seconds + 1):
            response = self.client.post('/chat', json=body)
            self.assertEqual(response.status_code, 410, response.text)
            refreshed = self.client.get('/conversation').json()
            self.assertNotEqual(refreshed['request_scope'], body['request_scope'])

    def test_changed_question_with_same_key_is_rejected(self):
        body = self.body()
        self.assertEqual(self.client.post('/chat', json=body).status_code, 200)
        body['query'] = '查询Alpha的ETCM科名。'
        self.assertEqual(self.client.post('/chat', json=body).status_code, 409)

    def test_scope_without_cookie_cannot_authorize_request(self):
        from fastapi.testclient import TestClient
        body = self.body()
        with TestClient(self.app.app) as other:
            self.assertEqual(other.post('/chat', json=body).status_code, 410)

    def test_clarification_is_acknowledged_without_creating_workspace(self):
        body = self.body(query='给出Alpha和Beta的相关信息')
        response = self.client.post('/chat', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['assistant']['status'], 'clarification_needed')
        self.assertIn(body['request_key'], self.client.get('/conversation').json()['acknowledged_request_keys'])
        self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'], [])
