"""Chat diagnostics correlate failures without logging payloads or credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agents.conversation_store import ConversationStore, MemoryUnavailable, ConversationConflict, SessionExpired
from tcm.request_observability import LOGGER


class RequestDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        import app
        self.routes = app
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.memory = ConversationStore(Path(temp.name) / 'memory.sqlite3')
        patches = [patch.object(app.agent_runtime, 'memory', self.memory),
                   patch.dict(os.environ, {'RESEARCH_TOOLS_ONLY': '1', 'ENABLE_NATURAL_ROUTING': '1'}),
                   patch.object(app, 'resolve_tool_request', return_value={
                       'tool': 'research', 'request': {'question': 'fixture'}, 'mode': 'explicit'}),
                   patch.object(app, 'process_query', return_value={
                       'messages': [AIMessage(content='fixture')], 'agent_name': 'RESEARCH_AGENT',
                       'research_result': {'status': 'candidates'}})]
        self.processor = patches[-1].start()
        self.addCleanup(patches[-1].stop)
        for item in patches[:-1]:
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def turn(self, **kwargs):
        with self.assertLogs(LOGGER.name, level='INFO') as captured:
            response = self.client.post('/chat', **kwargs)
        self.assertEqual(len(captured.records), 1)
        record = json.loads(captured.records[0].getMessage())
        self.assertRegex(response.headers['X-Request-ID'], r'^[a-f0-9]{32}$')
        self.assertEqual(record['request_id'], response.headers['X-Request-ID'])
        self.assertEqual(record['http_status'], response.status_code)
        self.assertGreaterEqual(record['duration_ms'], 0)
        self.assertEqual(response.headers['Server-Timing'], f"chat;dur={record['duration_ms']}")
        self.assertEqual(set(record), {'event', 'request_id', 'tool', 'phase', 'error_kind',
                                       'model_calls', 'http_status', 'duration_ms', 'outcome'})
        return response, record

    def test_success_has_server_generated_id_without_request_content(self):
        response, record = self.turn(json={'query': 'private question and source fragment'},
            headers={'X-Request-ID': 'forged-private-id', 'Cookie': 'secret-cookie=value',
                     'Authorization': 'Bearer secret-provider-key'}, params={'private': 'query-parameter'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((record['tool'], record['phase'], record['outcome']), ('research', 'response', 'success'))
        self.assertIsNone(record['model_calls'])  # Unknown is not reported as zero.
        for secret in ('private', 'secret', 'fragment', 'value', 'forged'):
            self.assertNotIn(secret, json.dumps(record))

    def test_partial_and_model_failure_keep_result_and_measured_call_count(self):
        for state, status, outcome in [('partial', 200, 'partial'), ('generation_failed', 502, 'unavailable')]:
            with self.subTest(state=state):
                self.processor.return_value = {'messages': [AIMessage(content='fixture')],
                    'agent_name': 'EVIDENCE_ASSISTANT', 'evidence_answer': {'status': state, 'model_calls': 2}}
                response, record = self.turn(json={'query': 'fixture'})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['evidence_answer']['status'], state)
                self.assertEqual((record['tool'], record['model_calls'], record['outcome']), ('evidence', 2, outcome))

    def test_memory_failures_preserve_status_without_exception_text(self):
        for error, status, kind in [(MemoryUnavailable, 503, 'memory_unavailable'),
                                    (ConversationConflict, 409, 'conversation_conflict'),
                                    (SessionExpired, 410, 'session_expired')]:
            with self.subTest(kind=kind):
                self.processor.side_effect = error('private storage path and cookie')
                response, record = self.turn(json={'query': 'private question'})
                self.assertEqual(response.status_code, status)
                self.assertEqual(record['error_kind'], kind)
                self.assertEqual(record['phase'], 'execution')
                self.assertNotIn('private', response.text + json.dumps(record))

    def test_unexpected_execution_failure_is_sanitized_without_retry(self):
        self.processor.side_effect = RuntimeError('private provider key and source text')
        response, record = self.turn(json={'query': 'fixture'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual((record['error_kind'], record['phase']), ('internal_error', 'execution'))
        self.assertNotIn('private', response.text + json.dumps(record))
        self.assertEqual(self.processor.call_count, 1)

    def test_failure_before_execution_is_also_correlated(self):
        with patch.object(self.routes, 'resolve_tool_request', side_effect=ValueError('private source path')):
            response, record = self.turn(json={'query': 'fixture'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual((record['error_kind'], record['phase']), ('internal_error', 'routing'))
        self.assertNotIn('private', response.text + json.dumps(record))
        self.processor.assert_not_called()

    def test_validation_and_disabled_mode_never_call_model(self):
        response, record = self.turn(json={'answer_mode': 'unknown'})
        self.assertEqual((response.status_code, record['phase']), (422, 'validation'))
        with patch.dict(os.environ, {'ENABLE_EVIDENCE_ASSISTANT': '0'}):
            response, record = self.turn(json={'query': 'fixture', 'answer_mode': 'evidence'})
        self.assertEqual((response.status_code, record['outcome']), (503, 'unavailable'))
        self.processor.assert_not_called()

    def test_concurrent_requests_do_not_share_diagnostics(self):
        with self.assertLogs(LOGGER.name, level='INFO') as captured, ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post('/chat', json={'query': 'fixture'}), range(4)))
        identifiers = {r.headers['X-Request-ID'] for r in responses}
        self.assertEqual(len(identifiers), 4)
        self.assertEqual({json.loads(r.getMessage())['request_id'] for r in captured.records}, identifiers)
        self.assertTrue(all(r.status_code == 200 for r in responses))

    def test_health_polling_does_not_emit_chat_records(self):
        with self.assertNoLogs(LOGGER.name, level='INFO'):
            response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('X-Request-ID', response.headers)


if __name__ == '__main__':
    unittest.main()
