"""Bounded question contract and HTTP delivery; all data and decisions synthetic."""
from copy import deepcopy
import json
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
from tcm.dataops.goal_proposal import propose


class QuestionCompilerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.records, factory = make_sources(Path(tmp.name))
        with factory() as sources:
            self.catalog = sources.name_catalog()

    def test_names_fields_and_provenance(self):
        for question in ('整理Alpha和Gamma的功效、注意事项，保留出处并生成表格',
                         '请帮我核对甲草与丙草的功能与主治和注意，附上原始来源',
                         'alpha，Gamma，功效，注意', '核对Alpha和Gamma的功效和注意，生成核对表'):
            with self.subTest(question=question):
                plan = propose(question, self.catalog)
                self.assertEqual(plan['status'], 'ready', plan)
                self.assertEqual(set(plan['goal']['document_fields']), {'功能与主治', '注意'})
                self.assertEqual(len(plan['goal']['query']['names']), 2)
                self.assertEqual(plan['question'], question)
                self.assertEqual(plan['external_model_calls'], 0)

    def test_quantity_filters_defaults_and_strict_completion(self):
        for quantity in ('20', '二十'):
            plan = propose('整理药用部位包含Root的前' + quantity + '条记录，要求全部字段补齐', self.catalog)
            self.assertEqual(plan['status'], 'ready', plan)
            self.assertEqual(plan['goal']['query']['limit'], 20)
            self.assertEqual(plan['goal']['query']['filters'], [{'field': 'medicinal_part', 'op': 'contains', 'value': 'Root'}])
            self.assertEqual(plan['goal']['completion_policy'], 'require_all_fields')
            self.assertTrue(plan['defaults'])
        self.assertEqual(propose('药用部位缺失的前2条，整理功效', self.catalog)['status'], 'ready')
        defaults = propose('药用部位缺失，整理功效', self.catalog)
        self.assertEqual(defaults['status'], 'ready', defaults)
        self.assertTrue(any('前20条' in text for text in defaults['defaults']))

    def test_unrecognized_or_negative_requirements_never_execute_partially(self):
        for question in ('整理Alpha功效并预测靶点', '整理Alpha功效，不要贮藏',
                         '整理Alpha功效并删除数据库', '整理Alpha功效并翻译成英文',
                         '整理Alpha功效和Unknown的注意', '整理Alpha功效的所有药材',
                         '整理Alpha功效并调用联网搜索', '整理Alpha功效<script>alert(1)</script>'):
            with self.subTest(question=question):
                plan = propose(question, self.catalog)
                self.assertEqual(plan['status'], 'needs_input', plan)
                self.assertIsNone(plan['goal'])
                self.assertTrue(plan['unresolved'])
                self.assertEqual(plan['question'], question)

    def test_limits_missing_objects_and_excess_fields(self):
        for question in ('整理前51条药材的功效', '整理前0条记录的功效', '整理前二百条的功效',
                         '增加贮藏', '整理Alphabeta的功效', '整理Alpha的功效、注意、贮藏、炮制、用量',
                         '整理前2条和前3条的功效'):
            with self.subTest(question=question):
                plan = propose(question, self.catalog)
                self.assertEqual(plan['status'], 'needs_input', plan)
                self.assertIsNone(plan['goal'])

    def test_parent_scope_add_replace_and_conflict_refresh(self):
        sid = self.records['Alpha']['source_record_id']
        parent = {'id': 'a'*32, 'revision': 1, 'status': 'complete', 'artifact': {'filename': 'v1.xlsx'},
                  'goal': {'document_fields': ['功能与主治', '注意'], 'attributes': ['medicinal_part', 'latin_name'],
                           'completion_policy': 'audit'},
                  'result': {'rows': [{'source_id': sid, 'mapping_status': 'medicinal_part_difference'}]}}
        original = deepcopy(parent)
        plan = propose('再增加一列贮藏，保留旧字段', self.catalog, parent=parent)
        self.assertEqual(plan['status'], 'ready', plan)
        self.assertEqual(plan['goal']['document_fields'], ['功能与主治', '注意', '贮藏'])
        self.assertEqual(plan['goal']['query'], {'source_ids': [sid], 'names': [], 'filters': [],
                         'group_by': None, 'limit': 50, 'offset': 0})
        plan = propose('只保留贮藏，同时重查有问题的记录', self.catalog, parent=parent)
        self.assertEqual(plan['status'], 'ready', plan)
        self.assertEqual(plan['goal']['document_fields'], ['贮藏'])
        self.assertEqual(plan['refresh_source_ids'], [sid])
        self.assertEqual(parent, original)
        parent['result']['rows'][0]['mapping_status'] = 'fields_consistent_not_verified'
        parent['result']['issues'] = [{'source_id': sid, 'field': '注意', 'kind': 'missing'}]
        self.assertEqual(propose('重查有问题的记录', self.catalog, parent=parent)['refresh_source_ids'], [sid])
        for question in ('增加贮藏并筛选前2条', '为Gamma增加贮藏', '把注意改为贮藏', '继续'):
            self.assertEqual(propose(question, self.catalog, parent=parent)['status'], 'needs_input')

    def test_aliases_do_not_approve_identity_or_choose_between_candidates(self):
        plan = propose('整理丙草的功效', self.catalog)
        self.assertEqual(plan['status'], 'ready', plan)
        self.assertNotIn('candidate_id', json.dumps(plan))
        self.assertNotIn('identity_verified', json.dumps(plan))


class QuestionApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.records, factory = make_sources(self.root / 'fixture')
        self.memory = ConversationStore(self.root / 'chat.sqlite3')
        for context in (patch.dict('os.environ', {'ENABLE_DATAOPS': '1', 'ENABLE_DATAOPS_AGENT': '1',
                'DATAOPS_DRIVER': 'synthetic', 'DATAOPS_ROOT': str(self.root / 'work'),
                'RESEARCH_JOB_DB': str(self.root / 'jobs.sqlite3'), 'RESEARCH_JOB_DSN': '', 'RESEARCH_CHECKPOINT_DSN': ''}),
                patch('tcm.dataops.api.configured_sources', factory)):
            context.start(); self.addCleanup(context.stop)
        app = FastAPI(); app.include_router(create_router(lambda: self.memory, lambda: 'cookie'))
        self.client = self.enter(TestClient(app)); self.client.get('/dataops')

    def enter(self, client):
        client.__enter__(); self.addCleanup(client.__exit__, None, None, None)
        return client

    def submit(self, question, key, **extra):
        response = self.client.post('/api/dataops/task-requests', json={
            'question': question, 'request_key': key, **extra})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def wait(self, identifier):
        for _ in range(250):
            response = self.client.get('/api/dataops/agent-runs/' + identifier)
            self.assertEqual(response.status_code, 200, response.text)
            doc = response.json()
            if doc['job']['status'] not in ('queued', 'running'):
                return doc
            time.sleep(.02)
        self.fail('job did not finish')

    def test_question_to_delivery_then_incremental_revision_preserves_v1(self):
        question = '整理Alpha的功效、注意，保留出处并生成表格'
        first = self.submit(question, 'question-first-1')
        parent = self.wait(first['workspace']['id'])['workspace']
        self.assertEqual(parent['status'], 'complete', parent)
        self.assertEqual(parent['question'], question)
        path = '/api/dataops/workspaces/' + parent['id'] + '/artifact'
        before = self.client.get(path).content
        second = self.submit('增加贮藏，保留旧字段', 'question-second-2',
            parent_id=parent['id'], expected_revision=parent['revision'])
        child = self.wait(second['workspace']['id'])['workspace']
        self.assertEqual((child['status'], child['revision']), ('complete', 2), child)
        self.assertEqual(child['progress']['reused_cells'], 4)
        self.assertEqual(child['progress']['leaf_reads'], 2)
        self.assertTrue(child['result']['diff'])
        self.assertEqual(self.client.get(path).content, before)
        repeated = self.submit('增加贮藏，保留旧字段', 'question-second-2',
            parent_id=parent['id'], expected_revision=parent['revision'])
        self.assertEqual(repeated['workspace']['id'], child['id'])

    def test_unknown_requirement_returns_original_question_without_enqueue(self):
        question = '整理Alpha功效并推荐配方'
        response = self.client.post('/api/dataops/task-requests', json={'question': question, 'request_key': 'not-enqueued'})
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()['detail']
        self.assertEqual(detail['question'], question)
        self.assertTrue(detail['unresolved'])
        self.assertIsNone(detail['goal'])
        self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'], [])
        self.assertEqual(self.memory.path.exists(), True)

    def test_candidate_ambiguity_uses_existing_human_wait_not_goal_confirmation(self):
        first = self.submit('整理Gamma的功效，保留出处', 'ambiguous-question')
        identifier = first['workspace']['id']; doc = self.wait(identifier)
        self.assertEqual(doc['job']['status'], 'waiting_input', doc)
        pending = doc['workspace']['pending']
        response = self.client.post(f'/api/dataops/agent-runs/{identifier}/answer', json={
            'job_revision': doc['job']['revision'], 'question_id': pending['question_id'], 'revision': 1,
            'operation': 'choose', 'candidate_id': 'entry-2'})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.wait(identifier)['workspace']['status'], 'complete')

    def test_proposal_is_read_only_and_submission_contract_and_owner_are_checked(self):
        plan = self.client.post('/api/dataops/proposals', json={'question': '整理Alpha功效'})
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual(plan.json()['status'], 'ready')
        self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'], [])
        body = {'question': '整理Alpha功效', 'request_key': 'guard-question'}
        self.assertEqual(self.client.post('/api/dataops/task-requests', json={**body, 'goal': {}}).status_code, 422)
        self.assertEqual(self.client.post('/api/dataops/task-requests', json=body,
            headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        parent = self.wait(self.submit(body['question'], body['request_key'])['workspace']['id'])['workspace']
        followup = {'question': '增加贮藏', 'parent_id': parent['id'], 'expected_revision': 99}
        self.assertEqual(self.client.post('/api/dataops/proposals', json=followup).status_code, 409)
        other = TestClient(self.client.app); self.addCleanup(other.close); other.get('/dataops')
        followup['expected_revision'] = parent['revision']
        self.assertEqual(other.post('/api/dataops/proposals', json=followup).status_code, 404)
        with patch.dict('os.environ', {'DATAOPS_DRIVER': 'deepseek'}):
            self.assertEqual(self.client.post('/api/dataops/task-requests', json=body).status_code, 409)

    def test_restricted_main_app_accepts_only_exact_question_routes(self):
        import app
        with patch.object(app.agent_runtime, 'memory', self.memory), patch.dict('os.environ', {'RESEARCH_TOOLS_ONLY': '1'}):
            client = self.enter(TestClient(app.app)); client.get('/dataops')
            response = client.post('/api/dataops/proposals', json={'question': '整理Alpha功效'})
            self.assertEqual(response.status_code, 200, response.text)
            response = client.post('/api/dataops/task-requests', json={
                'question': '整理Alpha功效', 'request_key': 'main-app-question'})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(client.post('/api/dataops/task-requests/not-allowed', json={}).status_code, 403)


if __name__ == '__main__': unittest.main()
