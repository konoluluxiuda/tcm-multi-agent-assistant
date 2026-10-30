"""Owner-scoped public views and fenced deletion for private loop journals."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tcm.research_run_durable import RunJournal,RunConflict


def records(path,owner,identifier=None):
    path=Path(path)
    if not path.exists():return []
    if path.is_symlink():raise RunConflict('Symlink journal rejected')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA application_id').fetchone()!=(1413696849,):raise RunConflict('Wrong journal database')
        rows=db.execute('SELECT id,document FROM runs WHERE owner=?'+(' AND id=?' if identifier else '')+' ORDER BY rowid DESC LIMIT 100',
                        (owner,identifier) if identifier else (owner,)).fetchall()
    return [(key,json.loads(raw)) for key,raw in rows]


def view(identifier,doc):
    state=doc['state']
    # Never return prompts, raw replies, tool arguments, bindings or signing material.
    return {'id':identifier,'revision':doc['revision'],'phase':doc['phase'],
            'status':state['status'],'reason':state['reason'],'expires_at':doc['expires_at'],
            'model_calls':state['model_calls'],'tool_calls':state['tool_calls'],
            'estimated_input_units':state['estimated_input_units'],
            'evidence_count':len(state['evidence']),'mode':'synthetic_offline_model'}


def get(path,owner,identifier):
    rows=records(path,owner,identifier)
    if not rows:raise KeyError(identifier)
    return view(*rows[0])


def delete(path,owner,identifier,revision):
    # Same process lock as execution; BEGIN IMMEDIATE also fences backup/writers.
    get(path,owner,identifier)
    with RunJournal(path,owner,identifier,'lifecycle').locked():
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('PRAGMA secure_delete=ON');db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM runs WHERE id=? AND owner=?',(identifier,owner)).fetchone()
            if not row:raise KeyError(identifier)
            if json.loads(row[0])['revision']!=revision:raise RunConflict('Run changed; refresh before deleting')
            db.execute('DELETE FROM runs WHERE id=? AND owner=?',(identifier,owner))
    # Keep the empty lock inode: unlinking it could allow two distinct process locks.
