"""Immutable private observations; commit content before publishing a bounded index.

No remote storage, file paths from model arguments, or automatic orphan deletion.
Quotas count UTF-8 payload bytes, not free disk space or provider tokens.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from tcm.research_business_tools import SPECS
from tcm.task_tools import Contract, ToolResult
from pydantic import Field


class EvidenceConflict(ValueError):
    pass


class EvidencePolicy(Contract):
    inline_bytes: int = Field(default=16000, ge=1000, le=48000)
    payload_bytes: int = Field(default=4_000_000, ge=1000, le=4_000_000)
    run_bytes: int = Field(default=16_000_000, ge=1000, le=256_000_000)
    owner_bytes: int = Field(default=64_000_000, ge=1000, le=256_000_000)
    total_bytes: int = Field(default=256_000_000, ge=1000, le=1_000_000_000)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def preview(value, depth=0):
    """Explicit display preview only; never used to validate source facts."""
    if depth > 6:
        return '[内容较长，请回读完整证据]'
    if isinstance(value, dict):
        return {k: preview(v, depth+1) for k, v in value.items()}
    if isinstance(value, list):
        return [preview(v, depth+1) for v in value[:20]]
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500]+'…'
    return value


class EvidenceStore:
    APP_ID = 1413696851

    def __init__(self, path, policy=None):
        self.path = Path(path).absolute()
        self.policy = policy or EvidencePolicy()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path)) as db, db:
            version = db.execute('PRAGMA application_id').fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version not in (0, self.APP_ID) or version == 0 and tables:
                raise EvidenceConflict('Wrong evidence database')
            db.execute('''CREATE TABLE IF NOT EXISTS evidence(
                handle TEXT PRIMARY KEY, owner TEXT NOT NULL, run_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(owner,run_id,evidence_id))''')
            db.execute(f'PRAGMA application_id={self.APP_ID}')

    @contextmanager
    def activity(self, *, exclusive=False):
        """Cross-process maintenance exclusion, held for an entire loop/read."""
        fd = os.open(self.path.with_name(self.path.name+'.activity'),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EvidenceConflict('Evidence store is active') from None
            yield
        finally:
            os.close(fd)

    @staticmethod
    def checked(payload):
        name = payload.get('tool')
        if name not in SPECS or payload.get('status') not in ('ok', 'empty'):
            raise EvidenceConflict('Unsupported evidence')
        ToolResult[SPECS[name][1]].model_validate(payload)
        return payload

    def put(self, owner, run_id, evidence_id, payload):
        if not owner or not run_id or not re.fullmatch(r'obs-[1-9][0-9]{0,8}', evidence_id):
            raise EvidenceConflict('Invalid evidence identity')
        raw = canonical(payload)
        if len(raw.encode()) > self.policy.payload_bytes:
            raise EvidenceConflict('Evidence exceeds payload quota')
        self.checked(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT handle,digest,payload FROM evidence WHERE owner=? AND run_id=? AND evidence_id=?',
                             (owner, run_id, evidence_id)).fetchone()
            if row:
                if row[1:] != (digest, raw):
                    raise EvidenceConflict('Evidence ID is immutable')
                handle = row[0]
            else:
                for where, args, quota in (
                    ('WHERE owner=? AND run_id=?', (owner, run_id), self.policy.run_bytes),
                    ('WHERE owner=?', (owner,), self.policy.owner_bytes),
                    ('', (), self.policy.total_bytes),
                ):
                    used = db.execute('SELECT coalesce(sum(length(cast(payload AS BLOB))),0) FROM evidence '+where, args).fetchone()[0]
                    if used + len(raw.encode()) > quota:
                        raise EvidenceConflict('Evidence storage quota exhausted')
                handle = uuid4().hex
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?)',
                           (handle, owner, run_id, evidence_id, digest, raw))
        # Returning this index before COMMIT would allow a dangling reference.
        data = payload['data']
        fields={k:len(v) for k,v in data.items() if k in ('target_ids','rows','items') and isinstance(v,list)}
        if 'target_ids' not in fields and isinstance(data.get('profile',{}).get('target_ids'),list):
            fields['target_ids']=len(data['profile']['target_ids'])
        if isinstance(data.get('report'),dict):
            report=data['report'];rows=report.get('hits',report.get('rows'))
            if isinstance(rows,list):fields['rows']=len(rows)
            if isinstance(report.get('targets',{}).get('items'),list):fields['items']=len(report['targets']['items'])
            if isinstance(report.get('relations',{}).get('data',{}).get('items'),list):
                fields['items']=len(report['relations']['data']['items'])
        reference = {'tool': payload['tool'], 'status': payload['status'],
                     'externalized': True, 'evidence_handle': handle,
                     'source_evidence_id': evidence_id, 'content_sha256': digest,
                     'payload_bytes': len(raw.encode()),
                     'field_counts':fields,
                     'source_binding': {k: data[k] for k in ('record_id', 'query', 'dataset_version', 'catalog_revision', 'page','request') if k in data},
                     'data': preview(data),
                     'notice': 'Preview only; omitted content remains in private evidence. Use read_run_evidence for bounded slices. This index does not prove omitted details.'}
        if len(canonical(reference).encode()) > 10000:
            reference['data'] = {k: preview(data[k]) for k in
                                 ('record_id', 'query', 'dataset_version', 'catalog_revision', 'page', 'has_next', 'total', 'top_k', 'metric') if k in data}
        if len(canonical(reference).encode()) > 12000:
            raise EvidenceConflict('Evidence index exceeds budget')
        return reference

    def load(self, owner, run_id, evidence_id, reference):
        handle = reference.get('evidence_handle')
        if not isinstance(handle, str) or not re.fullmatch('[0-9a-f]{32}', handle):
            raise EvidenceConflict('Unknown evidence')
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT evidence_id,digest,payload FROM evidence WHERE handle=? AND owner=? AND run_id=?',
                             (handle, owner, run_id)).fetchone()
        if row is None:
            raise EvidenceConflict('Evidence unavailable')
        eid, digest, raw = row
        if (eid != evidence_id or reference.get('source_evidence_id') != eid or
                reference.get('content_sha256') != digest or len(raw.encode()) > self.policy.payload_bytes or
                hashlib.sha256(raw.encode()).hexdigest() != digest):
            raise EvidenceConflict('Evidence integrity mismatch')
        payload = self.checked(json.loads(raw))
        data = payload['data']
        binding = {k: data[k] for k in ('record_id', 'query', 'dataset_version', 'catalog_revision', 'page','request') if k in data}
        if (reference.get('source_binding') != binding or reference.get('tool') != payload['tool'] or
                reference.get('status') != payload['status']):
            raise EvidenceConflict('Evidence binding changed')
        return payload

    def delete_run(self, owner, run_id):
        with self.activity(exclusive=True), closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM evidence WHERE owner=? AND run_id=?', (owner, run_id))


class RunEvidence:
    def __init__(self, path, owner, run_id, *, policy=None):
        if not owner or not run_id:
            raise EvidenceConflict('Host owner and run required')
        self.store = EvidenceStore(path, policy)
        self.owner, self.run_id = owner, run_id

    @property
    def contract(self):
        return {'version': 1, 'path': str(self.store.path), 'owner': self.owner,
                'run_id': self.run_id, 'policy': self.store.policy.model_dump()}

    def prepare(self, evidence_id, payload):
        if payload.get('status') not in ('ok', 'empty') or len(canonical(payload).encode()) <= self.store.policy.inline_bytes:
            return payload
        return self.store.put(self.owner, self.run_id, evidence_id, payload)

    def resolve(self, evidence_id, payload):
        if payload.get('externalized'):
            try:return self.store.load(self.owner, self.run_id, evidence_id, payload)
            except (OSError, ValueError, sqlite3.Error):
                raise EvidenceConflict('Evidence unavailable or changed') from None
        return payload

    def view(self, evidence):
        return {eid: self.resolve(eid, payload) for eid, payload in evidence.items()}


def for_run(run_db, owner, run_id):
    return RunEvidence(Path(run_db).with_name('evidence.sqlite3'), owner, run_id)
