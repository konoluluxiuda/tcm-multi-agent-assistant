"""U4 ASGI full-chain test: chat -> requirements_v3 -> fixed delivery -> XLSX."""
import json
import unittest
from fastapi.testclient import TestClient
from unittest.mock import patch
from langchain_core.messages import AIMessage
from tests import test_unified_request_harness as legacy


class RequirementsSequence:
    max_retries = 0

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.http_client = _Client()

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages):
        self.calls += 1
        if not self.replies:
            raise AssertionError('Unexpected extra model request')
        args = self.replies.pop(0)
        if isinstance(args, Exception):
            raise args
        return AIMessage(content='', tool_calls=[{'id': str(self.calls),
                                                  'name': 'submit_requirements_v2', 'args': args}],
                         usage_metadata={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12})


class _Client:
    def close(self):
        pass


def table_draft():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}],
            'requirements': [
                {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                 'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'},
                            {'namespace': 'pharmacopoeia', 'name': '注意'}]},
            ],
            'delivery': {'form': 'table', 'include_evidence': True, 'row_policy': 'keep_all'},
            'unresolved': []}


class RequirementsV3HTTPTests(unittest.TestCase):
    wait = legacy.EntryTests.wait

    def setUp(self):
        legacy.EntryTests.setUp(self)
        p = patch.dict('os.environ', {'DATAOPS_UNDERSTANDING_POLICY': 'requirements_v3'})
        p.start()
        self.addCleanup(p.stop)

    def test_table_chain_delivers_goal_and_xlsx(self):
        draft = table_draft()
        model = RequirementsSequence(draft, draft)  # initial + independent review
        body = {'query': '整理Alpha和Beta的功效和注意，生成表格', 'request_key': 'reqv3-http-1'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['model_calls'], 2)
            wid = first.json()['assistant']['workspace_id']
            doc = self.wait(wid)
            self.assertEqual(doc['status'], 'complete')
            self.assertEqual(len(doc['result']['rows']), 2)
            self.assertEqual(doc['goal_understanding']['policy'], 'requirements_v3')
            self.assertEqual(self.client.get('/api/dataops/workspaces/' + wid + '/artifact').status_code, 200)

    def test_idempotent_replay_no_new_model_call(self):
        draft = table_draft()
        model = RequirementsSequence(draft, draft)
        body = {'query': '整理Alpha和Beta的功效和注意，生成表格', 'request_key': 'reqv3-http-replay'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 200, first.text)
            again = self.client.post('/chat', json=body)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()['model_calls'], 0)
            self.assertEqual(model.calls, 2)  # no third call



    def test_local_optout_no_model_dispatch(self):
        model = RequirementsSequence(table_draft(), table_draft())
        body = {'question': '整理Alpha和Beta的功效和注意，生成表格',
                'request_key': 'reqv3-local-optout', 'use_model': False}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            response = self.client.post('/api/dataops/research-questions', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(model.calls, 0)
            self.assertNotIn('workspace', response.json())
            self.assertEqual(response.json()['proposal']['failure']['code'], 'requirements_unresolved')



    def test_known_rejection_replay_is_known(self):
        perobj = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                  'objects': [{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}],
                  'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                    'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]},
                                   {'id': 'r2', 'kind': 'read_fields', 'object_refs': ['o2'],
                                    'fields': [{'namespace': 'etcm', 'name': 'family'}]}],
                  'delivery': {'form': 'table', 'row_policy': 'keep_all'}, 'unresolved': []}
        model = RequirementsSequence(perobj, perobj)
        body = {'question': 'Alpha功效注意、Beta科名成表', 'request_key': 'reqv3-known-reject'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/api/dataops/research-questions', json=body)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['proposal']['failure']['code'], 'unsupported_delivery')
            again = self.client.post('/api/dataops/research-questions', json=body)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()['proposal']['failure']['code'], 'unsupported_delivery')
            self.assertEqual(model.calls, 2)



    def test_parent_append_preserves_fields_and_artifact(self):
        draft = table_draft()
        model = RequirementsSequence(draft, draft)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json={'query': '整理Alpha和Beta的功效和注意，生成表格',
                                                     'request_key': 'reqv3-parent-1'})
            self.assertEqual(first.status_code, 200, first.text)
            parent = self.wait(first.json()['assistant']['workspace_id'])
            self.assertEqual(parent['status'], 'complete')
            store = self.app.dataops_service.store()
            path = store.root / parent['id'] / parent['artifact']['filename']
            original = path.read_bytes()
            child_draft = {**table_draft(), 'operation': 'append_fields',
                           'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                                             'fields': [{'namespace': 'pharmacopoeia', 'name': '贮藏'}]}]}
            model2 = RequirementsSequence(child_draft, child_draft)
            with patch('tcm.dataops.research_model.configured_model', return_value=model2):
                response = self.client.post('/api/dataops/research-questions', json={
                    'question': '增加贮藏，保留旧字段', 'request_key': 'reqv3-child-1',
                    'parent_id': parent['id'], 'expected_revision': parent['revision']})
                self.assertIn(response.status_code, (200, 202), response.text)
                child = self.wait(response.json()['workspace']['id'])
                self.assertEqual(child['goal']['query'], parent['goal']['query'])
                self.assertEqual(sorted(child['goal']['document_fields']), ['功能与主治', '注意', '贮藏'])
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(model2.calls, 2)



    def test_parent_conflict_is_known_rejection(self):
        draft = table_draft()
        model = RequirementsSequence(draft, draft)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json={'query': '整理Alpha和Beta的功效和注意，生成表格',
                                                     'request_key': 'reqv3-pc-parent'})
            parent = self.wait(first.json()['assistant']['workspace_id'])
            self.assertEqual(parent['status'], 'complete')
            child_draft = {**table_draft(), 'operation': 'append_fields',
                           'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                           'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                             'fields': [{'namespace': 'pharmacopoeia', 'name': '贮藏'}]}]}
            model2 = RequirementsSequence(child_draft, child_draft)
            with patch('tcm.dataops.research_model.configured_model', return_value=model2):
                resp = self.client.post('/api/dataops/research-questions', json={
                    'question': '给Alpha增加贮藏，保留旧字段', 'request_key': 'reqv3-pc-child',
                    'parent_id': parent['id'], 'expected_revision': parent['revision']})
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertNotIn('workspace', resp.json())
                self.assertEqual(resp.json()['proposal']['failure']['code'], 'unsupported_delivery')
                self.assertEqual(model2.calls, 2)



    def test_answer_single_route_returns_query_proposal(self):
        ds = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
              'objects': [],
              'requirements': [{'id': 'r1', 'kind': 'dataset', 'datasets': ['etcm', 'pharmacopoeia']}],
              'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(ds, ds)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            response = self.client.post('/api/dataops/research-questions', json={
                'question': '介绍两个数据库', 'request_key': 'reqv3-answer-ds'})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body['proposal']['status'], 'query')
            self.assertEqual(body['proposal']['route']['request']['topic'], 'dataset_info')
            self.assertEqual(model.calls, 2)

    def test_answer_cross_source_carries_all_routes(self):
        cross = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                   'fields': [{'namespace': 'etcm', 'name': 'properties'},
                                              {'namespace': 'etcm', 'name': 'flavors'},
                                              {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(cross, cross)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            response = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha的性味和功效', 'request_key': 'reqv3-answer-cross'})
            self.assertEqual(response.status_code, 200, response.text)
            proposal = response.json()['proposal']
            self.assertEqual(proposal['status'], 'query')
            self.assertEqual(len(proposal['routes']), 3)
            self.assertEqual({r['request'].get('field') or r['request'].get('question') for r in proposal['routes']},
                             {'properties', 'flavors', 'Alpha的功能与主治'})



    def test_queue_busy_retry_reuses_paid_understanding(self):
        import hashlib
        owner = hashlib.sha256(self.client.cookies.get(self.app.SESSION_COOKIE_NAME).encode()).hexdigest()
        service = self.app.dataops_service
        queue = service.queue()
        blocker, _ = queue.enqueue(owner, 'hold queue', 'reqv3-busy-blocker')
        draft = table_draft()
        model = RequirementsSequence(draft, draft)
        body = {'query': '整理Alpha和Beta的功效和注意，生成表格', 'request_key': 'reqv3-busy'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 429, first.text)
            saved = service.store().by_request(owner, 'reqv3-busy')
            self.assertEqual(saved['goal_understanding']['provider_requests'], 2)
            second = self.client.post('/chat', json=body)
            self.assertEqual(second.status_code, 429, second.text)
            self.assertEqual(model.calls, 2)
            queue.cancel(owner, blocker['id'])
            retry = self.client.post('/chat', json=body)
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual(retry.json()['model_calls'], 0)
            doc = self.wait(saved['id'])
            self.assertEqual(doc['status'], 'complete')
            self.assertEqual(model.calls, 2)

    def test_unknown_dispatch_never_retries(self):
        draft = table_draft()
        model = RequirementsSequence(draft, TimeoutError('unknown'))
        body = {'query': '整理Alpha和Beta的功效和注意，生成表格', 'request_key': 'reqv3-unknown'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 503, first.text)
            second = self.client.post('/chat', json=body)
            self.assertEqual(second.status_code, 409, second.text)
            self.assertEqual(model.calls, 2)



    def test_ownership_isolation(self):
        draft = table_draft()
        model = RequirementsSequence(draft, draft)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json={'query': '整理Alpha和Beta的功效和注意，生成表格',
                                                     'request_key': 'reqv3-owner'})
            wid = first.json()['assistant']['workspace_id']
            self.wait(wid)
        other = TestClient(self.app.app)
        self.addCleanup(other.close)
        other.get('/dataops/research')
        response = other.get('/api/dataops/workspaces/' + wid)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(model.calls, 2)



    def test_unknown_dispatch_reservation_survives_store_reopen(self):
        import hashlib, os, sqlite3
        from pathlib import Path
        from tcm.request_journal import RequestJournal
        from fastapi import HTTPException
        owner = hashlib.sha256(self.client.cookies.get(self.app.SESSION_COOKIE_NAME).encode()).hexdigest()
        draft = table_draft()
        model = RequirementsSequence(draft, TimeoutError('unknown'))
        body = {'query': '整理Alpha和Beta的功效和注意，生成表格', 'request_key': 'reqv3-recover'}
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/chat', json=body)
            self.assertEqual(first.status_code, 503, first.text)
            path = Path(os.environ['DATAOPS_ROOT']) / 'question_proposals.sqlite3'
            with sqlite3.connect(path) as db:
                row = db.execute('SELECT signature FROM answers WHERE owner=? AND key=?',
                                 (owner, 'reqv3-recover')).fetchone()
            self.assertIsNotNone(row)
            fresh = RequestJournal(path, label='任务解析')
            with self.assertRaises(HTTPException):
                fresh.reserve(owner, 'reqv3-recover', row[0])
            self.assertEqual(model.calls, 2)


    def _cross_source(self, second_status):
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                     'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                                {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(draft, draft)
        primary = {'messages': [AIMessage(content='FIRST')], 'agent_name': 'fixture-herb',
                   'tcm_result': {'status': 'ok', 'message': '资料如下', 'records': [{
                       'source_record_id': 'fixture-herb',
                       'fields': {'medicinal_part': {'state': 'present', 'value': 'SYNTHETIC_LEAF'}},
                       'source': {'relative_file_path': 'fixture-herb.json'}}]}}
        secondary = {'messages': [AIMessage(content='SECOND')], 'agent_name': 'fixture-research',
                     'research_result': {'status': second_status, 'message': 'SECOND_RESULT',
                         'tool_result': {'hits': [{'name': 'Alpha', 'label': '功能与主治',
                             'text': 'SYNTHETIC_EFFICACY',
                             'evidence': [{'source': {'source_document': 'fixture.pdf', 'source_pdf_page': 7}}]}]}
                         if second_status == 'candidates' else None}}
        with patch('tcm.dataops.request_policy.needs_fallback', return_value=True), \
             patch('tcm.dataops.research_model.configured_model', return_value=model), \
             patch.object(self.app, 'process_query', side_effect=[primary, secondary]):
            response = self.client.post('/chat', json={
                'query': 'Alpha的药用部位和功效', 'request_key': 'reqv3-merge-' + second_status})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_cross_source_merges_both_results_and_provenance(self):
        body = self._cross_source('candidates')
        self.assertEqual(len(body['answer_steps']), 2)
        self.assertIn('SYNTHETIC_LEAF', json.dumps(body))
        self.assertIn('SYNTHETIC_EFFICACY', json.dumps(body))
        self.assertIn('fixture.pdf 第7页', body['response'])
        self.assertEqual(body['status'], 'success')
        self.assertEqual(body['unfinished'], [])

    def test_cross_source_second_failure_is_partial(self):
        body = self._cross_source('research_unavailable')
        self.assertEqual(body['status'], 'partial')
        self.assertIn('SYNTHETIC_LEAF', json.dumps(body))
        self.assertTrue(body.get('unfinished'))
        self.assertEqual(body['unfinished'][0]['tool'], 'research')



    def test_workbench_executes_saved_query_proposal(self):
        ds = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
              'objects': [],
              'requirements': [{'id': 'r1', 'kind': 'dataset', 'datasets': ['etcm', 'pharmacopoeia']}],
              'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(ds, ds)
        with patch('tcm.dataops.research_model.configured_model', return_value=model):
            first = self.client.post('/api/dataops/research-questions', json={
                'question': '介绍两个数据库', 'request_key': 'reqv3-wb-answer'})
            self.assertEqual(first.json()['proposal']['status'], 'query')
            executed = self.client.post('/api/dataops/answer', json={'request_key': 'reqv3-wb-answer'})
            self.assertEqual(executed.status_code, 200, executed.text)
            body = executed.json()
            self.assertEqual(body['status'], 'success')
            self.assertEqual(len(body['answer_steps']), 1)
            self.assertIn('ETCM', body['response'])
            self.assertEqual(model.calls, 2)



    def test_extra_route_does_not_persist_a_turn(self):
        from agents.agent_decision import process_query, memory
        session = self.client.cookies.get(self.app.SESSION_COOKIE_NAME)
        route = {'tool': 'assistant', 'request': {'topic': 'dataset_info', 'datasets': ['etcm']}}
        before = len(memory.load(session).messages)
        process_query('介绍两个数据库', session_id=session, resolved_request=route)
        after_first = len(memory.load(session).messages)
        process_query('介绍两个数据库', session_id=session, resolved_request=route, persist_turn=False)
        after_second = len(memory.load(session).messages)
        self.assertGreater(after_first, before)
        self.assertEqual(after_second, after_first)


    def test_etcm_answer_executes_against_synthetic_db(self):
        import os
        from pathlib import Path
        root = Path(os.environ['DATAOPS_ROOT']).parent
        herbs = root / 'sources' / 'herbs.sqlite3'
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                   'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(draft, draft)
        env = {'TCM_DB': str(herbs), 'TCM_DATASET_VERSION': 'fixture-v1'}
        with patch.dict('os.environ', env), \
             patch('tcm.dataops.research_model.configured_model', return_value=model):
            created = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha的药用部位', 'request_key': 'reqv3-etcm-e2e'})
            self.assertEqual(created.json()['proposal']['status'], 'query')
            executed = self.client.post('/api/dataops/answer', json={'request_key': 'reqv3-etcm-e2e'})
            self.assertEqual(executed.status_code, 200, executed.text)
            body = executed.json()
            self.assertEqual(body['status'], 'success')
            self.assertIn('Leaf', body['response'])
            self.assertIn('etcm_herbs/Alpha.json', json.dumps(body))



    def _pharma_fixture(self):
        import hashlib, json, os
        from pathlib import Path
        from scripts.build_mineru_retrieval import build
        from tcm.mineru_pharmacopoeia import digest
        root = Path(os.environ['DATAOPS_ROOT']).parent / 'pharma'
        root.mkdir(exist_ok=True)
        pdf = root / 'pharma.pdf'
        import fitz
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 180), 'Alpha TEST_EFFICACY')
        document.save(pdf)
        document.close()
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        view = root / 'view'
        if not view.is_dir():
            view.mkdir()
            evidence = [{'block_id': 'b1', 'chapter_page': 1,
                         'source': {'source_document': 'high', 'source_pdf_page': 1, 'source_sha256': sha},
                         'bbox_0_1000': [100, 200, 400, 230]}]
            entry = {'id': 'e1', 'name_zh_candidate': 'Alpha', 'raw_name_zh_candidate': 'Alpha',
                     'fields': [{'scope': 'monograph', 'label': '功能与主治', 'text': 'TEST_EFFICACY',
                                 'raw_text': 'TEST_EFFICACY', 'overlay_ids': [], 'evidence': evidence}],
                     'human_signoff': False, 'production_eligible': False, 'raw_flags': [],
                     'applied_overlay_ids': [], 'review_status': 'pending_human_review', 'title_evidence': evidence}
            (view / 'research_view.json').write_text(json.dumps(
                {'human_signoff': False, 'production_eligible': False, 'entries': [entry]}, ensure_ascii=False))
            (view / 'revision_ledger.json').write_text('{}')
            (view / 'summary.json').write_text(json.dumps(
                {'research_view_sha256': digest(view / 'research_view.json'),
                 'ledger_sha256': digest(view / 'revision_ledger.json')}))
        index = root / 'index'
        if not index.is_dir():
            build(view, index)
        return index, pdf

    def test_pharmacopoeia_answer_executes_against_synthetic_index(self):
        index, pdf = self._pharma_fixture()
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                   'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(draft, draft)
        env = {'PHARMACOPOEIA_INDEX': str(index), 'PHARMACOPOEIA_PDF_HIGH': str(pdf),
               'PHARMACOPOEIA_PDF_FULL': str(pdf), 'ENABLE_PHARMACOPOEIA_RESEARCH': '1'}
        with patch.dict('os.environ', env), \
             patch('tcm.dataops.research_model.configured_model', return_value=model):
            created = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha的功能与主治', 'request_key': 'reqv3-pharm-e2e'})
            self.assertEqual(created.json()['proposal']['status'], 'query')
            executed = self.client.post('/api/dataops/answer', json={'request_key': 'reqv3-pharm-e2e'})
            self.assertEqual(executed.status_code, 200, executed.text)
            body = executed.json()
            self.assertEqual(body['status'], 'success')
            self.assertIn('TEST_EFFICACY', json.dumps(body))



    def test_cross_source_etcm_and_pharmacopoeia_end_to_end(self):
        import os
        from pathlib import Path
        herbs = Path(os.environ['DATAOPS_ROOT']).parent / 'sources' / 'herbs.sqlite3'
        index, pdf = self._pharma_fixture()
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                   'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                                              {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(draft, draft)
        env = {'TCM_DB': str(herbs), 'TCM_DATASET_VERSION': 'fixture-v1',
               'PHARMACOPOEIA_INDEX': str(index), 'PHARMACOPOEIA_PDF_HIGH': str(pdf),
               'PHARMACOPOEIA_PDF_FULL': str(pdf), 'ENABLE_PHARMACOPOEIA_RESEARCH': '1'}
        with patch.dict('os.environ', env), \
             patch('tcm.dataops.research_model.configured_model', return_value=model):
            created = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha的药用部位和功效', 'request_key': 'reqv3-cross-e2e'})
            self.assertEqual(created.json()['proposal']['status'], 'query')
            self.assertEqual(len(created.json()['proposal']['routes']), 2)
            executed = self.client.post('/api/dataops/answer', json={'request_key': 'reqv3-cross-e2e'})
            self.assertEqual(executed.status_code, 200, executed.text)
            body = executed.json()
            self.assertEqual(body['status'], 'success')
            self.assertEqual(len(body['answer_steps']), 2)
            self.assertIn('Leaf', body['response'])
            self.assertIn('TEST_EFFICACY', body['response'])
            self.assertIn('etcm_herbs/Alpha.json', json.dumps(body))



    def test_same_type_multi_route_keeps_both_objects(self):
        import os
        from pathlib import Path
        herbs = Path(os.environ['DATAOPS_ROOT']).parent / 'sources' / 'herbs.sqlite3'
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                                   'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(draft, draft)
        env = {'TCM_DB': str(herbs), 'TCM_DATASET_VERSION': 'fixture-v1'}
        with patch.dict('os.environ', env), \
             patch('tcm.dataops.research_model.configured_model', return_value=model):
            created = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha和Beta的药用部位', 'request_key': 'reqv3-same-e2e'})
            self.assertEqual(len(created.json()['proposal']['routes']), 2)
            executed = self.client.post('/api/dataops/answer', json={'request_key': 'reqv3-same-e2e'})
            self.assertEqual(executed.status_code, 200, executed.text)
            body = executed.json()
            self.assertEqual(body['status'], 'success')
            self.assertEqual(len(body['answer_steps']), 2)
            self.assertIn('Leaf', body['response'])
            self.assertIn('Root', body['response'])



    def test_answer_rejects_source_revision_change(self):
        import os
        from pathlib import Path
        herbs = Path(os.environ['DATAOPS_ROOT']).parent / 'sources' / 'herbs.sqlite3'
        draft = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                 'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                   'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
                 'delivery': {'form': 'answer'}, 'unresolved': []}
        model = RequirementsSequence(draft, draft)
        env = {'TCM_DB': str(herbs), 'TCM_DATASET_VERSION': 'fixture-v1'}
        with patch.dict('os.environ', env), \
             patch('tcm.dataops.research_model.configured_model', return_value=model):
            created = self.client.post('/api/dataops/research-questions', json={
                'question': 'Alpha的药用部位', 'request_key': 'reqv3-src-change'})
            self.assertEqual(created.json()['proposal']['status'], 'query')
            herbs.write_bytes(herbs.read_bytes() + b'\x00')
            executed = self.client.post('/api/dataops/answer', json={'request_key': 'reqv3-src-change'})
            self.assertEqual(executed.status_code, 409, executed.text)


if __name__ == '__main__':
    unittest.main()
