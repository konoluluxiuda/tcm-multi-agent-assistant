"""Private JSON journal for bounded tool loops; uncertain dispatches never auto-replay."""
from contextlib import contextmanager, closing
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from langchain_core.messages import messages_to_dict, messages_from_dict
from tcm.research_run_local import run_local_profile


class RunConflict(RuntimeError):pass


class RunJournal:
    def __init__(self,path,owner,identifier,contract,*,resume=False,expected_revision=None,guard=None):
        if not owner or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',identifier) or not contract:
            raise ValueError('Private owner, run id and execution contract required')
        self.path=Path(path);self.owner=owner;self.identifier=identifier;self.contract=contract;self.resume=resume
        self.record=None;self.binding=None;self.expected_revision=expected_revision
        self.guard=guard

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.path.is_symlink():raise RunConflict('Symlink journal rejected')
        locks=self.path.with_name(self.path.name+'.locks');locks.mkdir(mode=0o700,exist_ok=True)
        if locks.is_symlink():raise RunConflict('Symlink lock directory rejected')
        key=hashlib.sha256(self.identifier.encode()).hexdigest()
        fd=os.open(locks/key,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RunConflict('Run already executing') from None
            fd_db=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);os.close(fd_db)
            with closing(sqlite3.connect(self.path)) as db,db:
                version=db.execute('PRAGMA application_id').fetchone()[0]
                tables=db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if (version==0 and tables) or version not in (0,1413696849):raise RunConflict('Wrong journal database')
                db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,binding TEXT NOT NULL,document TEXT NOT NULL)')
                db.execute('PRAGMA application_id=1413696849')
            yield self
        finally:os.close(fd)

    def load(self,initial,binding,seconds):
        self.binding=binding
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT owner,binding,document FROM runs WHERE id=?',(self.identifier,)).fetchone()
        if row:
            if row[0]!=self.owner:raise KeyError(self.identifier)
            if not self.resume:raise RunConflict('Run exists; explicit resume required')
            if row[1]!=binding:raise RunConflict('Question, source or execution contract changed')
            self.record=json.loads(row[2])
            if self.expected_revision is not None and self.record['revision']!=self.expected_revision:
                raise RunConflict('Run changed; refresh before resuming')
            state=self.decode(self.record['state'])
            if self.record['phase']=='pending':
                state.update(status='partial',reason='uncertain_'+self.record['operation']+'_dispatch')
                self.save(state,'terminal')
            elif self.record['phase']!='terminal' and time.time()>=self.record['expires_at']:
                state.update(status='partial',reason='time_budget');self.save(state,'terminal')
            return state,self.record['next_node'],max(0,self.record['expires_at']-time.time())
        if self.resume:raise KeyError(self.identifier)
        self.record={'expires_at':time.time()+seconds,'revision':0}
        self.save(initial,'ready',next_node='decide')
        return initial,'decide',seconds

    @staticmethod
    def decode(state):
        return {**state,'messages':messages_from_dict(state['messages'])}

    def save(self,state,phase,*,next_node='decide',operation=None):
        if self.guard is not None:self.guard()
        record={**self.record,'phase':phase,'next_node':next_node,'operation':operation,
                'revision':self.record['revision']+1,
                'state':{**state,'messages':messages_to_dict(state['messages'])}}
        raw=json.dumps(record,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>2_000_000:raise RunConflict('Journal state exceeds bound')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if self.record['revision']==0 and db.execute('SELECT count(*) FROM runs WHERE owner=?',(self.owner,)).fetchone()[0]>=100:
                raise RunConflict('Delete an old run before creating another')
            db.execute('INSERT INTO runs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                       (self.identifier,self.owner,self.binding,raw))
        self.record=record


async def run_durable(question,sdk,backend,*,path,owner,identifier,contract,resume=False,expected_revision=None,**kwargs):
    """Same runtime, opt-in persistence. Caller binds model/source/tokenizer identity in contract."""
    from tcm.research_run import run
    from tcm.research_evidence_store import for_run
    if kwargs.get('scope') is not None and 'evidence_store' not in kwargs:
        kwargs['evidence_store']=for_run(path,owner,identifier)
    journal=RunJournal(path,owner,identifier,contract,resume=resume,expected_revision=expected_revision)
    with journal.locked():return await run(question,sdk,backend,journal=journal,**kwargs)


async def local_profile(store, owner, path, disease):
    """Compatibility audit wrapper for explicit local runs; no checkpoint auto-resume."""
    doc = store.create(owner, disease)
    async def persist(snapshot):
        nonlocal doc
        doc = store.save(owner, doc, snapshot=snapshot)
    try:
        result = await run_local_profile(path, disease, on_state=persist)
        doc = store.save(owner, doc, result=result, status=result['status'])
    except asyncio.CancelledError:
        store.save(owner, doc, status='cancelled')
        raise
    except Exception:
        store.save(owner, doc, status='failed')
        raise
    return {'run_id': doc['id'], 'revision': doc['revision'], **result}
