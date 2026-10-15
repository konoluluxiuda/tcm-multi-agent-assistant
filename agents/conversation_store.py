"""Durable, completed-turn snapshots. JSON only; no pickle or intermediate checkpoints."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]


class MemoryUnavailable(RuntimeError):
    pass


class ConversationConflict(RuntimeError):
    pass


class SessionExpired(RuntimeError):
    pass


@dataclass
class ConversationSnapshot:
    revision: int
    messages: list
    tcm_context: dict | None


class ConversationStore:
    def __init__(self, path, *, ttl_seconds=30*86400, clock=time.time):
        self.path = Path(path).resolve()
        self.ttl_seconds = int(ttl_seconds)
        if self.ttl_seconds <= 0:
            raise ValueError('Memory retention must be positive')
        self.clock = clock

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('CHAT_MEMORY_DB') or ROOT / 'var/memory/conversations.sqlite3',
                   ttl_seconds=int(os.environ.get('CHAT_MEMORY_TTL_DAYS', '30'))*86400)

    @contextmanager
    def connection(self):
        con = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            con = sqlite3.connect(self.path, timeout=10)
            con.execute('PRAGMA secure_delete=ON')
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise MemoryUnavailable('Unsupported conversation schema')
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if 'settings' not in tables:
                    raise MemoryUnavailable('Not a conversation database')
                marker = con.execute("SELECT value FROM settings WHERE key='store_kind'").fetchone()
                if marker != ('tcm_completed_conversations_v1',):
                    raise MemoryUnavailable('Not a conversation database')
            con.execute('PRAGMA journal_mode=WAL')
            # Schema and marker commit together, including on a first-start crash.
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            con.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    key TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
                )
            ''')
            con.execute("INSERT OR IGNORE INTO settings VALUES ('store_kind','tcm_completed_conversations_v1')")
            con.execute('PRAGMA user_version=1')
            con.commit()
            yield con
            con.commit()
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            if con is not None:
                con.rollback()
            raise MemoryUnavailable('Conversation storage unavailable') from exc
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def key(session_id):
        return hashlib.sha256(session_id.encode('utf-8')).hexdigest()

    def _secret(self):
        with self.connection() as con:
            con.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('cookie_secret', secrets.token_hex(32)))
            return con.execute('SELECT value FROM settings WHERE key=?', ('cookie_secret',)).fetchone()[0]

    def new_cookie(self):
        identifier = secrets.token_hex(16)
        signature = hmac.new(self._secret().encode(), identifier.encode(), hashlib.sha256).hexdigest()
        return identifier + '.' + signature

    def valid_cookie(self, value):
        if not value or not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{64}', value):
            return False
        identifier, signature = value.split('.')
        expected = hmac.new(self._secret().encode(), identifier.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def load(self, session_id):
        from langchain_core.messages import messages_from_dict
        with self.connection() as con:
            row = con.execute('SELECT revision,payload,updated_at,deleted FROM conversations WHERE key=?',
                              (self.key(session_id),)).fetchone()
            if row is None:
                return ConversationSnapshot(0, [], None)
            revision, raw, updated, deleted = row
            if deleted:
                raise SessionExpired('Conversation was cleared')
            if updated + self.ttl_seconds <= self.clock():
                con.execute("UPDATE conversations SET payload='{}',deleted=1,revision=revision+1 WHERE key=? AND revision=?",
                            (self.key(session_id), revision))
                # Commit the deletion before reporting expiry.
                con.commit()
                raise SessionExpired('Conversation expired')
            obj = json.loads(raw)
            if obj['version'] != 1:
                raise MemoryUnavailable('Unsupported conversation payload')
            return ConversationSnapshot(revision, messages_from_dict(obj['messages']), obj['tcm_context'])

    def save(self, session_id, messages, tcm_context, *, revision):
        from langchain_core.messages import messages_to_dict
        with self.connection() as con:
            payload = json.dumps({'version': 1, 'messages': messages_to_dict(messages),
                                  'tcm_context': tcm_context}, ensure_ascii=False, allow_nan=False)
            key, now = self.key(session_id), self.clock()
            if revision == 0:
                cursor = con.execute('INSERT OR IGNORE INTO conversations VALUES (?,1,?,?,0)', (key, payload, now))
            else:
                cursor = con.execute('''UPDATE conversations SET payload=?,updated_at=?,revision=revision+1
                    WHERE key=? AND revision=? AND deleted=0''', (payload, now, key, revision))
            if cursor.rowcount != 1:
                raise ConversationConflict('Conversation changed; reload before retrying')

    def delete(self, session_id):
        # Keep only a tombstone so an in-flight save cannot recreate deleted content.
        with self.connection() as con:
            con.execute('''INSERT INTO conversations VALUES (?,1,'{}',?,1)
                ON CONFLICT(key) DO UPDATE SET payload='{}',deleted=1,revision=revision+1''',
                        (self.key(session_id), self.clock()))

    def purge_expired(self):
        with self.connection() as con:
            cursor = con.execute("""UPDATE conversations SET payload='{}',deleted=1,revision=revision+1
                WHERE deleted=0 AND updated_at<=?""", (self.clock()-self.ttl_seconds,))
            return cursor.rowcount
