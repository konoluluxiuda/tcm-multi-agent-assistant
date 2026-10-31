from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tcm.dataops.api import create_router
from tcm.dataops.presentation import workspace_view, selection_message, public_response
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchFixed
from tcm.dataops.workspace import Workspaces
from tcm.dataops.artifacts import verify


class PresentationTests(unittest.TestCase):
    def document(self, driver='research_fixed_field_policy'):
        return dict(id='test', created=1, goal={'research': {}}, source_revision={}, mode='dynamic_A',
                    progress={'driver': driver, 'model_calls': 3}, status='running')

    def test_counts_distinguish_local_decisions_goal_and_unknown_native_requests(self):
        original = self.document(); original['goal_understanding'] = {'provider_requests': 1}
        before = deepcopy(original); view = workspace_view(original)
        self.assertEqual((view['mode'], view['progress']['decision_steps'], view['progress']['provider_requests']),
                         ('research_fixed', 3, 0))
        self.assertEqual(view['execution']['goal_provider_requests'], 1)
        self.assertNotIn('model_calls', view['progress']); self.assertEqual(original, before)
        self.assertEqual(workspace_view(view), view)
        for driver in ('research_native_tool_calling_v2', 'native_tool_calling', 'unknown_driver'):
            doc = self.document(driver); doc['status'] = 'interrupted'
            self.assertIsNone(workspace_view(doc)['execution']['provider_requests'])
        doc = self.document('research_native_tool_calling_v2')
        doc['result'] = {'model_calls': 5, 'research': {'provider_requests': 5}}
        self.assertEqual(workspace_view(doc)['execution']['provider_requests'], 5)

    def test_synthetic_pending_and_legacy_summary_do_not_invent_exclusions(self):
        self.assertEqual(workspace_view(self.document('research_synthetic_observation_policy'))['mode'], 'research_synthetic')
        doc = self.document(); doc.pop('progress')
        self.assertEqual(workspace_view(doc)['mode'], 'execution_pending_or_unknown')
        message = selection_message({'qualified_count': 0, 'candidate_states': [
            {'source_id': 'old', 'eligible': False, 'status': 'candidate_selected'}]})
        self.assertIn('历史记录未保存', message); self.assertNotIn('药用部位差异', message)
        self.assertIsNone(selection_message({'task_mode': 'audit_named', 'qualified_count': 0}))

    def test_queue_snapshot_counter_view_keeps_saved_protocol_unchanged(self):
        job = {'payload': {'kind': 'dataops'}, 'snapshot': {'model_calls': 3,
                'result': {'model_calls': 3}, 'execution_limits': {'model_calls': 10}}}
        before = deepcopy(job); shown = public_response(job)
        self.assertEqual(shown['snapshot']['decision_steps'], 3)
        self.assertEqual(shown['snapshot']['result']['decision_steps'], 3)
        self.assertEqual(shown['snapshot']['execution_limits']['decision_steps'], 10)
        self.assertEqual(job, before)

    def test_public_api_history_replay_and_artifact_preserve_stored_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); records, sources = make_sources(root)
            memory = ConversationStore(root/'chat.sqlite3')
            with patch.dict('os.environ', {'ENABLE_DATAOPS': '1', 'DATAOPS_ROOT': str(root/'state')}):
                app = FastAPI(); app.include_router(create_router(lambda: memory, lambda: 'cookie'))
                with TestClient(app) as client:
                    client.get('/dataops')
                    owner = hashlib.sha256(client.cookies.get('cookie').encode()).hexdigest()
                    store = Workspaces(root/'state')
                    goal = ResearchGoal(query={'names': ['Alpha', 'Beta'], 'limit': 20}, attributes=['medicinal_part'],
                                        document_fields=['注意'], research={'target_count': 2})
                    with sources() as s:
                        doc, _ = store.reserve_agent(owner, 'presentation-test', goal.model_dump(), s.revision, question='测试')
                    store.project(owner, doc['id'], job_id='existing-job')
                    done = ResearchEngine(store, owner, doc['id'], sources, ResearchFixed()).run()
                    stored_before = store.get(owner, doc['id'])
                    path, _ = store.artifact(owner, doc['id']); content = path.read_bytes()
                    route = '/api/dataops/workspaces/'+doc['id']
                    public = client.get(route).json()
                    self.assertEqual(public['mode'], 'research_fixed')
                    self.assertEqual(public['execution']['provider_requests'], 0)
                    self.assertNotIn('model_calls', public['progress']); self.assertNotIn('model_calls', public['result'])
                    self.assertIn('0 条满足', public['selection_message'])
                    self.assertIn('注意：未收录', public['selection_message'])
                    self.assertEqual(public['result']['rows'], [])
                    self.assertTrue(all(x['reasons'] for x in done['result']['research']['candidate_states']))
                    history = client.get('/api/dataops/workspaces').json()['items'][0]
                    self.assertEqual(history['mode'], 'research_fixed')
                    self.assertTrue(history['execution']['queued_execution'])
                    self.assertEqual(client.get(route+'/artifact').content, content)
                    self.assertEqual(client.get(route).json(), public)
                    self.assertEqual(store.get(owner, doc['id']), stored_before)
                    verify(path, public['result']); self.assertEqual(path.read_bytes(), content)


if __name__ == '__main__': unittest.main()
