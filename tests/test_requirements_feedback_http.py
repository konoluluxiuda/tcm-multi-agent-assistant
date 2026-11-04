"""Real ASGI, fixture databases and deliveries; only model responses are substituted."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import unittest
from tests import test_unified_request_harness as legacy
from tests.test_requirements_v3_http import RequirementsSequence
from tcm.dataops.data_access import SourceChanged


def draft():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []}


class FeedbackHTTPTests(unittest.TestCase):
    wait = legacy.EntryTests.wait

    def setUp(self):
        legacy.EntryTests.setUp(self)
        setting = patch.dict('os.environ', {'DATAOPS_UNDERSTANDING_POLICY': 'requirements_feedback_v1',
            'TCM_DB': str(Path(self.tmp.name) / 'sources/herbs.sqlite3'), 'TCM_DATASET_VERSION': 'fixture-v1'})
        setting.start()
        self.addCleanup(setting.stop)

    def test_chat_tool_feedback_repairs_then_delivers_both_objects_once(self):
        good = draft()
        bad = deepcopy(good)
        bad['requirements'][0]['fields'][0]['name'] = 'Medicinal Part'
        model = RequirementsSequence(bad, good, good)
        body = {'query': '分别告诉我Alpha和Beta的药用部位', 'request_key': 'feedback-chat-1'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model) as factory:
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 200, first.text)
            result = first.json()
            self.assertEqual(result['status'], 'success', result)
            self.assertEqual(len(result['answer_steps']), 2)
            self.assertEqual(result['model_calls'], 3)
            factory.assert_called_once_with(max_requests=3)
            again = self.client.post('/chat', json=body)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()['model_calls'], 0)
            self.assertEqual(model.calls, 3)
            self.assertEqual(again.json()['answer_steps'], result['answer_steps'])

    def test_workbench_table_file_and_append_preserve_parent(self):
        good = draft()
        good['delivery'] = {'form': 'table', 'row_policy': 'keep_all'}
        good['requirements'][0]['fields'].append({'namespace': 'pharmacopoeia', 'name': '功能与主治'})
        model = RequirementsSequence(good, good)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/api/dataops/research-questions', json={
                'question': '把Alpha和Beta的药用部位和药典功效做成表格', 'request_key': 'feedback-table-1'})
            self.assertEqual(first.status_code, 202, first.text)
            parent = self.wait(first.json()['workspace']['id'])
        self.assertEqual(parent['status'], 'complete')
        self.assertEqual(parent['goal_understanding']['policy'], 'requirements_feedback_v1')
        url = '/api/dataops/workspaces/' + parent['id'] + '/artifact'
        original = self.client.get(url)
        self.assertEqual(original.status_code, 200)
        child = deepcopy(good)
        child['operation'] = 'append_fields'
        child['requirements'][0]['fields'] = [{'namespace': 'pharmacopoeia', 'name': '注意'}]
        with patch('tcm.dataops.research_model.configured_model', return_value=RequirementsSequence(child, child)):
            appended = self.client.post('/api/dataops/research-questions', json={
                'question': '增加注意事项，保留旧字段', 'parent_id': parent['id'],
                'expected_revision': parent['revision'], 'request_key': 'feedback-table-2'})
            self.assertEqual(appended.status_code, 202, appended.text)
            doc = self.wait(appended.json()['workspace']['id'])
        self.assertEqual(doc['status'], 'complete')
        self.assertEqual(set(doc['goal']['document_fields']), {'功能与主治', '注意'})
        self.assertEqual(self.client.get(url).content, original.content)
        self.assertEqual(self.client.get('/api/dataops/workspaces/' + doc['id'] + '/artifact').status_code, 200)

    def test_semantic_disagreement_creates_no_workspace_or_final_query(self):
        good = draft()
        other = deepcopy(good)
        other['requirements'][0]['fields'][0]['name'] = 'family'
        with patch('tcm.dataops.research_model.configured_model', return_value=RequirementsSequence(good, other)):
            response = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha和Beta的药用部位', 'request_key': 'feedback-disagree'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['proposal']['failure']['code'], 'semantic_disagreement')
        self.assertNotIn('workspace', response.json())
        self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'], [])

    def test_local_optout_never_constructs_model(self):
        with patch('tcm.dataops.research_model.configured_model') as factory:
            response = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha的药用部位', 'request_key': 'feedback-local-1', 'use_model': False})
        self.assertEqual(response.status_code, 200, response.text)
        factory.assert_not_called()
        self.assertEqual(response.json()['proposal']['external_model_calls'], 0)

    def test_unknown_repair_is_not_repeated_by_same_request_key(self):
        good = draft()
        bad = deepcopy(good)
        bad['requirements'][0]['fields'][0]['name'] = 'UnknownField'
        model = RequirementsSequence(bad, TimeoutError('unknown'))
        body = {'question': 'Alpha和Beta的药用部位', 'request_key': 'feedback-unknown-1'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            response = self.client.post('/api/dataops/research-questions', json=body)
            self.assertEqual(response.status_code, 503, response.text)
            again = self.client.post('/api/dataops/research-questions', json=body)
        self.assertIn(again.status_code, (409, 503))
        self.assertEqual(model.calls, 2)

    def test_source_version_change_is_not_reported_as_model_timeout(self):
        model = RequirementsSequence(draft())
        with patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch('tcm.dataops.requirements_feedback.probe_availability', side_effect=SourceChanged('private')):
            response = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha和Beta的药用部位', 'request_key': 'feedback-source-change'})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn('private', response.text)
        self.assertEqual(model.calls, 1)

    def test_workbench_unsupported_request_stops_before_model(self):
        with patch('tcm.dataops.research_model.configured_model') as factory:
            response = self.client.post('/api/dataops/research-questions', json={
                'question': '给患者推荐Alpha治疗方案', 'request_key': 'feedback-boundary-1'})
        self.assertEqual(response.status_code, 200, response.text)
        factory.assert_not_called()
        self.assertEqual(response.json()['proposal']['failure']['code'], 'unsupported_requirement')

    def test_local_probe_failure_is_not_reported_as_unknown_model_dispatch(self):
        from tcm.dataops.requirements_feedback import SourceProbeFailed
        model = RequirementsSequence(draft())
        with patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch('tcm.dataops.requirements_feedback.probe_availability', side_effect=SourceProbeFailed()):
            response = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha和Beta的药用部位', 'request_key': 'feedback-probe-failure'})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('本地资料预检失败', response.text)
        self.assertNotIn('理解状态未知', response.text)
        self.assertEqual(model.calls, 1)
