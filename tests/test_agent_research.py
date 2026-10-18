"""Real LangGraph/API research branch with every provider invocation forbidden."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcm.agent_research import parse_request, ResearchRuntime, split_contexts
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture
from scripts.build_mineru_retrieval import build


class ResearchParserTests(unittest.TestCase):
    def test_command_owns_errors_and_does_not_intercept_images_or_ordinary_chat(self):
        self.assertIsNone(parse_request('甘草的功效'))
        self.assertIsNone(parse_request({'image': 'fixture', 'text': '/research 甘草的功效'}))
        self.assertEqual(parse_request('/research')['error'], 'invalid_query')
        self.assertEqual(parse_request('/research ' + 'x' * 501)['error'], 'invalid_query')
        self.assertEqual(parse_request('/research-clear'), {'clear': True})
        self.assertEqual(parse_request('/research 它的用量')['question'], '它的用量')

    def test_lazy_runtime_is_disabled_without_loading_sources(self):
        runtime = ResearchRuntime()
        with patch.dict(os.environ, {'ENABLE_PHARMACOPOEIA_RESEARCH': '0'}):
            result, selected = runtime.execute({'question': '甘草的功效'}, None)
        self.assertEqual(result['status'], 'research_disabled')
        self.assertIsNone(runtime.service); self.assertIsNone(selected)


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires main application CI dependencies')
class ResearchGraphTests(unittest.TestCase):
    def setUp(self):
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from langchain_core.runnables import RunnableLambda
        self.ad = ad
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root / 'view'); build(self.root / 'view', self.root / 'index')
        self.service = Service(self.root / 'index')
        self.memory = ConversationStore(self.root / 'memory.sqlite3')
        self.calls = []
        def forbidden(*args, **kwargs):
            self.calls.append('provider'); raise AssertionError('Research branch called model')
        patches = [patch.object(ad, 'memory', self.memory),
                   patch.object(ad.research_runtime, 'get_service', return_value=self.service),
                   patch.object(ad.config.agent_decision, 'llm', RunnableLambda(forbidden)),
                   patch.object(ad.config.conversation, 'llm', RunnableLambda(forbidden)),
                   patch.object(ad.LocalGuardrails, 'check_input', forbidden),
                   patch.object(ad.LocalGuardrails, 'check_output', forbidden)]
        for p in patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

    def turn(self, query, session='A'):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ad.process_query(query, session_id=session)

    def test_research_node_returns_source_evidence_without_models_or_raw_history(self):
        result = self.turn('/research 示例叶炭的功效')
        self.assertEqual(result['agent_name'], 'PHARMACOPOEIA_RESEARCH_AGENT')
        body = result['research_result']['tool_result']['hits'][0]['text']
        self.assertNotIn(body, str(result['messages'])); self.assertEqual(self.calls, [])
        self.assertEqual(result['research_result']['status'], 'candidates')

    def test_followup_and_source_use_research_slot_only(self):
        self.turn('/research 示例叶炭的功效')
        result = self.turn('/research 它的用量')
        self.assertEqual(result['research_result']['resolved_question'], '示例叶炭的用量')
        self.assertEqual(self.turn('/research 出处呢')['research_result']['resolved_question'], '示例叶炭的用量')
        self.assertEqual(self.turn('/research 它的用量', 'B')['research_result']['status'], 'clarify_entity')

    def test_restart_recovers_focus_from_existing_main_memory_store(self):
        from agents.conversation_store import ConversationStore
        self.turn('/research 示例叶炭的功效')
        with patch.object(self.ad, 'memory', ConversationStore(self.memory.path)):
            self.assertEqual(self.turn('/research 它的用量')['research_result']['status'], 'candidates')

    def test_herb_tool_clears_research_focus_and_legacy_context_remains_compatible(self):
        self.turn('/research 示例叶炭的功效')
        with patch.object(self.ad, 'execute_turn', return_value=({'message': 'fixture', 'status': 'not_found'}, None)):
            result = self.turn('/herb fixture')
        self.assertIsNone(result['research_context'])
        self.assertEqual(self.turn('/research 它的用量')['research_result']['status'], 'clarify_entity')
        legacy = {'status': 'ambiguous_entity'}
        self.assertEqual(split_contexts(legacy), (legacy, None))

    def test_failed_scope_clear_and_invalid_command_never_call_model(self):
        self.turn('/research 示例叶炭的功效')
        result = self.turn('/research 酒示例叶的用量')
        self.assertEqual(result['research_result']['status'], 'variant_scope_unresolved')
        self.assertIsNone(result['research_context'])
        self.assertEqual(self.turn('/research')['research_result']['status'], 'invalid_query')
        self.assertEqual(self.turn('/research-clear')['research_result']['status'], 'context_cleared')
        self.assertEqual(self.calls, [])

    def test_unavailable_tool_does_not_fall_back_to_model(self):
        with patch.object(self.ad.research_runtime, 'get_service', side_effect=FileNotFoundError('private path')):
            result = self.turn('/research 示例叶炭的功效')['research_result']
        self.assertEqual(result['status'], 'research_unavailable')
        self.assertNotIn('private path', str(result)); self.assertEqual(self.calls, [])

    def test_ordinary_turn_clears_focus_without_sending_source_body_to_model(self):
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda
        result = self.turn('/research 示例叶炭的功效')
        body = result['research_result']['tool_result']['hits'][0]['text']
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
        self.assertIsNone(ordinary['research_context']); self.assertTrue(prompts)
        self.assertNotIn(body, str(prompts))
        self.assertEqual(self.turn('/research 它的用量')['research_result']['status'], 'clarify_entity')

    def test_herb_pronoun_cannot_read_research_focus(self):
        self.turn('/research 示例叶炭的功效')
        result = self.turn('它的药用部位是什么？')
        self.assertEqual(result['tcm_result']['status'], 'clarify_entity')
        self.assertIsNone(result['research_context']); self.assertEqual(self.calls, [])

    def test_api_cookie_restore_delete_and_disable_status(self):
        from fastapi.testclient import TestClient
        import app
        with TestClient(app.app) as client:
            response = client.post('/chat', json={'query': '/research 示例叶炭的功效'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['agent'], 'PHARMACOPOEIA_RESEARCH_AGENT')
            restored = client.get('/conversation').json()
            self.assertEqual(restored['research']['status'], 'candidates')
            self.assertNotIn('tcm', restored)
            self.assertEqual(client.post('/chat', json={'query': '/research 它的用量'}).json()['research']['status'], 'candidates')
            client.delete('/conversation')
            self.assertEqual(client.post('/chat', json={'query': '/research 它的用量'}).json()['research']['status'], 'clarify_entity')
            with patch.object(self.ad.research_runtime, 'get_service', return_value=None):
                disabled = client.post('/chat', json={'query': '/research 示例叶炭的功效'})
                self.assertEqual(disabled.status_code, 503)
                self.assertEqual(disabled.json()['research']['status'], 'research_disabled')
        self.assertEqual(self.calls, [])
