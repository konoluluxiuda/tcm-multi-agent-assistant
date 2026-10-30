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


def seed_snapshot(case, *, session='A', api=None, query='frozen-fixture'):
    """Load an explicit historical fixture; never exercise the retired search route."""
    from tcm.literature_store import LiteratureStore
    from tcm.agent_pubmed import _hash
    if api is not None:
        import app
        api.get('/conversation')
        session = api.cookies.get(app.SESSION_COOKIE_NAME)
    store = LiteratureStore(case.root / 'literature.sqlite3')
    try:
        run_id = store.save(case.provider.search(query))
        data = store.get(run_id)
    finally:
        store.close()
    context = {'kind': 'pubmed_run_v1', 'run_id': run_id, 'snapshot_sha256': _hash(data)}
    current = case.memory.load(session)
    case.memory.save(session, current.messages, pack_contexts(None, None, context), revision=current.revision)
    return dict(data, run_id=run_id), context


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

    def test_retired_search_and_invalid_command_never_open_ledger(self):
        with patch('tcm.agent_pubmed.LiteratureStore', side_effect=AssertionError('opened ledger')):
            for query in ('/pubmed x', '/pubmed'):
                result = self.turn(query)
                self.assertEqual(result['pubmed_result']['status'], 'pubmed_retired')
        self.assertFalse((self.root/'literature.sqlite3').exists())

    def test_replay_survives_reopening_and_never_searches(self):
        from agents.conversation_store import ConversationStore
        seed_snapshot(self)
        before = (self.root/'literature.sqlite3').read_bytes()
        with patch.object(self.ad, 'memory', ConversationStore(self.memory.path)):
            result = self.turn('/pubmed-last')['pubmed_result']
        self.assertTrue(result['tool_result']['replayed'])
        self.assertEqual(result['tool_result']['articles'][0]['pmid'], '123')
        self.assertEqual((self.root/'literature.sqlite3').read_bytes(), before)

    def test_session_isolation_clear_and_task_switch(self):
        seed_snapshot(self)
        self.assertEqual(self.turn('/pubmed-last', 'B')['pubmed_result']['status'], 'no_active_run')
        self.assertEqual(self.turn('/pubmed-clear')['pubmed_result']['status'], 'context_cleared')
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')
        seed_snapshot(self)
        self.turn('/research-clear')
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')

    def test_graph_never_puts_old_abstract_into_memory(self):
        data, _ = seed_snapshot(self)
        abstract = data['articles'][0]['abstract'][0]['text']
        result = self.turn('/pubmed-last')
        self.assertEqual(result['pubmed_result']['status'], 'ok')
        self.assertNotIn(abstract, str(self.memory.load('A')))
        self.assertNotIn('articles', str(self.memory.load('A').tcm_context))

    def test_missing_and_changed_history_remain_explicit(self):
        import sqlite3
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'no_active_run')
        seed_snapshot(self)
        with sqlite3.connect(self.root/'literature.sqlite3') as db:
            db.execute("UPDATE search_runs SET payload='{}'")
        self.assertEqual(self.turn('/pubmed-last')['pubmed_result']['status'], 'snapshot_unavailable')

    def test_api_retires_search_but_can_replay_export_and_clear_history(self):
        from fastapi.testclient import TestClient
        import app
        with TestClient(app.app) as api, patch.dict(os.environ, {'RESEARCH_TOOLS_ONLY': '1'}):
            for flag in ('0', '1'):
                with patch.dict(os.environ, {'ENABLE_PUBMED': flag}):
                    result = api.post('/chat', json={'query': '/pubmed x'})
                    self.assertEqual(result.status_code, 410)
                    self.assertEqual(result.json()['pubmed']['status'], 'pubmed_retired')
            seed_snapshot(self, api=api)
            self.assertTrue(api.get('/conversation').json()['pubmed']['tool_result']['replayed'])
            html = api.get('/').text
            self.assertNotIn('workspace-tab-literature', html)
            self.assertNotIn('data-workspace-literature-form', html)
            api.delete('/conversation')
            self.assertNotIn('pubmed', api.get('/conversation').json())


if __name__ == '__main__': unittest.main()
