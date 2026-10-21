"""Dedicated local task snapshots with optimistic revisions; unrelated DBs are refused."""
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

KIND = 'synthetic_research_tasks_v1'


class TaskConflict(RuntimeError):pass
class TaskLimit(RuntimeError):pass


class ResearchTaskStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if Path(path).is_symlink():raise ValueError('Task database must not be a symlink')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if tables != {'research_task_meta','research_tasks'}:
                    raise ValueError('Not a dedicated research task database')
                row = db.execute('SELECT kind FROM research_task_meta').fetchall()
                if row != [(KIND,)]:raise ValueError('Unsupported research task schema')
            else:
                db.execute('CREATE TABLE research_task_meta(kind TEXT NOT NULL)')
                db.execute('INSERT INTO research_task_meta VALUES(?)',(KIND,))
                db.execute('CREATE TABLE research_tasks(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')
            db.commit()
            db.execute('PRAGMA journal_mode=WAL')
        os.chmod(self.path,0o600)

    def create(self, document):
        task = {**document,'id':uuid4().hex,'revision':0}
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if task.get('owner') and db.execute(
                    "SELECT count(*) FROM research_tasks WHERE json_extract(document,'$.owner')=?",
                    (task['owner'],)).fetchone()[0] >= 20:
                raise TaskLimit('Delete an old task before creating another')
            db.execute('INSERT INTO research_tasks VALUES(?,?,?)',(task['id'],0,json.dumps(task,ensure_ascii=False)))
        return task

    def list_owned(self, owner):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT revision,document FROM research_tasks WHERE json_extract(document,'$.owner')=? ORDER BY rowid DESC LIMIT 20", (owner,)).fetchall()
        return [{**json.loads(document),'revision':revision} for revision,document in rows]

    def get(self, identifier):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT revision,document FROM research_tasks WHERE id=?',(identifier,)).fetchone()
        if row is None:raise KeyError('Task not found')
        document=json.loads(row[1]);document['revision']=row[0]
        return document

    def save(self, task):
        revision=task['revision']
        with sqlite3.connect(self.path) as db:
            cursor=db.execute('UPDATE research_tasks SET revision=?,document=? WHERE id=? AND revision=?',
                (revision+1,json.dumps(task,ensure_ascii=False),task['id'],revision))
            if cursor.rowcount!=1:raise TaskConflict('Task changed; reload before updating')
        task['revision']=revision+1
        return task

    def delete(self, identifier, revision):
        with sqlite3.connect(self.path) as db:
            cursor=db.execute('DELETE FROM research_tasks WHERE id=? AND revision=?',(identifier,revision))
            if cursor.rowcount!=1:raise TaskConflict('Task changed or no longer exists')
