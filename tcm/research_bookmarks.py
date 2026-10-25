"""Explicit research bookmarks, separate from chat history and model memory."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time


class BookmarkUnavailable(RuntimeError):
    pass


class BookmarkConflict(RuntimeError):
    pass


class BookmarkMissing(RuntimeError):
    pass


class BookmarkLimit(RuntimeError):
    pass


class BookmarkStale(RuntimeError):
    pass


class BookmarkStore:
    LIMIT = 100

    def __init__(self, path, *, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock

    @contextmanager
    def connection(self):
        con = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            con = sqlite3.connect(self.path, timeout=10)
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA secure_delete=ON')
            if con.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise BookmarkUnavailable('Unsupported bookmark schema')
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                marker = (con.execute("SELECT value FROM settings WHERE key='store_kind'").fetchone()
                          if 'settings' in tables else None)
                if marker is None or marker[0] != 'tcm_research_bookmarks_v1':
                    raise BookmarkUnavailable('Not a bookmark database')
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            con.execute("INSERT OR IGNORE INTO settings VALUES('store_kind','tcm_research_bookmarks_v1')")
            con.execute('CREATE TABLE IF NOT EXISTS owners(owner TEXT PRIMARY KEY)')
            con.execute('''CREATE TABLE IF NOT EXISTS bookmarks(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, fingerprint TEXT NOT NULL,
                title TEXT NOT NULL, note TEXT NOT NULL, payload TEXT NOT NULL,
                revision INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(owner,fingerprint))''')
            con.execute('CREATE INDEX IF NOT EXISTS bookmarks_owner ON bookmarks(owner,updated_at,id)')
            con.execute('PRAGMA user_version=1')
            con.commit()
            yield con
            con.commit()
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            if con is not None:
                con.rollback()
            raise BookmarkUnavailable('Bookmark storage unavailable') from exc
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def owner(cookie):
        return hashlib.sha256(cookie.encode()).hexdigest()

    def valid_cookie(self, cookie):
        if not isinstance(cookie, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', cookie):
            return False
        with self.connection() as con:
            return con.execute('SELECT 1 FROM owners WHERE owner=?', (self.owner(cookie),)).fetchone() is not None

    def new_cookie(self):
        cookie = secrets.token_urlsafe(32)
        with self.connection() as con:
            con.execute('INSERT INTO owners VALUES(?)', (self.owner(cookie),))
        return cookie

    @staticmethod
    def public(row):
        return {**{key: row[key] for key in ('id', 'title', 'note', 'revision', 'created_at', 'updated_at')},
                'kind': json.loads(row['payload'])['kind']}

    def list(self, cookie, *, offset=0):
        with self.connection() as con:
            owner = self.owner(cookie)
            total = con.execute('SELECT count(*) FROM bookmarks WHERE owner=?', (owner,)).fetchone()[0]
            rows = con.execute('SELECT * FROM bookmarks WHERE owner=? ORDER BY updated_at DESC,id LIMIT 20 OFFSET ?',
                               (owner, offset)).fetchall()
            return {'items': [self.public(row) for row in rows], 'total': total,
                    'next_offset': offset + 20 if offset + 20 < total else None}

    def get(self, cookie, identifier):
        with self.connection() as con:
            row = con.execute('SELECT * FROM bookmarks WHERE id=? AND owner=?',
                              (identifier, self.owner(cookie))).fetchone()
            if row is None:
                raise BookmarkMissing()
            return {**self.public(row), 'payload': json.loads(row['payload'])}

    def create(self, cookie, title, note, payload):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 8192:
            raise BookmarkLimit()
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        owner = self.owner(cookie)
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            previous = con.execute('SELECT * FROM bookmarks WHERE owner=? AND fingerprint=?',
                                   (owner, fingerprint)).fetchone()
            if previous is not None:
                return self.public(previous), False  # Never overwrite an existing user's note.
            if con.execute('SELECT count(*) FROM bookmarks WHERE owner=?', (owner,)).fetchone()[0] >= self.LIMIT:
                raise BookmarkLimit()
            identifier, now = secrets.token_hex(16), self.clock()
            con.execute('INSERT INTO bookmarks VALUES(?,?,?,?,?,?,1,?,?)',
                        (identifier, owner, fingerprint, title, note, encoded, now, now))
            row = con.execute('SELECT * FROM bookmarks WHERE id=?', (identifier,)).fetchone()
            return self.public(row), True

    def update(self, cookie, identifier, revision, title, note):
        with self.connection() as con:
            cursor = con.execute('UPDATE bookmarks SET title=?,note=?,revision=revision+1,updated_at=? '
                                 'WHERE id=? AND owner=? AND revision=?',
                                 (title, note, self.clock(), identifier, self.owner(cookie), revision))
            if cursor.rowcount != 1:
                self._conflict(con, cookie, identifier)
            return self.public(con.execute('SELECT * FROM bookmarks WHERE id=?', (identifier,)).fetchone())

    def delete(self, cookie, identifier, revision):
        with self.connection() as con:
            cursor = con.execute('DELETE FROM bookmarks WHERE id=? AND owner=? AND revision=?',
                                 (identifier, self.owner(cookie), revision))
            if cursor.rowcount != 1:
                self._conflict(con, cookie, identifier)

    def export_backup(self, cookie):
        from tcm.bookmark_backup import encode, InvalidBookmarkBackup
        with self.connection() as con:
            rows = con.execute('SELECT title,note,payload FROM bookmarks WHERE owner=? ORDER BY created_at,id',
                               (self.owner(cookie),)).fetchall()
            items = [{'title': r['title'], 'note': r['note'], 'payload': json.loads(r['payload'])} for r in rows]
            try:
                return encode(items)
            except InvalidBookmarkBackup as exc:
                raise BookmarkUnavailable('Stored bookmark format invalid') from exc

    def import_backup(self, cookie, raw):
        from tcm.bookmark_backup import decode
        items = decode(raw)  # Validate every item before any bookmark write.
        unique = {}
        for item in items:
            encoded = json.dumps(item['payload'], ensure_ascii=False, sort_keys=True, allow_nan=False)
            fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
            unique.setdefault(fingerprint, (item, encoded))
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            owner = self.owner(cookie)
            existing = {r[0] for r in con.execute('SELECT fingerprint FROM bookmarks WHERE owner=?', (owner,))}
            pending = [(key, value) for key, value in unique.items() if key not in existing]
            if len(existing) + len(pending) > self.LIMIT:
                raise BookmarkLimit()
            now = self.clock()
            for fingerprint, (item, encoded) in pending:
                con.execute('INSERT INTO bookmarks VALUES(?,?,?,?,?,?,1,?,?)',
                            (secrets.token_hex(16), owner, fingerprint, item['title'], item['note'], encoded, now, now))
            return {'imported': len(pending), 'skipped_duplicates': len(items) - len(pending),
                    'total': len(existing) + len(pending), 'sources_checked': False}

    def _conflict(self, con, cookie, identifier):
        if con.execute('SELECT 1 FROM bookmarks WHERE id=? AND owner=?',
                       (identifier, self.owner(cookie))).fetchone() is None:
            raise BookmarkMissing()
        raise BookmarkConflict()


def capture(context):
    """Whitelist server-side bindings; do not copy messages, rows or model answers."""
    from tcm.agent_research import split_contexts
    from tcm.relation_memory import context_from
    _, research = split_contexts(context)
    relation = context_from(context)
    if research and research.get('last_question'):
        fields = ('kind', 'name', 'last_question', 'index_sha256', 'view_sha256',
                  'retriever_sha256', 'guards_sha256')
        return {'kind': 'research', 'focus': {**{key: research[key] for key in fields},
                                                   **({'request_sha256': research['request_sha256']} if 'request_sha256' in research else {})}}
    if relation:
        fields = ('kind', 'query_kind', 'page', 'dataset_version', 'herb_source_fingerprint',
                  'herb_sources', 'uniprot', 'target_export_sha256')
        return {'kind': 'relation', 'focus': {key: relation[key] for key in fields if key in relation}}
    return None


def check_source(payload, runtime):
    from tcm.mineru_chat import ResearchTool
    from tcm.relation_memory import valid
    if payload['kind'] == 'research':
        service = runtime.get_service()
        if service is None:
            raise BookmarkUnavailable()
        tool = ResearchTool(service)
        if not tool.valid_context(payload['focus']):
            raise BookmarkStale()
        return tool
    if payload['kind'] == 'relation':
        if not valid(payload['focus']):
            raise BookmarkStale()
        return None
    raise BookmarkStale()


def preview(payload, runtime):
    tool = check_source(payload, runtime)
    if payload['kind'] == 'research':
        result, resolved = tool.execute(payload['focus']['last_question'], None)
        if resolved is None or resolved['name'] != payload['focus']['name']:
            raise BookmarkStale()
        return {'research': result}
    from tcm.relation_memory import execute_turn
    result, _ = execute_turn({'followup': True, 'action': 'replay'}, payload['focus'])
    if result['status'] != 'ok':
        raise BookmarkUnavailable()
    return {'assistant': result}
