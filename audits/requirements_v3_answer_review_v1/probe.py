"""Offline defect reproductions, not acceptance tests (passing reproduces defects).

Use scripts.run_offline_ci.worker with test_environment(); no real provider or corpus.
The request, understanding pipeline and HTTP assembly are real; tool outputs are
synthetic substitutes, so this does not claim retrieval end-to-end coverage.
"""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from tests import test_requirements_v3_http as fixtures


class AnswerAssemblyProbe(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.RequirementsV3HTTPTests('test_answer_cross_source_carries_all_routes')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def run_case(self, second_status):
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                     'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                                {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = fixtures.RequirementsSequence(draft, draft)
        primary = {'messages': [AIMessage(content='FIRST_PLACEHOLDER')], 'agent_name': 'fixture-herb',
                   'tcm_result': {'status': 'ok', 'message': '资料如下', 'records': [{
                       'source_record_id': 'fixture-herb',
                       'fields': {'medicinal_part': {'state': 'present', 'value': 'SYNTHETIC_LEAF'}},
                       'source': {'relative_file_path': 'fixture-herb.json'}}]}}
        secondary = {'messages': [AIMessage(content='SECOND_PLACEHOLDER')], 'agent_name': 'fixture-research',
                     'research_result': {'status': second_status,
                         'message': 'SECOND_RESULT', 'tool_result': {'hits': [{
                             'name': 'Alpha', 'label': '功能与主治', 'text': 'SYNTHETIC_EFFICACY',
                             'evidence': [{'source': {'source_document': 'fixture.pdf', 'source_pdf_page': 7}}]
                         }]} if second_status == 'candidates' else None}}
        with patch('tcm.dataops.request_policy.needs_fallback', return_value=True), \
             patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch.object(self.case.app, 'process_query', side_effect=[primary, secondary]) as execute:
            response = self.case.client.post('/chat', json={
                'query': 'Alpha的药用部位和功效', 'request_key': 'answer-review-' + second_status})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.calls, 2)
        self.assertEqual(execute.call_count, 2)
        body = response.json()
        record = {'http_status': response.status_code, 'response': body,
                  'fixture_model_calls': model.calls, 'real_model_requests': 0,
                  'executed_routes': [c.kwargs['resolved_request'] for c in execute.call_args_list],
                  'secondary_tool_status': second_status,
                  'scope': 'real ASGI + requirements pipeline, substituted tool results'}
        Path(__file__).with_name(second_status + '.json').write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return body

    def test_reproduce_second_success_missing_from_answer(self):
        body = self.run_case('candidates')
        self.assertIn('SYNTHETIC_LEAF', body['response'])
        self.assertNotIn('SYNTHETIC_EFFICACY', json.dumps(body))
        self.assertNotIn('research', body)
        self.assertNotIn('SECOND_PLACEHOLDER', body['response'])

    def test_reproduce_second_failure_reported_as_success(self):
        body = self.run_case('research_unavailable')
        self.assertEqual(body['status'], 'success')
        self.assertNotIn('research_unavailable', json.dumps(body))


if __name__ == '__main__':
    unittest.main()
