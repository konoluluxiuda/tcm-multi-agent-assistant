"""PubMed graph/API contracts with real persistence and no provider invocations."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcm.agent_pubmed import PubMedRuntime, parse_request, context_from
from tcm.agent_research import pack_contexts, split_contexts
from tests.test_pubmed import client as fixture_client


class ParserTests(unittest.TestCase):
    def test_explicit_commands_and_boundaries(self):
        self.assertIsNone(parse_request('黄芪现代研究'))
        self.assertIsNone(parse_request('/pubmed-last extra'))
        self.assertIsNone(parse_request({'image': 'private', 'text': '/pubmed x'}))
        self.assertEqual(parse_request('/pubmed-last'), {'action': 'last'})
        self.assertEqual(parse_request('/pubmed-clear'), {'action': 'clear'})
        for query in ('/pubmed', '/pubmed ' + 'x' * 2001):
            self.assertEqual(parse_request(query)['action'], 'invalid')

    def test_legacy_context_contract(self):
        legacy = {'status': 'candidate'}
        self.assertEqual(split_contexts(legacy), (legacy, None))
        self.assertIsNone(context_from(legacy))
        packed = pack_contexts(None, None, {'kind': 'pubmed_run_v1'})
        self.assertEqual(split_contexts(packed), (None, None))
        self.assertEqual(context_from(packed), {'kind': 'pubmed_run_v1'})


class PubMedGraphTests(unittest.TestCase):
    def setUp(self):
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from langchain_core.runnables import RunnableLambda
        self.ad = ad
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = ConversationStore(self.root / 'memory.sqlite3')
        def forbidden(*args, **kwargs):
            raise AssertionError('Explicit PubMed invoked a model')
        self.provider = fixture_client()
        self.search_patch = patch('tcm.agent_pubmed.PubMedClient', return_value=self.provider)
        patches = [patch.dict(os.environ, {'ENABLE_PUBMED': '1', 'PUBMED_DB': str(self.root / 'literature.sqlite3')}),
                   patch.object(ad, 'memory', self.memory), self.search_patch,
                   patch.object(ad.config.agent_decision, 'llm', RunnableLambda(forbidden)),
                   patch.object(ad.config.conversation, 'llm', RunnableLambda(forbidden)),
                   patch.object(ad.LocalGuardrails, 'check_input', forbidden),
                   patch.object(ad.LocalGuardrails, 'check_output', forbidden)]
        for p in patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

    def turn(self, query, session='A'):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ad.process_query(query, session_id=session)

    def test_graph_no_raw_abstract_in_messages_or_memory(self):
        result = self.turn('/pubmed Astragalus[Title/Abstract]')
        self.assertEqual(result['agent_name'], 'PUBMED_AGENT')
        self.assertEqual(result['pubmed_result']['status'], 'ok')
        abstract = result['pubmed_result']['tool_result']['articles'][0]['abstract'][0]['text']
        snapshot = self.memory.load('A')
        self.assertNotIn(abstract, str(snapshot))
        self.assertNotIn('articles', str(snapshot.tcm_context))
        self.assertIsNone(result['tcm_context']); self.assertIsNone(result['research_context'])

    def test_replay_survives_reopening_and_never_searches(self):
        from agents.conversation_store import ConversationStore
        self.turn('/pubmed x')
        with patch.object(self.ad, 'memory', ConversationStore(self.memory.path)), \
             patch('tcm.agent_pubmed.PubMedClient', side_effect=AssertionError('Replay must stay offline')):
            replay = self.turn('/pubmed-last')['pubmed_result']
        self.assertTrue(replay['tool_result']['replayed'])
        self.assertEqual(replay['tool_result']['articles'][0]['pmid'], '123')

    def test_session_isolation_clear_and_task_switch(self):
        self.turn('/pubmed x')
        self.assertEqual(self.turn('/pubmed-last', 'B')['pubmed_result']['status'], 'no_active_run')
        self.assertEqual(self.turn('/pubmed-clear')['pubmed_result']['status'], 'context_cleared')
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')
        self.turn('/pubmed x')
        self.turn('/research-clear')
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')
        self.turn('/pubmed x')
        self.turn('/herb-clear')
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')

    def test_ordinary_turn_clears_focus_without_source_in_model_prompt(self):
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda
        result = self.turn('/pubmed x')
        abstract = result['pubmed_result']['tool_result']['articles'][0]['abstract'][0]['text']
        prompts = []
        class Guard:
            def __init__(self, *_): pass
            def check_input(self, text): return True, text
            def check_output(self, text, *args, **kwargs): return text
        def converse(prompt):
            prompts.append(str(prompt)); return AIMessage(content='fixture ordinary reply')
        with patch.object(self.ad, 'LocalGuardrails', Guard), \
             patch.object(self.ad.config.agent_decision, 'llm', RunnableLambda(lambda _: AIMessage(content='{"agent":"CONVERSATION_AGENT","reasoning":"fixture","confidence":1}'))), \
             patch.object(self.ad.config.conversation, 'llm', RunnableLambda(converse)):
            ordinary = self.turn('hello')
        self.assertIsNone(ordinary['pubmed_context']); self.assertTrue(prompts)
        self.assertNotIn(abstract, str(prompts))
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')

    def test_disabled_and_invalid_commands_never_open_database(self):
        with patch('tcm.agent_pubmed.LiteratureStore', side_effect=AssertionError('Opened store')), \
             patch.dict(os.environ, {'ENABLE_PUBMED': '0'}):
            self.assertEqual(self.turn('/pubmed x')['pubmed_result']['status'], 'pubmed_disabled')
            self.assertEqual(self.turn('/pubmed')['pubmed_result']['status'], 'invalid_query')

    def test_changed_snapshot_or_missing_database_does_not_research(self):
        import sqlite3
        self.turn('/pubmed x')
        with sqlite3.connect(self.root / 'literature.sqlite3') as db:
            db.execute("UPDATE search_runs SET payload='{}'")
        with patch('tcm.agent_pubmed.PubMedClient', side_effect=AssertionError('Replay called provider')):
            self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'snapshot_unavailable')

    def test_busy_and_storage_error_are_safe(self):
        runtime = PubMedRuntime()
        runtime.slot.acquire()
        try:
            self.assertEqual(runtime.execute({'action': 'search', 'query': 'x'}, None)[0]['status'], 'pubmed_busy')
        finally:
            runtime.slot.release()
        with patch('tcm.agent_pubmed.LiteratureStore', side_effect=OSError('/private/secret')):
            result = self.turn('/pubmed x')['pubmed_result']
            self.assertEqual(result['status'], 'pubmed_unavailable'); self.assertNotIn('/private', str(result))

    def test_api_errors_cards_restore_and_delete(self):
        from fastapi.testclient import TestClient
        import app
        with TestClient(app.app) as api, patch.dict(os.environ, {'RESEARCH_TOOLS_ONLY': '1'}):
            self.assertEqual(api.post('/chat', json={'query': '/pubmed'}).status_code, 400)
            result = api.post('/chat', json={'query': '/pubmed x'})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['pubmed']['status'], 'ok')
            restored = api.get('/conversation').json()
            self.assertTrue(restored['pubmed']['tool_result']['replayed'])
            self.assertNotIn('research', restored); self.assertNotIn('tcm', restored)
            self.assertIn('/pubmed Astragalus', api.get('/').text)
            api.delete('/conversation')
            self.assertNotIn('pubmed', api.get('/conversation').json())
            self.assertEqual(api.post('/chat', json={'query': '/pubmed-last'}).json()['pubmed']['status'], 'no_active_run')
            with patch.dict(os.environ, {'ENABLE_PUBMED': '0'}):
                self.assertEqual(api.post('/chat', json={'query': '/pubmed x'}).status_code, 503)

    def test_api_upstream_failure_and_partial_are_distinct(self):
        from fastapi.testclient import TestClient
        import app
        with TestClient(app.app) as api:
            with patch('tcm.agent_pubmed.PubMedClient', return_value=fixture_client(ids=['123', '456'])):
                result = api.post('/chat', json={'query': '/pubmed partial'})
                self.assertEqual(result.json()['status'], 'partial')
                self.assertEqual(result.json()['pubmed']['status'], 'partial_fetch')
            with patch('tcm.agent_pubmed.PubMedClient', return_value=fixture_client(xml='<bad')):
                result = api.post('/chat', json={'query': '/pubmed failed'})
                self.assertEqual(result.status_code, 502)
                self.assertEqual(result.json()['pubmed']['status'], 'error')


if __name__ == '__main__':
    unittest.main()
