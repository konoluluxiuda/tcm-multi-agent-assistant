"""Owner-scoped persistent baseline workspaces and immutable result snapshots."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from tcm.dataops.data_access import canonical


class Conflict(ValueError):
    pass


class Workspaces:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'workspaces.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            marker = db.execute('PRAGMA application_id').fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if marker not in (0, 1413696855) or marker == 0 and tables:
                raise Conflict('Not a DataOps workspace database')
            db.execute('PRAGMA application_id=1413696855')
            db.execute('''CREATE TABLE IF NOT EXISTS workspaces(
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_key TEXT NOT NULL,
                binding TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,document TEXT NOT NULL,
                UNIQUE(owner,request_key))''')

    def reserve(self, owner, key, goal, source_revision):
        binding = hashlib.sha256(canonical({'goal': goal, 'source_revision': source_revision}).encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT binding,document FROM workspaces WHERE owner=? AND request_key=?', (owner, key)).fetchone()
            if old:
                if old[0] != binding:
                    raise Conflict('Request key already bound to another goal or source version')
                return json.loads(old[1]), False
            if db.execute('SELECT count(*) FROM workspaces WHERE owner=?', (owner,)).fetchone()[0] >= 100:
                raise Conflict('Workspace quota reached')
            doc = {'id': uuid4().hex, 'revision': 1, 'created': time.time(), 'goal': goal,
                   'source_revision': source_revision, 'status': 'building', 'mode': 'fixed_local_F'}
            db.execute('INSERT INTO workspaces VALUES(?,?,?,?,?,?,?)',
                       (doc['id'], owner, key, binding, 'building', doc['created'], canonical(doc)))
            return doc, True

    def get(self, owner, identifier):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT document FROM workspaces WHERE owner=? AND id=?', (owner, identifier)).fetchone()
        if not row:
            raise KeyError(identifier)
        return json.loads(row[0])

    def listing(self, owner):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute('SELECT document FROM workspaces WHERE owner=? ORDER BY created DESC LIMIT 100', (owner,))
            return [{k: d[k] for k in ('id', 'revision', 'created', 'status', 'goal', 'mode')}
                    for (raw,) in rows for d in [json.loads(raw)]]

    def by_request(self, owner, key):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT document FROM workspaces WHERE owner=? AND request_key=?', (owner, key)).fetchone()
        return json.loads(row[0]) if row else None

    def finish(self, owner, identifier, result=None, artifact=None, error=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document,status FROM workspaces WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if not row:
                raise KeyError(identifier)
            if row[1] != 'building':
                raise Conflict('Workspace is immutable after publication')
            doc = json.loads(row[0])
            doc.update(status='failed' if error else result['status'], result=result, artifact=artifact, error=error)
            if len(canonical(doc).encode()) > 8_000_000:
                raise Conflict('Workspace exceeds storage limit')
            db.execute('UPDATE workspaces SET status=?,document=? WHERE owner=? AND id=?',
                       (doc['status'], canonical(doc), owner, identifier))
        return doc

    def artifact(self, owner, identifier):
        doc = self.get(owner, identifier)
        if not doc.get('artifact'):
            raise KeyError(identifier)
        if doc['status'] not in ('complete', 'partial'):
            raise Conflict('Artifact is not a published delivery')
        filename = doc['artifact']['filename']
        if filename != 'v' + str(doc['revision']) + '.xlsx':
            raise Conflict('Invalid artifact name')
        path = self.root / identifier / filename
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != doc['artifact']['sha256']:
            raise Conflict('Published file missing or changed')
        return path, doc

    def reserve_agent(self, owner, key, goal, source_revision, *, question, parent_id=None,
                      expected_revision=None, refresh_source_ids=()):
        """One immutable child per parent. Concurrent edits must refresh/fork explicitly."""
        payload = dict(goal=goal, source_revision=source_revision, question=question,
                       parent_id=parent_id, expected_revision=expected_revision,
                       refresh_source_ids=list(refresh_source_ids))
        binding = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT binding,document FROM workspaces WHERE owner=? AND request_key=?', (owner, key)).fetchone()
            if old:
                if old[0] != binding:
                    raise Conflict('Request key bound to another revision')
                return json.loads(old[1]), False
            parent = None
            if parent_id:
                row = db.execute('SELECT document FROM workspaces WHERE owner=? AND id=?', (owner, parent_id)).fetchone()
                if not row:
                    raise KeyError(parent_id)
                parent = json.loads(row[0])
                if (parent['revision'] != expected_revision or parent['status'] not in ('complete', 'partial') or
                        parent['source_revision'] != source_revision):
                    raise Conflict('Stale parent, incomplete parent or changed data; start a new full run')
                siblings = db.execute('SELECT document FROM workspaces WHERE owner=?', (owner,)).fetchall()
                if any(json.loads(r[0]).get('parent_id') == parent_id and
                       json.loads(r[0])['status'] not in ('failed', 'cancelled') for r in siblings):
                    raise Conflict('This parent already has a revision; refresh before editing')
            elif expected_revision is not None or refresh_source_ids:
                raise Conflict('Revision/refresh requires a parent')
            if db.execute('SELECT count(*) FROM workspaces WHERE owner=?', (owner,)).fetchone()[0] >= 100:
                raise Conflict('Workspace quota reached')
            doc = dict(id=uuid4().hex, revision=parent['revision'] + 1 if parent else 1,
                       created=time.time(), goal=goal, source_revision=source_revision, question=question,
                       parent_id=parent_id, refresh_source_ids=list(refresh_source_ids),
                       status='building', mode='dynamic_A', pending=None)
            db.execute('INSERT INTO workspaces VALUES(?,?,?,?,?,?,?)',
                       (doc['id'], owner, key, binding, 'building', doc['created'], canonical(doc)))
            return doc, True

    def project(self, owner, identifier, **updates):
        """UI projection only; native checkpoints are the sole graph recovery authority."""
        allowed = {'status', 'pending', 'progress', 'result', 'artifact', 'error', 'job_id', 'goal_understanding'}
        if set(updates) - allowed:
            raise Conflict('Invalid projection fields')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM workspaces WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if not row:
                raise KeyError(identifier)
            doc = json.loads(row[0])
            if updates.get('error') in ('queue_busy','queue_not_enqueued') and doc.get('job_id'):
                return doc  # A later failed admission cannot overwrite an acknowledged job.
            if 'job_id' in updates:
                if doc.get('job_id') not in (None,updates['job_id']):
                    raise Conflict('Workspace already bound to another queue job')
                # Inspect the committed state, not a producer's stale snapshot.
                # A second same-key admission must not regress a running/result state.
                if doc['status']=='queue_busy':doc.update(status='building',error=None)
            doc.update(updates)
            if len(canonical(doc).encode()) > 8_000_000:
                raise Conflict('Workspace exceeds storage limit')
            db.execute('UPDATE workspaces SET status=?,document=? WHERE owner=? AND id=?',
                       (doc['status'], canonical(doc), owner, identifier))
            return doc
