"""SQLite run journal with optimistic writes and conservative stale-run recovery."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


class RunConflict(RuntimeError): pass


class RunStore:
    def __init__(self, path, *, clock=time.time):
        self.path=Path(path)
        if self.path.is_symlink():raise ValueError('Journal cannot be a symlink')
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.clock=clock
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables:
                    if tables!={'run_meta','run_journal'} or db.execute('SELECT version FROM run_meta').fetchall()!=[(1,)]:
                        raise ValueError('Not a supported run journal')
                else:
                    db.execute('CREATE TABLE run_meta(version INTEGER NOT NULL)')
                    db.execute('INSERT INTO run_meta VALUES(1)')
                    db.execute('CREATE TABLE run_journal(id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL, document TEXT NOT NULL)')
            db.execute('PRAGMA journal_mode=WAL')
        os.chmod(self.path,0o600)

    def create(self, owner, question):
        if not owner or not isinstance(question,str) or not 1<=len(question)<=2000:raise ValueError('Invalid run')
        doc={'id':uuid4().hex,'revision':0,'status':'running','question':question,'updated':self.clock(),
             'snapshot':{},'result':None,'recovery':'manual_rerun_only'}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO run_journal VALUES(?,?,?,?,?,?)',(doc['id'],owner,0,'running',doc['updated'],json.dumps(doc)))
        return doc

    def get(self, owner, identifier):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT document FROM run_journal WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if row is None:raise KeyError('Run not found')
        return json.loads(row[0])

    def list(self, owner):
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT document FROM run_journal WHERE owner=? ORDER BY updated DESC,id LIMIT 20',(owner,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save(self, owner, document, *, snapshot=None, result=None, status='running'):
        if status not in ('running','complete','partial','needs_input','cancelled','failed','interrupted'):raise ValueError('Invalid status')
        doc={**document,'revision':document['revision']+1,'updated':self.clock(),'status':status}
        if snapshot is not None:doc['snapshot']=snapshot
        if result is not None:doc['result']=result
        raw=json.dumps(doc,ensure_ascii=False)
        if len(raw.encode())>2_000_000:raise ValueError('Journal entry too large')
        with closing(sqlite3.connect(self.path)) as db, db:
            changed=db.execute('UPDATE run_journal SET revision=?,status=?,updated=?,document=? WHERE id=? AND owner=? AND revision=? AND status=?',
                (doc['revision'],status,doc['updated'],raw,doc['id'],owner,document['revision'],'running')).rowcount
            if changed!=1:raise RunConflict('Run changed or already ended')
        return doc

    def recover(self, owner, identifier):
        doc=self.get(owner,identifier)
        # Executor calls are bounded to 30s and entire runs to 120s; no blind
        # startup sweep that could interrupt another worker's active execution.
        if doc['status']!='running' or self.clock()-doc['updated']<=180:
            raise RunConflict('Run is active or already ended')
        return self.save(owner,doc,status='interrupted')

    def delete(self, owner, identifier):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM run_journal WHERE id=? AND owner=?',(identifier,owner)).fetchone()
            if row is None:raise KeyError('Run not found')
            if row[0]=='running':raise RunConflict('Cannot delete a running entry')
            db.execute('DELETE FROM run_journal WHERE id=? AND owner=?',(identifier,owner))
