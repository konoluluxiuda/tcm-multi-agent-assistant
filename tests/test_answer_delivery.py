"""Delivery contract, durable replay and actual synthetic DB regressions."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from tests import test_requirements_v3_http as fixtures
from tcm.dataops.answer_delivery import merge_answer_datas, execute_routes, deliver_saved_query


def requirements():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []}


def output(status='ok', label='FIRST'):
    return {'messages': [AIMessage(content=label)], 'agent_name': 'fixture',
            'tcm_result': {'status': status, 'message': label, 'records': [{
                'source_record_id': 'fixture-' + label,
                'fields': {'medicinal_part': {'state': 'present', 'value': label}},
                'source': {'relative_file_path': label + '.json'}}]}}


class MergeTests(unittest.TestCase):
    def test_partial_evidence_retains_missing_fields(self):
        data = {'research_result': {'status': 'partial_evidence', 'missing_fields': ['注意'],
                                    'message': '注意缺失', 'tool_result': {'hits': []}}}
        result = merge_answer_datas([data], [{'tool': 'research'}])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['unfinished'][0]['missing_fields'], ['注意'])

    def test_single_route_retains_legacy_result_field(self):
        result = merge_answer_datas([output()], [{'tool':'herb'}])
        self.assertEqual(result['tcm'],result['answer_steps'][0]['result'])
        multiple = merge_answer_datas([output(),output()], [{'tool':'herb'},{'tool':'herb'}])
        self.assertNotIn('tcm',multiple)

    def test_missing_output_is_not_success(self):
        result = merge_answer_datas([{}], [{'tool': 'herb'}])
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['unfinished'][0]['status'], 'invalid_tool_result')

    def test_missing_or_empty_routes_rejected(self):
        for datas, routes in [([], []), ([output()], [{'tool':'herb'}, {'tool':'research'}])]:
            with self.assertRaises(ValueError): merge_answer_datas(datas, routes)

    def test_needs_input_is_not_server_outage(self):
        result = merge_answer_datas([output('clarification_needed')], [{'tool': 'herb'}])
        self.assertEqual(result['status'], 'partial')

    def test_step_exceptions_keep_success_without_retry(self):
        from unittest.mock import Mock
        call = Mock(side_effect=[output(label='KEPT'), ValueError('private failure text')])
        result = execute_routes(call, 'question', 'session', [{'tool':'herb'}, {'tool':'herb'}], request_key='key')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('KEPT', result['response'])
        self.assertNotIn('private failure text', json.dumps(result))
        self.assertEqual(call.call_count, 2)
        self.assertTrue(all(c.kwargs['persist_turn'] is False for c in call.call_args_list))


class DeliveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.RequirementsV3HTTPTests('test_workbench_executes_saved_query_proposal')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.client, self.app = self.case.client, self.case.app
        self.session = self.client.cookies.get(self.app.SESSION_COOKIE_NAME)
        self.memory = self.case.memory

    def propose(self, key):
        draft = requirements();model = fixtures.RequirementsSequence(draft, draft)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            r = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha和Beta的药用部位', 'request_key': key})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['proposal']['status'], 'query')
        return r.json()

    def chat(self, key, replies):
        draft = requirements();model = fixtures.RequirementsSequence(draft, draft)
        with patch('tcm.dataops.request_policy.needs_fallback', return_value=True), \
             patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch.object(self.app, 'process_query', side_effect=replies):
            return self.client.post('/chat', json={'query':'Alpha和Beta的药用部位','request_key':key})

    def test_order_independent_partial_http_and_both_failure(self):
        for index, statuses in enumerate([('ok','database_unavailable'), ('database_unavailable','ok'),
                                           ('database_unavailable','database_unavailable')]):
            expected = 'unavailable' if index == 2 else 'partial'
            http = 503 if index == 2 else 200
            replies = [output(status, str(i)) for i,status in enumerate(statuses)]
            r = self.chat('delivery-status-' + str(index), replies)
            self.assertEqual((r.status_code,r.json()['status']), (http,expected), r.text)
            key = 'delivery-wb-status-' + str(index);self.propose(key)
            with patch('agents.agent_decision.process_query', side_effect=replies):
                r = self.client.post('/api/dataops/answer',json={'request_key':key})
            self.assertEqual((r.status_code,r.json()['status']), (http,expected), r.text)

    def test_chat_second_exception_keeps_first(self):
        r = self.chat('delivery-exception', [output(label='KEPT'), RuntimeError('synthetic exception')])
        self.assertEqual(r.status_code, 200,r.text)
        self.assertEqual(r.json()['status'],'partial')
        self.assertIn('KEPT',r.json()['response'])
        self.assertEqual(r.json()['unfinished'][0]['failure']['code'],'tool_execution_failed')

    def test_workbench_replays_without_new_tools_or_turns(self):
        key = 'delivery-repeat'; self.propose(key)
        with patch('agents.agent_decision.process_query', side_effect=[output(label='A'),output(label='B')]) as execute:
            first = self.client.post('/api/dataops/answer',json={'request_key':key})
            self.assertEqual(first.status_code,200,first.text)
            before = self.memory.load(self.session)
            second = self.client.post('/api/dataops/answer',json={'request_key':key})
            self.assertEqual(second.status_code,200,second.text)
            self.assertEqual(execute.call_count,2)
            for c in execute.call_args_list:
                self.assertEqual(c.args[0],'Alpha和Beta的药用部位')
                self.assertEqual(c.kwargs['session_id'],self.session)
                self.assertTrue(c.kwargs['request_key'].startswith(key+'#s'))
                self.assertFalse(c.kwargs['persist_turn'])
        body=second.json()
        self.assertTrue(body['replayed']);self.assertEqual(body['tool_calls_this_submission'],0)
        self.assertEqual(body['model_calls'],0)
        self.assertEqual(self.memory.load(self.session).revision,before.revision)
        self.assertEqual(len(before.messages),2)
        # The persisted marker still prevents recommit after bounded history evicts it.
        self.memory.save(self.session,[HumanMessage(content='later'),AIMessage(content='later')],None,revision=before.revision)
        revision=self.memory.load(self.session).revision
        self.client.post('/api/dataops/answer',json={'request_key':key})
        self.assertEqual(self.memory.load(self.session).revision,revision)

    def test_chat_replay_no_added_tools_and_understanding_count(self):
        draft=requirements();model=fixtures.RequirementsSequence(draft,draft)
        body={'query':'Alpha和Beta的药用部位','request_key':'delivery-chat-repeat'}
        with patch('tcm.dataops.request_policy.needs_fallback',return_value=True), \
             patch('tcm.dataops.research_model.configured_model',return_value=model), \
             patch.object(self.app,'process_query',side_effect=[output(label='A'),output(label='B')]) as execute:
            first=self.client.post('/chat',json=body)
            second=self.client.post('/chat',json=body)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual((first.json()['model_calls'],second.json()['model_calls']),(2,0))
        self.assertEqual((model.calls,execute.call_count),(2,2))
        self.assertEqual(len(self.memory.load(self.session).messages),2)

    def test_real_db_delivery_restores_both_objects(self):
        draft=requirements();model=fixtures.RequirementsSequence(draft,draft)
        herbs=Path(os.environ['DATAOPS_ROOT']).parent/'sources/herbs.sqlite3'
        with patch.dict('os.environ',{'TCM_DB':str(herbs),'TCM_DATASET_VERSION':'fixture-v1'}), \
             patch('tcm.dataops.request_policy.needs_fallback',return_value=True), \
             patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json={'query':'Alpha和Beta的药用部位','request_key':'delivery-real'})
            self.assertEqual(first.status_code,200,first.text)
            restored=self.client.get('/conversation').json()['answer_delivery']
        self.assertEqual(len(restored['answer_steps']),2)
        for token in ('Alpha','Leaf','Beta','Root','Alpha.json','Beta.json'):
            self.assertIn(token,restored['response'])
        snapshot=self.memory.load(self.session)
        self.assertEqual(len(snapshot.messages),2)
        self.assertIsNone(snapshot.tcm_context['herb'])  # no ambiguous implicit herb selection
        self.assertNotIn('Leaf',snapshot.messages[-1].content)  # source text lives in delivery, not model history

    def test_foreign_owner_cannot_execute_or_restore(self):
        key='delivery-private';self.propose(key)
        with patch('agents.agent_decision.process_query',return_value=output()) as call:
            self.assertEqual(self.client.post('/api/dataops/answer',json={'request_key':key}).status_code,200)
            other=TestClient(self.app.app);self.addCleanup(other.close);other.get('/conversation')
            for method,url,kwargs in [('post','/api/dataops/answer',{'json':{'request_key':key}}),
                                      ('get','/api/dataops/answers/'+key,{})]:
                self.assertEqual(getattr(other,method)(url,**kwargs).status_code,404)
            self.assertEqual(call.call_count,2)

    def test_client_routes_rejected(self):
        self.assertEqual(self.client.post('/api/dataops/answer',json={
            'request_key':'delivery-malicious','routes':[{'tool':'herb'}]}).status_code,422)

    def test_durable_result_recover_after_memory_commit_interruption(self):
        key='delivery-commit-crash';self.propose(key)
        from tcm.dataops.api import configured_sources
        from agents.conversation_store import MemoryUnavailable, ConversationStore
        root=self.app.dataops_service.store().root
        kwargs=dict(root=root,memory=self.memory,session_id=self.session,request_key=key,
                    sources_provider=configured_sources,process_query=lambda *a,**k:output())
        with patch.object(self.memory,'save',side_effect=MemoryUnavailable('simulated crash')):
            with self.assertRaises(MemoryUnavailable):deliver_saved_query(**kwargs)
        kwargs['memory']=ConversationStore(self.memory.path)
        kwargs['process_query']=lambda *a,**k: self.fail('completed tools must not rerun')
        answer=deliver_saved_query(**kwargs)
        self.assertTrue(answer['replayed'])
        self.assertEqual(len(kwargs['memory'].load(self.session).messages),2)
        deliver_saved_query(**kwargs)
        self.assertEqual(len(kwargs['memory'].load(self.session).messages),2)

    def test_unknown_execution_reservation_is_not_retried(self):
        class SimulatedDeath(BaseException): pass
        key='delivery-execution-crash';self.propose(key)
        from tcm.dataops.api import configured_sources
        def die(*a,**k):raise SimulatedDeath()
        with self.assertRaises(SimulatedDeath):
            deliver_saved_query(root=self.app.dataops_service.store().root,memory=self.memory,
                session_id=self.session,request_key=key,sources_provider=configured_sources,process_query=die)
        with patch('agents.agent_decision.process_query',side_effect=AssertionError('must not rerun')):
            retry=self.client.post('/api/dataops/answer',json={'request_key':key})
        self.assertEqual(retry.status_code,409,retry.text)
        self.assertEqual(len(self.memory.load(self.session).messages),0)

    def test_each_step_has_diagnostic_events(self):
        r=self.chat('delivery-trace',[output(),output(label='B')])
        trace=self.client.get('/api/dataops/diagnostics/'+r.headers['X-Request-ID'])
        self.assertEqual(trace.status_code,200,trace.text)
        encoded=json.dumps(trace.json(),ensure_ascii=False)
        self.assertIn('answer_step',encoded);self.assertIn('s1',encoded);self.assertIn('s2',encoded)

    def test_synthetic_pdf_contains_source_text(self):
        import fitz
        index,pdf=self.case._pharma_fixture()
        with fitz.open(pdf) as document:
            self.assertEqual(len(document),1)
            self.assertIn('TEST_EFFICACY',document[0].get_text())


if __name__=='__main__':unittest.main()
