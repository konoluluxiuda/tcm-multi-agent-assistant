"""Durability, deletion races, retention and cookie ownership without a provider."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agents.conversation_store import ConversationConflict, ConversationStore, MemoryUnavailable, SessionExpired


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'memory.sqlite3'
        self.now = 1000
        self.store = ConversationStore(self.db, ttl_seconds=100, clock=lambda: self.now)
        from langchain_core.messages import HumanMessage
        self.messages = [HumanMessage(content='remember fixture-only fact')]

    def test_new_store_instance_recovers_messages_context_and_signing_key(self):
        cookie = self.store.new_cookie()
        self.store.save(cookie, self.messages, {'status': 'ambiguous_entity'}, revision=0)
        reopened = ConversationStore(self.db, ttl_seconds=100, clock=lambda: self.now)
        self.assertTrue(reopened.valid_cookie(cookie))
        restored = reopened.load(cookie)
        self.assertEqual(restored.messages[0].content, self.messages[0].content)
        self.assertEqual(restored.tcm_context, {'status': 'ambiguous_entity'})
        self.assertNotIn(cookie.encode(), self.db.read_bytes())
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_cookie_tampering_and_other_database_are_rejected(self):
        cookie = self.store.new_cookie()
        self.assertFalse(self.store.valid_cookie(cookie[:-1] + ('0' if cookie[-1] != '0' else '1')))
        self.assertFalse(self.store.valid_cookie('A'))
        other = ConversationStore(Path(self.tmp.name) / 'other.sqlite3')
        self.assertFalse(other.valid_cookie(cookie))

    def test_conflicting_save_cannot_overwrite_committed_turn(self):
        self.store.save('A', self.messages, None, revision=0)
        snapshot = self.store.load('A')
        self.store.save('A', [], None, revision=snapshot.revision)
        with self.assertRaises(ConversationConflict):
            self.store.save('A', self.messages, None, revision=snapshot.revision)
        self.assertEqual(self.store.load('A').messages, [])
        self.assertEqual(self.store.load('B').messages, [])

    def test_deleted_session_cannot_be_resurrected_by_an_inflight_save(self):
        self.store.save('A', self.messages, None, revision=0)
        revision = self.store.load('A').revision
        self.store.delete('A')
        for version in [0, revision]:
            with self.assertRaises(ConversationConflict):
                self.store.save('A', self.messages, None, revision=version)
        with self.assertRaises(SessionExpired): self.store.load('A')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT payload,deleted FROM conversations').fetchone(), ('{}', 1))

    def test_expiry_deletes_payload_and_blocks_old_cookie(self):
        self.store.save('A', self.messages, None, revision=0)
        self.now += 101
        with self.assertRaises(SessionExpired): self.store.load('A')
        self.store.save('B', self.messages, None, revision=0)
        self.now += 101
        self.assertEqual(self.store.purge_expired(), 1)
        with self.assertRaises(SessionExpired): self.store.load('B')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM conversations WHERE payload!='{}'").fetchone()[0], 0)

    def test_corruption_or_unrelated_database_never_becomes_empty_memory(self):
        self.store.save('A', self.messages, None, revision=0)
        with sqlite3.connect(self.db) as con:
            con.execute('UPDATE conversations SET payload=?', ('{broken json',))
        with self.assertRaises(MemoryUnavailable): self.store.load('A')
        other = Path(self.tmp.name) / 'herbs.sqlite3'
        with sqlite3.connect(other) as con:
            con.execute('CREATE TABLE herbs (id INTEGER)')
            con.execute('PRAGMA user_version=1')
        with self.assertRaises(MemoryUnavailable): ConversationStore(other).load('A')
        with sqlite3.connect(other) as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='conversations'").fetchone())

    def test_only_latest_payload_exists_without_checkpoint_history(self):
        self.store.save('A', self.messages, None, revision=0)
        self.store.save('A', [], None, revision=1)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM conversations').fetchone()[0], 1)
            payload = json.loads(con.execute('SELECT payload FROM conversations').fetchone()[0])
            self.assertEqual(payload['messages'], [])

    def test_serialized_messages_remain_compatible_with_existing_codec(self):
        self.store.save_serialized('A', [{'type': 'human', 'data': {'content': 'fixture'}}], None, revision=0)
        self.assertEqual(self.store.load('A').messages[0].content, 'fixture')
        self.store.save('A', self.messages, None, revision=1)
        self.assertEqual(self.store.load_serialized('A').messages[0]['data']['content'], self.messages[0].content)
