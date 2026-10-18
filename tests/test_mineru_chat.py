"""Research turns persist only completed state and never infer missing source content."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from agents.conversation_store import ConversationStore, ConversationConflict, SessionExpired, MemoryUnavailable
from scripts.build_mineru_retrieval import build
from tcm.mineru_chat import ResearchChat
from tcm.mineru_retrieval import ResearchRetriever
from tcm.mineru_research import SnapshotChanged
from tests.test_mineru_retrieval import fixture


class Service:
    def __init__(self, directory):
        self.directory = directory
        self.sources = {}
        self.receipt = json.loads((directory / 'manifest.json').read_text())
        self.queries = []
        self.available = True

    def check(self):
        if not self.available:
            raise SnapshotChanged('Changed source')

    def search(self, question, **kwargs):
        self.check(); self.queries.append(question)
        r = ResearchRetriever(self.directory)
        try:
            return r.query(question, **kwargs)
        finally:
            r.close()


class ResearchChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        fixture(self.root / 'view'); build(self.root / 'view', self.root / 'index')
        self.service = Service(self.root / 'index')
        self.db = self.root / 'memory.sqlite3'
        self.chat = ResearchChat(self.service, self.db)
        self.cookie = self.chat.session(None)

    def tearDown(self):
        self.temp.cleanup()

    def first(self):
        return self.chat.turn(self.cookie, '示例叶炭的功效', 0)

    def test_followup_uses_explicit_name_slot_and_fresh_retrieval(self):
        self.first()
        result = self.chat.turn(self.cookie, '它的用量', 1)
        self.assertEqual(result['resolved_question'], '示例叶炭的用量')
        self.assertEqual(result['tool_result']['hits'][0]['label'], '用法与用量')
        self.assertEqual(self.service.queries, ['示例叶炭的功效', '示例叶炭的用量'])
        self.assertEqual(result['llm_calls'], 0)

    def test_source_followup_retrieves_previous_question_again(self):
        self.first(); result = self.chat.turn(self.cookie, '出处呢？', 1)
        self.assertEqual(result['resolved_question'], '示例叶炭的功效')
        self.assertTrue(result['tool_result']['hits'][0]['evidence'])

    def test_no_focus_requires_clarification_without_tool_call(self):
        result = self.chat.turn(self.cookie, '它的用量', 0)
        self.assertEqual(result['status'], 'clarify_entity')
        self.assertIsNone(result['tool_result']); self.assertEqual(self.service.queries, [])

    def test_name_only_selection_allows_followup(self):
        selected = self.chat.turn(self.cookie, '示例叶炭', 0)
        self.assertEqual(selected['focus_name'], '示例叶炭')
        self.assertEqual(self.chat.turn(self.cookie, '它的功效', 1)['status'], 'candidates')

    def test_source_clarification_after_name_only_keeps_selected_name(self):
        self.chat.turn(self.cookie, '示例叶炭', 0)
        result = self.chat.turn(self.cookie, '出处呢', 1)
        self.assertEqual(result['status'], 'field_not_resolved')
        self.assertEqual(result['focus_name'], '示例叶炭')
        self.assertEqual(self.chat.turn(self.cookie, '它的用量', 2)['status'], 'candidates')

    def test_restart_restores_signed_cookie_history_and_focus(self):
        self.first()
        restarted = ResearchChat(Service(self.root / 'index'), self.db)
        self.assertEqual(restarted.session(self.cookie), self.cookie)
        self.assertEqual(len(restarted.conversation(self.cookie)['messages']), 2)
        self.assertEqual(restarted.turn(self.cookie, '它的用量', 1)['status'], 'candidates')

    def test_different_session_cannot_read_focus_or_history(self):
        self.first(); other = self.chat.session(None)
        self.assertEqual(self.chat.conversation(other)['messages'], [])
        self.assertEqual(self.chat.turn(other, '它的用量', 0)['status'], 'clarify_entity')

    def test_scope_failure_clears_previous_focus(self):
        self.first()
        result = self.chat.turn(self.cookie, '酒示例叶的用量', 1)
        self.assertEqual(result['status'], 'variant_scope_unresolved'); self.assertIsNone(result['focus_name'])
        self.assertEqual(self.chat.turn(self.cookie, '它的用量', 2)['status'], 'clarify_entity')

    def test_changed_snapshot_cannot_reuse_old_focus_after_restart(self):
        self.first(); changed = Service(self.root / 'index'); changed.receipt['index_sha256'] = 'new'
        restarted = ResearchChat(changed, self.db)
        self.assertTrue(restarted.conversation(self.cookie)['context_stale'])
        self.assertEqual(restarted.turn(self.cookie, '它的用量', 1)['status'], 'context_stale')
        self.assertEqual(changed.queries, [])

    def test_failed_source_check_does_not_commit_a_turn(self):
        self.first(); self.service.available = False
        with self.assertRaises(SnapshotChanged):
            self.chat.turn(self.cookie, '它的用量', 1)
        self.assertEqual(self.chat.store.load_serialized(self.cookie).revision, 1)

    def test_stale_revision_and_deleted_session_cannot_overwrite(self):
        self.first()
        with self.assertRaises(ConversationConflict):
            self.chat.turn(self.cookie, '它的用量', 0)
        self.chat.delete(self.cookie)
        with self.assertRaises(SessionExpired):
            self.chat.turn(self.cookie, '它的用量', 1)

    def test_only_queries_and_fixed_messages_are_persisted(self):
        result = self.first(); text = result['tool_result']['hits'][0]['text']
        with self.chat.store.connection() as con:
            key, payload = con.execute('SELECT key,payload FROM conversations').fetchone()
            self.assertNotIn(self.cookie, key); self.assertNotIn(text, payload)
            self.assertNotIn('evidence', payload)
            self.assertNotIn('tool_result', payload)

    def test_retention_limits_to_twenty_messages(self):
        for revision in range(12):
            self.chat.turn(self.cookie, '示例叶炭的功效', revision)
        self.assertEqual(len(self.chat.conversation(self.cookie)['messages']), 20)

    def test_existing_nonresearch_conversation_database_is_rejected(self):
        other = self.root / 'main.sqlite3'; store = ConversationStore(other); store.new_cookie()
        with self.assertRaises(MemoryUnavailable):
            ResearchChat(self.service, other)

    def test_malformed_messages_fail_closed(self):
        self.chat.store.save_serialized(self.cookie, [{'type': 'ai', 'data': {}}], None, revision=0)
        with self.assertRaises(MemoryUnavailable):
            self.chat.conversation(self.cookie)

    def test_invalid_requests_do_not_create_turns(self):
        for question, revision in [('', 0), ('x' * 501, 0), ('问题', True), (None, 0)]:
            with self.assertRaises(ValueError):
                self.chat.turn(self.cookie, question, revision)
        self.assertEqual(self.chat.store.load_serialized(self.cookie).revision, 0)
