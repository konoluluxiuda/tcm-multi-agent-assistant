"""Offline review probes: passing means the described defect is reproduced."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from tests import test_requirements_v3_http as fixtures
from tcm.dataops.answer_delivery import merge_answer_datas


def draft():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []}


def data(status='ok', label='FIRST'):
    return {'messages': [AIMessage(content=label)], 'agent_name': 'fixture',
            'tcm_result': {'status': status, 'message': label, 'records': [{
                'source_record_id': 'fixture-' + label,
                'fields': {'medicinal_part': {'state': 'present', 'value': label}},
                'source': {'relative_file_path': label + '.json'}}]}}


class DeliveryReview(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.RequirementsV3HTTPTests('test_workbench_executes_saved_query_proposal')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def record(self, name, result):
        Path(__file__).with_name(name + '.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')

    def proposal(self, key):
        model = fixtures.RequirementsSequence(draft(), draft())
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            r = self.case.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha和Beta的药用部位', 'request_key': key})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['proposal']['status'], 'query')
        return r.json()

    def chat(self, replies, key):
        model = fixtures.RequirementsSequence(draft(), draft())
        with patch('tcm.dataops.request_policy.needs_fallback', return_value=True), \
             patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch.object(self.case.app, 'process_query', side_effect=replies):
            return self.case.client.post('/chat', json={
                'query': 'Alpha和Beta的药用部位', 'request_key': key})

    def test_partial_evidence_is_promoted_to_success(self):
        r = merge_answer_datas([{
            'messages': [AIMessage(content='部分功效可用，注意缺失')],
            'research_result': {'status': 'partial_evidence', 'message': '部分功效可用，注意缺失',
                                'missing_fields': ['注意'], 'tool_result': {'hits': []}}
        }], [{'tool': 'research'}])
        self.assertEqual(r['status'], 'success')
        self.assertEqual(r['unfinished'], [])
        self.record('partial_promoted', r)

    def test_first_failed_second_ok_has_partial_body_but_http503(self):
        r = self.chat([data('database_unavailable'), data(label='SECOND')], 'review2-first-failed')
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()['status'], 'partial')
        self.record('first_failed', {'http': r.status_code, 'body': r.json()})

    def test_workbench_all_failed_returns_http200(self):
        self.proposal('review2-wb-failed')
        with patch('agents.agent_decision.process_query', return_value=data('database_unavailable')):
            r = self.case.client.post('/api/dataops/answer', json={'request_key': 'review2-wb-failed'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'unavailable')
        self.record('workbench_failed', {'http': r.status_code, 'body': r.json()})

    def test_workbench_reexecutes_saved_key_without_original_question_or_session(self):
        created = self.proposal('review2-wb-replay')
        with patch('agents.agent_decision.process_query', return_value=data()) as execute:
            a = self.case.client.post('/api/dataops/answer', json={'request_key': 'review2-wb-replay'})
            b = self.case.client.post('/api/dataops/answer', json={'request_key': 'review2-wb-replay'})
        self.assertEqual(a.status_code, 200)
        self.assertEqual(b.status_code, 200)
        self.assertEqual(execute.call_count, 4)  # two routes, executed on both submissions
        self.assertTrue(all(c.args[0] == '' for c in execute.call_args_list))
        self.assertTrue(all(c.kwargs['session_id'] is None for c in execute.call_args_list))
        self.assertTrue(all('request_key' not in c.kwargs for c in execute.call_args_list))
        self.record('workbench_replay', {'calls': [{'args': c.args, 'kwargs': c.kwargs}
                                                for c in execute.call_args_list],
                                         'proposal_keys': list(created['proposal']),
                                         'real_model_requests': 0})

    def test_second_exception_loses_first_success(self):
        r = self.chat([data(label='SUCCESS_FIRST'), RuntimeError('fixture tool failure')],
                      'review2-second-exception')
        self.assertEqual(r.status_code, 500, r.text)
        self.assertNotIn('SUCCESS_FIRST', r.text)
        self.record('second_exception', {'http': r.status_code, 'body': r.json()})

    def test_real_chat_saves_only_first_route_context(self):
        from agents import agent_decision as runtime
        herbs = Path(os.environ['DATAOPS_ROOT']).parent / 'sources' / 'herbs.sqlite3'
        model = fixtures.RequirementsSequence(draft(), draft())
        outputs = []
        original = self.case.app.process_query

        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            outputs.append(result)
            return result

        with patch.dict('os.environ', {'TCM_DB': str(herbs), 'TCM_DATASET_VERSION': 'fixture-v1'}), \
             patch('tcm.dataops.request_policy.needs_fallback', return_value=True), \
             patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch.object(self.case.app, 'process_query', side_effect=observe):
            response = self.case.client.post('/chat', json={
                'query': 'Alpha和Beta的药用部位', 'request_key': 'review2-real-memory'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('Leaf', response.json()['response'])
        self.assertIn('Root', response.json()['response'])
        self.assertEqual(len(outputs), 2)
        session = self.case.client.cookies.get(self.case.app.SESSION_COOKIE_NAME)
        snapshot = runtime.memory.load(session)
        self.assertEqual(snapshot.messages[-1].content, outputs[0]['messages'][-1].content)
        self.assertNotEqual(outputs[0]['tcm_context'], outputs[1]['tcm_context'])
        from tcm.agent_research import split_contexts
        saved_herb, _ = split_contexts(snapshot.tcm_context)
        self.assertEqual(saved_herb, outputs[0]['tcm_context'])
        self.record('saved_first_only', {'http': response.json(),
            'saved_messages': [m.model_dump() for m in snapshot.messages],
            'saved_herb_context': saved_herb, 'second_herb_context': outputs[1]['tcm_context']})


if __name__ == '__main__':
    unittest.main()
