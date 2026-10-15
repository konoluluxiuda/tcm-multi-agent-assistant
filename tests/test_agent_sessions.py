"""Real LangGraph/checkpointer tests with deterministic model/guardrail substitutes."""
import contextlib
import importlib.util
import io
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'requires container dependencies')
class AgentSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import agents.agent_decision as ad
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda
        from agents.conversation_store import ConversationStore
        cls.ad = ad
        cls.ConversationStore = ConversationStore
        cls.decision = RunnableLambda(lambda _: AIMessage(content='{"agent":"CONVERSATION_AGENT","confidence":1.0,"reasoning":"test"}'))
        cls.conversation = RunnableLambda(lambda prompt: AIMessage(content='HERB-74291' if 'HERB-74291' in str(prompt) else 'UNKNOWN'))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = self.ConversationStore(Path(temp.name) / "memory.sqlite3")
        self.history_seen = []
        seen = self.history_seen
        class Guard:
            def __init__(self, *_): pass
            def check_input(self, query): return True, query
            def check_output(self, output, query, chat_history=''):
                seen.append(chat_history)
                return output
        self.patches = [patch.object(self.ad,'memory',self.store),
                        patch.object(self.ad,'LocalGuardrails',Guard),
                        patch.object(self.ad.config.agent_decision,'llm',self.decision),
                        patch.object(self.ad.config.conversation,'llm',self.conversation),
                        patch.object(self.ad.config,'max_conversation_history',4)]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def run_turn(self, query, session=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ad.process_query(query,session_id=session)

    def test_history_recalled_only_in_own_session(self):
        self.run_turn('remember HERB-74291','A')
        self.assertEqual(self.run_turn('recall','A')['messages'][-1].content,'HERB-74291')
        self.assertIn('HERB-74291',self.history_seen[-1])
        self.assertEqual(self.run_turn('recall','B')['messages'][-1].content,'UNKNOWN')
        self.assertNotIn('HERB-74291',self.history_seen[-1])

    def test_omitted_session_does_not_share_history(self):
        self.run_turn('remember HERB-74291')
        self.assertEqual(self.run_turn('recall')['messages'][-1].content,'UNKNOWN')

    def test_checkpoint_history_really_trimmed(self):
        for i in range(4): self.run_turn('hello '+str(i),'bounded')
        snapshot=self.store.load('bounded')
        self.assertLessEqual(len(snapshot.messages),4)
