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
from tcm.research_jobs import JobStore, JobConflict


class D2ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.records, sources = make_sources(self.root)
        self.memory = ConversationStore(self.root / 'chat.sqlite3')
        for p in (patch.dict('os.environ', {'ENABLE_DATAOPS': '1', 'ENABLE_DATAOPS_AGENT': '1',
            'DATAOPS_DRIVER': 'synthetic', 'DATAOPS_ROOT': str(self.root / 'dataops'),
            'RESEARCH_JOB_DB': str(self.root / 'jobs.sqlite3'), 'RESEARCH_JOB_DSN': '', 'RESEARCH_CHECKPOINT_DSN': ''}),
            patch('tcm.dataops.api.configured_sources', sources)):
            p.start(); self.addCleanup(p.stop)
        app = FastAPI(); app.include_router(create_router(lambda: self.memory, lambda: 'cookie'))
        self.client = TestClient(app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.get('/dataops')

    def create(self, names=('Gamma',), key='agent-request-1'):
        body = dict(request_key=key, question='核对指定资料并生成表格',
                    goal={'query': {'names': list(names), 'limit': 50}})
        response = self.client.post('/api/dataops/agent-runs', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def wait(self, identifier):
        for _ in range(200):
            response = self.client.get('/api/dataops/agent-runs/' + identifier)
            self.assertEqual(response.status_code, 200, response.text)
            doc = response.json()
            if doc['job']['status'] not in ('queued', 'running'):
                return doc
            time.sleep(.02)
        self.fail('job did not release worker')

    def test_human_answer_queue_sse_revision_owner_and_idempotence(self):
        created = self.create(); identifier = created['workspace']['id']
        waiting = self.wait(identifier)
        self.assertEqual(waiting['job']['status'], 'waiting_input', waiting)
        pending = waiting['workspace']['pending']
        events = self.client.get(f'/api/dataops/agent-runs/{identifier}/events')
        self.assertIn('waiting_input', events.text)
        self.assertIn('"terminal": false', events.text)
        body = dict(job_revision=waiting['job']['revision'], question_id=pending['question_id'],
                    revision=1, operation='choose', candidate_id=pending['candidates'][0]['entry_id'])
        path = f'/api/dataops/agent-runs/{identifier}/answer'
        self.assertEqual(self.client.post(path, json={**body, 'candidate_id': 'invalid'}).status_code, 409)
        self.assertEqual(self.client.post(path, json={**body, 'job_revision': 0}).status_code, 409)
        other = TestClient(self.client.app); self.addCleanup(other.close); other.get('/dataops')
        self.assertEqual(other.post(path, json=body).status_code, 404)
        self.assertEqual(self.client.post(path, json=body).status_code, 202)
        self.assertEqual(self.client.post(path, json=body).status_code, 202)
        finished = self.wait(identifier)
        self.assertEqual(finished['job']['status'], 'complete', finished)
        self.assertEqual(self.client.get(f'/api/dataops/workspaces/{identifier}/artifact').status_code, 200)
        repeated = self.create()
        self.assertEqual(repeated['workspace']['id'], identifier)

    def test_waiting_cancel_is_terminal_and_releases_worker(self):
        created = self.create(); identifier = created['workspace']['id']; waiting = self.wait(identifier)
        self.assertEqual(waiting['job']['status'], 'waiting_input')
        second = self.create(('Alpha',), 'agent-request-2')
        self.assertEqual(self.wait(second['workspace']['id'])['job']['status'], 'complete')
        cancelled = self.client.post(f'/api/dataops/agent-runs/{identifier}/cancel', json={})
        self.assertEqual(cancelled.json()['status'], 'cancelled')
        self.assertEqual(self.wait(identifier)['workspace']['status'], 'cancelled')
        p = waiting['workspace']['pending']
        stale = self.client.post(f'/api/dataops/agent-runs/{identifier}/answer', json={
            'job_revision': waiting['job']['revision'], 'question_id': p['question_id'], 'revision': 1, 'operation': 'skip'})
        self.assertEqual(stale.status_code, 409)

    def test_api_new_version_and_feature_default_external_off(self):
        capabilities = self.client.get('/api/dataops/capabilities').json()
        self.assertFalse(capabilities['external_model_enabled'])
        created = self.create(('Alpha',)); parent = self.wait(created['workspace']['id'])['workspace']
        body = dict(request_key='agent-version-2', question='增加贮藏字段', parent_id=parent['id'], expected_revision=1,
                    goal={'query': parent['goal']['query'], 'document_fields': ['功能与主治', '注意', '贮藏']})
        response = self.client.post('/api/dataops/agent-runs', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        child = self.wait(response.json()['workspace']['id'])['workspace']
        self.assertEqual(child['revision'], 2); self.assertIn('diff', child['result'])
        self.assertEqual(child['artifact']['verification']['sheets'], 5)
        self.assertEqual(self.client.post('/api/dataops/agent-runs', json={**body, 'request_key': 'competing-edit'}).status_code, 409)
        with patch.dict('os.environ', {'ENABLE_DATAOPS_AGENT': '0'}):
            self.assertFalse(self.client.get('/api/dataops/capabilities').json()['agent'])
            self.assertEqual(self.client.post('/api/dataops/agent-runs', json=body).status_code, 409)
        with patch.dict('os.environ', {'DATAOPS_DRIVER': 'deepseek'}):
            self.assertFalse(self.client.get('/api/dataops/capabilities').json()['agent'])
            self.assertEqual(self.client.post('/api/dataops/agent-runs', json=body).status_code, 409)
        with patch.dict('os.environ', {'RESEARCH_JOB_DSN': 'postgresql://not-used'}):
            self.assertFalse(self.client.get('/api/dataops/capabilities').json()['agent'])
            self.assertEqual(self.client.post('/api/dataops/agent-runs', json=body).status_code, 409)

    def test_csrf_and_contract(self):
        body = {'request_key': 'csrf-request', 'question': '核对', 'goal': {}}
        self.assertEqual(self.client.post('/api/dataops/agent-runs', json=body,
                                         headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        self.assertEqual(self.client.post('/api/dataops/agent-runs', json={**body, 'sql': 'select 1'}).status_code, 422)

    def test_main_app_restricted_profile_allows_d2_submit_and_answer(self):
        import app
        with patch.object(app.agent_runtime, 'memory', self.memory), patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY': '1'}), TestClient(app.app) as client:
            client.get('/dataops')
            response = client.post('/api/dataops/agent-runs', json={'request_key': 'main-dataops-run',
                'question': '核对Gamma', 'goal': {'query': {'names': ['Gamma']}}})
            self.assertEqual(response.status_code, 202, response.text)
            identifier = response.json()['workspace']['id']
            for _ in range(200):
                value = client.get('/api/dataops/agent-runs/' + identifier).json()
                if value['job']['status'] == 'waiting_input':break
                time.sleep(.02)
            self.assertEqual(value['job']['status'], 'waiting_input', value)
            p = value['workspace']['pending']
            response = client.post(f'/api/dataops/agent-runs/{identifier}/answer', json={
                'job_revision': value['job']['revision'], 'question_id': p['question_id'], 'revision': 1, 'operation': 'skip'})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(client.post('/api/dataops/agent-runs/not-a-valid-id/answer', json={}).status_code, 403)


class QueueTests(unittest.TestCase):
    def test_expired_lease_recovers_with_revision_but_unknown_dispatch_cannot(self):
        with tempfile.TemporaryDirectory() as root:
            clock = [100.0]; store = JobStore(Path(root) / 'jobs.db', clock=lambda: clock[0])
            doc, _ = store.enqueue('owner', '核对', 'key-1', payload={'kind': 'dataops'})
            _, running = store.claim('old-worker')
            clock[0] += 31; store.claim('new-worker')
            interrupted = store.get('owner', doc['id'])
            self.assertEqual(interrupted['status'], 'interrupted')
            with self.assertRaises(JobConflict):store.resume('owner', doc['id'], expected_revision=0)
            queued = store.resume('owner', doc['id'], expected_revision=interrupted['revision'])
            self.assertEqual(queued['status'], 'queued')
            _, running = store.claim('new-worker')
            unknown = store.save('owner', running, 'new-worker', status='interrupted',
                                 result={'reason': 'model_dispatch_unknown'})
            with self.assertRaises(JobConflict): store.resume('owner', doc['id'], expected_revision=unknown['revision'])


if __name__ == '__main__': unittest.main()
