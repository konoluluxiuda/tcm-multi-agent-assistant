"""Optional PostgreSQL queue using the existing job contract and fenced transactions."""
import time
import hashlib
import json
from contextlib import closing
import psycopg
from tcm.research_jobs import JobStore, JobConflict, TERMINAL

LOCK = 70420261026


class Connection:
    def __init__(self,dsn):
        self.db=psycopg.connect(dsn,autocommit=True,options='-c search_path=tcm_jobs,public',connect_timeout=5)
        self.transaction=None
    def __enter__(self):
        self.transaction=self.db.transaction();self.transaction.__enter__()
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
        return self
    def __exit__(self,*args):return self.transaction.__exit__(*args)
    def execute(self,sql,params=()):
        if sql=='BEGIN IMMEDIATE':return self.db.execute('SELECT 1')
        return self.db.execute(sql.replace('?', '%s'),params)
    def close(self):self.db.close()


class PostgresJobStore(JobStore):
    def __init__(self,dsn,*,clock=time.time):
        self.dsn=dsn;self.clock=clock
        with psycopg.connect(dsn,connect_timeout=5) as db:
            if db.execute('SELECT version FROM tcm_jobs.job_meta').fetchall()!=[(1,)]:
                raise ValueError('Unsupported PostgreSQL job schema; explicit migration required')
    def connect(self):return Connection(self.dsn)
    def delete(self,owner,identifier):
        with closing(self.connect()) as db,db:
            row=db.execute('SELECT document FROM jobs WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if not row:raise KeyError(identifier)
            doc=json.loads(row[0])
            if doc['status'] not in TERMINAL:raise JobConflict('Active job cannot be deleted')
            if doc.get('recovery')=='checkpoint':
                thread=hashlib.sha256((owner+':'+identifier).encode()).hexdigest()
                if not db.execute('SELECT pg_try_advisory_xact_lock(%s)',(int(thread[:15],16),)).fetchone()[0]:
                    raise JobConflict('Checkpoint still running')
                for table in ('checkpoint_writes','checkpoint_blobs','checkpoints'):
                    db.execute('DELETE FROM public.'+table+' WHERE thread_id=%s',(thread,))
                db.execute('DELETE FROM checkpoint_bindings WHERE thread_id=%s',(thread,))
            db.execute('DELETE FROM job_events WHERE job_id=?',(identifier,))
            db.execute('DELETE FROM jobs WHERE id=?',(identifier,))



def migrate(dsn):
    with psycopg.connect(dsn,connect_timeout=5) as db:
        db.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
        db.execute('CREATE SCHEMA IF NOT EXISTS tcm_jobs')
        db.execute('SET LOCAL search_path=tcm_jobs,public')
        db.execute('CREATE TABLE IF NOT EXISTS job_meta(version INTEGER NOT NULL)')
        versions=db.execute('SELECT version FROM job_meta').fetchall()
        if versions:
            if versions!=[(1,)]:raise ValueError('Unsupported migration version')
            return
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, revision BIGINT NOT NULL, status TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL, worker TEXT, lease DOUBLE PRECISION, cancel INTEGER NOT NULL, document TEXT NOT NULL, UNIQUE(owner,request_key))')
        db.execute('CREATE TABLE job_events(job_id TEXT NOT NULL, sequence BIGINT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,sequence))')
        db.execute('CREATE INDEX jobs_claim ON jobs(status,updated)')
        db.execute('INSERT INTO job_meta VALUES(1)')
