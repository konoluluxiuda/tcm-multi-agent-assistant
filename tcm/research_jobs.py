"""Durable local job queue and replayable events; no provider calls or checkpoint replay."""
import asyncio
from contextlib import closing,suppress
import hashlib
import json
import os
import sqlite3
import time
from uuid import uuid4

from tcm.research_run_local import run_local_profile

TERMINAL={'complete','partial','needs_input','failed','cancelled','interrupted'}


class JobConflict(RuntimeError):pass
class JobBusy(RuntimeError):pass


class JobStore:
    """Separate queue database keeps the existing run journal schema untouched."""
    def __init__(self,path, *, clock=time.time):
        from pathlib import Path
        self.path=Path(path);self.clock=clock
        if self.path.is_symlink():raise ValueError('Queue cannot be a symlink')
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if tables!={'job_meta','jobs','job_events'} or db.execute('SELECT version FROM job_meta').fetchall()!=[(1,)]:
                    raise ValueError('Not a job database')
            else:
                db.execute('CREATE TABLE job_meta(version INTEGER NOT NULL)');db.execute('INSERT INTO job_meta VALUES(1)')
                db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL, worker TEXT, lease REAL, cancel INTEGER NOT NULL, document TEXT NOT NULL, UNIQUE(owner,request_key))')
                db.execute('CREATE TABLE job_events(job_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,sequence))')
                db.execute('CREATE INDEX jobs_claim ON jobs(status,updated)')
        os.chmod(self.path,0o600)

    @staticmethod
    def event(db,doc):
        snapshot=doc.get('snapshot') or {}
        data={k:doc[k] for k in ('id','revision','status','updated')}
        data.update(node=snapshot.get('last_node'),model_calls=snapshot.get('model_calls',0),tool_calls=snapshot.get('tool_calls',0),
                    latest_action=(snapshot.get('events') or [None])[-1],terminal=doc['status'] in TERMINAL)
        db.execute('INSERT INTO job_events VALUES(?,?,?)',(doc['id'],doc['revision'],json.dumps(data,ensure_ascii=False)))

    def enqueue(self,owner,question,key,*,payload=None):
        fingerprint=hashlib.sha256((question if payload is None else json.dumps({'question':question,'payload':payload},sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT fingerprint,document FROM jobs WHERE owner=? AND request_key=?',(owner,key)).fetchone()
            if row:
                if row[0]!=fingerprint:raise JobConflict('Idempotency key used for another question')
                return json.loads(row[1]),False
            if db.execute("SELECT count(*) FROM jobs WHERE owner=? AND status IN ('queued','running')",(owner,)).fetchone()[0]:
                raise JobBusy('An active job already exists')
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=20:
                raise JobBusy('Queue full')
            doc={'id':uuid4().hex,'question':question,'revision':0,'status':'queued','updated':self.clock(),
                 'snapshot':{},'result':None,'reason':'','recovery':'new_run_only'}
            if payload is not None:doc['payload']=payload
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(doc['id'],owner,key,fingerprint,0,'queued',doc['updated'],None,None,0,json.dumps(doc)))
            self.event(db,doc)
        return doc,True

    def get(self,owner,identifier):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT document FROM jobs WHERE owner=? AND id=?',(owner,identifier)).fetchone()
        if not row:raise KeyError(identifier)
        return json.loads(row[0])

    def list(self,owner):
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT document FROM jobs WHERE owner=? ORDER BY updated DESC,id LIMIT 20',(owner,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def claim(self,worker):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            # Expired workers are fenced off; uncertain work is never silently retried.
            for identifier,raw in db.execute("SELECT id,document FROM jobs WHERE status='running' AND lease<?",(self.clock(),)).fetchall():
                doc=json.loads(raw);doc.update(status='interrupted',reason='lease_expired',revision=doc['revision']+1,updated=self.clock())
                db.execute('UPDATE jobs SET status=?,revision=?,updated=?,document=? WHERE id=?',('interrupted',doc['revision'],doc['updated'],json.dumps(doc),identifier));self.event(db,doc)
            if db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]>=4:return None
            row=db.execute("SELECT owner,document FROM jobs WHERE status='queued' ORDER BY updated,id LIMIT 1").fetchone()
            if not row:return None
            doc=json.loads(row[1]);doc.update(status='running',revision=doc['revision']+1,updated=self.clock())
            db.execute('UPDATE jobs SET status=?,revision=?,updated=?,worker=?,lease=?,document=? WHERE id=?',('running',doc['revision'],doc['updated'],worker,self.clock()+30,json.dumps(doc),doc['id']))
            self.event(db,doc);return row[0],doc

    def heartbeat(self,identifier,worker):
        with closing(sqlite3.connect(self.path)) as db,db:
            changed=db.execute("UPDATE jobs SET lease=? WHERE id=? AND worker=? AND status='running' AND lease>=?",(self.clock()+30,identifier,worker,self.clock())).rowcount
            if changed!=1:raise JobConflict('Lease lost')
            return bool(db.execute('SELECT cancel FROM jobs WHERE id=?',(identifier,)).fetchone()[0])

    def save(self,owner,doc,worker,*,snapshot=None,result=None,status='running',reason=''):
        if status not in TERMINAL|{'running'}:raise ValueError('Invalid status')
        updated={**doc,'revision':doc['revision']+1,'status':status,'reason':reason,'updated':self.clock()}
        if snapshot is not None:updated['snapshot']=snapshot
        if result is not None:updated['result']=result
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            # Cancellation wins over a concurrent completion commit.
            row=db.execute("SELECT cancel FROM jobs WHERE id=? AND owner=? AND worker=? AND status='running' AND revision=? AND lease>=?",(doc['id'],owner,worker,doc['revision'],self.clock())).fetchone()
            if row is None:raise JobConflict('Lease or revision lost')
            if row[0] and status!='running':updated.update(status='cancelled',reason='cancel_requested',result=None)
            raw=json.dumps(updated,ensure_ascii=False)
            if len(raw.encode())>2_000_000:raise ValueError('Job result too large')
            db.execute('UPDATE jobs SET revision=?,status=?,updated=?,document=? WHERE id=?',(updated['revision'],updated['status'],updated['updated'],raw,doc['id']))
            self.event(db,updated)
        return updated

    def cancel(self,owner,identifier):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM jobs WHERE id=? AND owner=?',(identifier,owner)).fetchone()
            if not row:raise KeyError(identifier)
            doc=json.loads(row[0])
            if doc['status']=='queued':
                doc.update(status='cancelled',reason='cancel_requested',revision=doc['revision']+1,updated=self.clock())
                db.execute('UPDATE jobs SET status=?,revision=?,updated=?,cancel=1,document=? WHERE id=?',('cancelled',doc['revision'],doc['updated'],json.dumps(doc),identifier));self.event(db,doc)
            elif doc['status']=='running':db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(identifier,))
            return {'id':identifier,'status':doc['status'],'cancel_requested':doc['status'] not in TERMINAL or doc['status']=='cancelled'}

    def events(self,owner,identifier,after):
        doc=self.get(owner,identifier)
        if after>doc['revision']:raise JobConflict('Event cursor exceeds revision')
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT payload FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT 100',(identifier,after)).fetchall()
        return doc,[json.loads(r[0]) for r in rows]

    def delete(self,owner,identifier):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM jobs WHERE id=? AND owner=?',(identifier,owner)).fetchone()
            if not row:raise KeyError(identifier)
            if row[0] not in TERMINAL:raise JobConflict('Active job cannot be deleted')
            db.execute('DELETE FROM job_events WHERE job_id=?',(identifier,));db.execute('DELETE FROM jobs WHERE id=?',(identifier,))


