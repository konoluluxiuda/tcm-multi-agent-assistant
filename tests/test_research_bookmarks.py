"""Durable explicit bookmarks must keep owner, revision and source boundaries."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tcm.research_bookmarks import (BookmarkStore, BookmarkConflict, BookmarkMissing,
    BookmarkLimit, BookmarkUnavailable, capture, preview)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'bookmarks.sqlite3'
        self.store = BookmarkStore(self.path)
        self.cookie = self.store.new_cookie()
        self.payload = {'kind': 'research', 'focus': {'name': '示例', 'last_question': '示例的功效'}}

    def test_restart_and_owner_isolation(self):
        item, _ = self.store.create(self.cookie, '标题', '人工笔记', self.payload)
        reopened = BookmarkStore(self.path)
        self.assertTrue(reopened.valid_cookie(self.cookie))
        self.assertFalse(reopened.valid_cookie('fabricated-cookie'))
        self.assertEqual(reopened.get(self.cookie, item['id'])['note'], '人工笔记')
        other = reopened.new_cookie()
        self.assertEqual(reopened.list(other)['items'], [])
        with self.assertRaises(BookmarkMissing):reopened.get(other, item['id'])
        with sqlite3.connect(self.path) as con:
            self.assertNotIn(self.cookie, str(con.execute('SELECT * FROM owners').fetchall()))

    def test_duplicate_save_keeps_original_note_and_revision(self):
        first, created = self.store.create(self.cookie, '标题', '保留', self.payload)
        duplicate, created_again = self.store.create(self.cookie, '替换', '不能覆盖', self.payload)
        self.assertTrue(created);self.assertFalse(created_again)
        self.assertEqual(first, duplicate)

    def test_optimistic_updates_and_deletes_do_not_overwrite_another_editor(self):
        item, _ = self.store.create(self.cookie, '标题', '', self.payload)
        changed = self.store.update(self.cookie, item['id'], 1, '修改', '笔记')
        self.assertEqual(changed['revision'], 2)
        with self.assertRaises(BookmarkConflict):self.store.update(self.cookie, item['id'], 1, '旧编辑', '')
        with self.assertRaises(BookmarkConflict):self.store.delete(self.cookie, item['id'], 1)
        other = self.store.new_cookie()
        with self.assertRaises(BookmarkMissing):self.store.update(other, item['id'], 2, '他人', '')
        self.store.delete(self.cookie, item['id'], 2)
        with self.assertRaises(BookmarkMissing):self.store.get(self.cookie, item['id'])

    def test_pagination_and_limit_are_owner_scoped(self):
        for i in range(21):self.store.create(self.cookie, str(i), '', {'kind': 'research', 'index': i})
        self.assertEqual(len(self.store.list(self.cookie)['items']), 20)
        self.assertEqual(self.store.list(self.cookie)['next_offset'], 20)
        self.assertEqual(len(self.store.list(self.cookie, offset=20)['items']), 1)
        with patch.object(self.store, 'LIMIT', 21), self.assertRaises(BookmarkLimit):
            self.store.create(self.cookie, 'full', '', {'kind': 'research', 'index': 22})

    def test_wrong_database_marker_is_rejected(self):
        path = self.path.with_name('other.sqlite3')
        with sqlite3.connect(path) as con:con.execute('CREATE TABLE unrelated(value TEXT)')
        before = path.read_bytes()
        with self.assertRaises(BookmarkUnavailable):BookmarkStore(path).new_cookie()
        self.assertEqual(before, path.read_bytes())


class ApiTests(unittest.TestCase):
    def setUp(self):
        import app
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        from scripts.build_mineru_retrieval import build
        from tests.test_mineru_chat import Service
        from tests.test_mineru_retrieval import fixture
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.app = app;self.ad = ad
        fixture(self.root / 'view');build(self.root / 'view', self.root / 'index')
        self.service = Service(self.root / 'index')
        self.memory = ConversationStore(self.root / 'memory.sqlite3')
        def forbidden(*a, **kw):raise AssertionError('Bookmark reached a model')
        patches = [patch.dict(os.environ, {'ENABLE_RESEARCH_BOOKMARKS': '1', 'RESEARCH_TOOLS_ONLY': '1',
                                          'ENABLE_NATURAL_ROUTING': '1', 'RESEARCH_BOOKMARK_DB': str(self.root / 'saved.sqlite3')}),
                   patch.object(ad, 'memory', self.memory),
                   patch.object(ad.research_runtime, 'get_service', return_value=self.service),
                   patch.object(ad.config.agent_decision, 'llm', RunnableLambda(forbidden)),
                   patch.object(ad.config.conversation, 'llm', RunnableLambda(forbidden)),
                   patch.object(ad.LocalGuardrails, 'check_input', forbidden),
                   patch.object(ad.LocalGuardrails, 'check_output', forbidden)]
        for p in patches:p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])
        self.client = TestClient(app.app);self.addCleanup(self.client.close)
        self.client.get('/api/research/bookmarks')

    def select(self):
        with redirect_stdout(io.StringIO()):
            response = self.client.post('/chat', json={'query': '示例叶炭的功效'})
        self.assertEqual(response.status_code, 200)
        return self.client.get('/api/research/bookmarks').json()['conversation_revision']

    def save(self, revision=None, **fields):
        revision = self.select() if revision is None else revision
        return self.client.post('/api/research/bookmarks', json={'expected_revision': revision, **fields})

    def test_bookmark_outlives_chat_clear_and_memory_reopen_without_raw_evidence(self):
        item = self.save(note='用户自己写的笔记').json()['item']
        self.client.delete('/conversation')
        with patch.object(self.ad, 'memory', type(self.memory)(self.memory.path)):
            self.assertEqual(self.client.get('/api/research/bookmarks').json()['total'], 1)
            result = self.client.post('/api/research/bookmarks/' + item['id'] + '/preview')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['research']['status'], 'candidates')
        self.assertEqual(result.json()['llm_calls'], 0)
        self.assertEqual(self.client.get('/conversation').json()['messages'], [])
        with sqlite3.connect(self.root / 'saved.sqlite3') as con:
            payload = con.execute('SELECT payload FROM bookmarks').fetchone()[0]
        self.assertNotIn('测试用途', payload);self.assertNotIn('evidence', json.loads(payload))
        self.assertLess(len(payload), 2000)

    def test_cross_browser_read_edit_delete_preview_are_denied(self):
        from fastapi.testclient import TestClient
        item = self.save().json()['item'];path = '/api/research/bookmarks/' + item['id']
        with TestClient(self.app.app) as other:
            self.assertEqual(other.get('/api/research/bookmarks').json()['items'], [])
            self.assertEqual(other.post(path + '/preview').status_code, 404)
            self.assertEqual(other.patch(path, json={'revision': 1, 'title': 'x'}).status_code, 404)
            self.assertEqual(other.delete(path + '?revision=1').status_code, 404)

    def test_chat_revision_conflict_and_source_pin_are_not_client_controlled(self):
        revision = self.select()
        with redirect_stdout(io.StringIO()):self.client.post('/chat', json={'query': '示例叶炭的用量'})
        self.assertEqual(self.save(revision).status_code, 409)
        self.assertEqual(self.save(self.client.get('/api/research/bookmarks').json()['conversation_revision'],
                                   payload={'kind': 'research'}).status_code, 422)
        self.assertEqual(self.client.get('/api/research/bookmarks').json()['total'], 0)

    def test_edit_conflict_stale_source_and_unavailable_source_are_distinct(self):
        item = self.save().json()['item'];path = '/api/research/bookmarks/' + item['id']
        first = self.client.patch(path, json={'revision': 1, 'title': '<img src=x onerror=alert(1)>', 'note': '<script>x</script>'})
        self.assertEqual(first.json()['item']['revision'], 2)
        self.assertEqual(self.client.patch(path, json={'revision': 1, 'title': 'stale'}).status_code, 409)
        self.assertEqual(self.client.delete(path + '?revision=1').status_code, 409)
        with patch('tcm.mineru_chat.ResearchTool.valid_context', return_value=False):
            self.assertEqual(self.client.post(path + '/preview').status_code, 409)
        with patch.object(self.ad.research_runtime, 'get_service', return_value=None):
            self.assertEqual(self.client.post(path + '/preview').status_code, 503)
        self.assertEqual(self.client.delete(path + '?revision=2').status_code, 200)

    def test_missing_focus_disabled_feature_and_storage_failure_are_explicit(self):
        self.assertFalse(self.client.get('/api/research/bookmarks').json()['can_save'])
        self.assertEqual(self.save(0).status_code, 409)
        with patch.dict(os.environ, {'ENABLE_RESEARCH_BOOKMARKS': '0'}):
            self.assertEqual(self.client.get('/api/research/bookmarks').status_code, 404)
            self.assertNotIn('id="research-bookmarks"', self.client.get('/').text)
        with patch.dict(os.environ, {'RESEARCH_BOOKMARK_DB': str(self.root / 'not-a-file')}):
            (self.root / 'not-a-file').mkdir()
            response = self.client.get('/api/research/bookmarks')
            self.assertEqual(response.status_code, 503);self.assertNotIn(str(self.root), response.text)

    def test_personal_notes_are_not_sent_to_retriever_or_conversation(self):
        item = self.save(note='note-unique-not-source').json()['item']
        before = list(self.service.queries)
        response = self.client.post('/api/research/bookmarks/' + item['id'] + '/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.queries[len(before):], ['示例叶炭的功效'])
        history = self.client.get('/conversation').json()
        self.assertNotIn('note-unique-not-source', str(history))


class RelationBookmarkTests(unittest.TestCase):
    def setUp(self):
        from tests import test_herb_target_paths as fixtures
        fixtures.HerbTargetPathTests.setUp(self)

    def test_relation_page_sources_and_staleness_survive_bookmark_storage(self):
        from tcm.relation_memory import execute_turn
        from tcm.relation_queries import parse
        from tcm.agent_research import pack_contexts
        from tcm.research_bookmarks import BookmarkStale
        _, focus = execute_turn(parse('TestHerb的成分关联哪些靶点 第2页'), None)
        payload = capture(pack_contexts(None, None, None, focus))
        store = BookmarkStore(self.root / 'bookmarks.sqlite3');cookie = store.new_cookie()
        item, _ = store.create(cookie, '路径第二页', '', payload)
        restored = BookmarkStore(store.path).get(cookie, item['id'])['payload']
        result = preview(restored, None)['assistant']
        self.assertEqual(result['relations']['page'], 2)
        self.assertEqual(result['relations']['data']['total'], 26)
        self.assertNotIn('raw_row', json.dumps(restored))
        with sqlite3.connect(self.enrichment) as con:
            con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        with self.assertRaises(BookmarkStale):preview(restored, None)


if __name__ == '__main__':unittest.main()