class ResearchWorker:
    def __init__(self,store_factory,database,runner=run_local_profile):
        self.store_factory=store_factory;self.database=database;self.runner=runner
        self.identity=uuid4().hex;self.loop_task=None;self.tasks=set();self.closing=False

    def start(self):
        if self.loop_task is None or self.loop_task.done():
            self.closing=False;self.loop_task=asyncio.create_task(self.loop())

    async def loop(self):
        while not self.closing:
            try:
                if len(self.tasks)<4:
                    store=self.store_factory();claimed=store.claim(self.identity)
                    if claimed:
                        task=asyncio.create_task(self.execute(store,*claimed));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
            except Exception:
                # Storage failure is not permission to execute an unrecorded job.
                pass
            await asyncio.sleep(.2)

    async def execute(self,store,owner,doc):
        work=None;monitor=None;lost_lease=False
        async def persist(snapshot):
            nonlocal doc
            doc=store.save(owner,doc,self.identity,snapshot=snapshot)
        async def watch():
            nonlocal lost_lease
            while True:
                await asyncio.sleep(.2)
                try:cancel=store.heartbeat(doc['id'],self.identity)
                except Exception:
                    lost_lease=True;work.cancel();return
                if cancel:work.cancel();return
        try:
            if doc.get('payload',{}).get('kind')=='cross_source':
                from tcm.cross_source_report import run_report
                work=asyncio.create_task(run_report(doc['payload'],on_state=persist))
            elif doc.get('payload',{}).get('kind')=='pair_report':
                from tcm.pair_research_report import run_pair_report
                work=asyncio.create_task(run_pair_report(doc['payload'],on_state=persist))
            else:
                work=asyncio.create_task(self.runner(self.database(),doc['question'],on_state=persist))
            monitor=asyncio.create_task(watch())
            result=await asyncio.wait_for(work,timeout=125)
            doc=store.save(owner,doc,self.identity,result=result,status=result['status'])
        except asyncio.CancelledError:
            with suppress(Exception):doc=store.save(owner,doc,self.identity,status='interrupted' if self.closing or lost_lease else 'cancelled',reason='worker_shutdown' if self.closing else 'lease_or_storage_lost' if lost_lease else 'cancel_requested')
        except Exception:
            with suppress(Exception):doc=store.save(owner,doc,self.identity,status='failed',reason='local_execution_failed')
        finally:
            for task in (work,monitor):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError,Exception):await task

    async def stop(self):
        self.closing=True
        if self.loop_task:
            self.loop_task.cancel()
            with suppress(asyncio.CancelledError):await self.loop_task
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
