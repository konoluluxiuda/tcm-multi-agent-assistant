"""Offline core runtime bundles. Never overwrite a live state directory/database."""
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

FORMAT = 'tcm_core_runtime_backup_v1'
PG_TABLES = {'tcm_jobs.jobs','tcm_jobs.job_events','tcm_jobs.job_meta','tcm_jobs.checkpoint_bindings',
             'public.checkpoints','public.checkpoint_blobs','public.checkpoint_writes','public.checkpoint_migrations'}
ROLES = {'memory','projects','jobs','loops','plans','runs','evidence'}


class RuntimeBackupError(RuntimeError):
    """Public error code, without credentials, cookies or database contents."""


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def private_json(path, value):
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
        stream.flush();os.fsync(stream.fileno())


def sqlite_check(db,role):
    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RuntimeBackupError('invalid_sqlite')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if role=='memory':
        from agents.memory_backup import _validate
        _validate(db)
    elif role=='projects':
        if tables!={'projects'} or db.execute('PRAGMA application_id').fetchone()!=(1413696848,) or db.execute('PRAGMA user_version').fetchone()!=(1,):
            raise RuntimeBackupError('wrong_project_schema')
    elif role in ('loops','plans'):
        if tables!={'runs'} or db.execute('PRAGMA application_id').fetchone()!=(1413696849,):
            raise RuntimeBackupError('wrong_loop_schema')
        for raw, in db.execute('SELECT document FROM runs'):
            doc=json.loads(raw)
            if doc['phase'] not in ('ready','pending','terminal') or type(doc['revision']) is not int or doc['revision']<1:
                raise RuntimeBackupError('invalid_loop_document')
    elif role=='jobs':
        if tables!={'jobs','job_events','job_meta'} or db.execute('SELECT version FROM job_meta').fetchall()!=[(1,)]:
            raise RuntimeBackupError('wrong_job_schema')
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise RuntimeBackupError('active_jobs_stop_and_drain_first')
    elif role=='runs':
        if tables!={'run_meta','run_journal'} or db.execute('SELECT version FROM run_meta').fetchall()!=[(1,)]:
            raise RuntimeBackupError('wrong_run_schema')
        if db.execute("SELECT 1 FROM run_journal WHERE status='running' LIMIT 1").fetchone():
            raise RuntimeBackupError('active_runs_stop_and_drain_first')
    elif role=='evidence':
        from tcm.research_evidence_store import EvidenceStore,canonical
        if tables!={'evidence'} or db.execute('PRAGMA application_id').fetchone()!=(EvidenceStore.APP_ID,):
            raise RuntimeBackupError('wrong_evidence_schema')
        for sha,raw in db.execute('SELECT digest,payload FROM evidence'):
            if len(raw.encode())>4_000_000 or hashlib.sha256(raw.encode()).hexdigest()!=sha:
                raise RuntimeBackupError('invalid_evidence_content')
            EvidenceStore.checked(json.loads(raw))
    else:raise RuntimeBackupError('unknown_sqlite_role')
    return {table:db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0] for table in sorted(tables)}


def check_evidence_references(folder,names):
    """Validate only handles already published by the two execution stores."""
    from tcm.research_evidence_store import EvidenceStore,EvidencePolicy
    reader=object.__new__(EvidenceStore)
    reader.path=Path(folder)/'evidence.sqlite3';reader.policy=EvidencePolicy()
    for role,table in (('runs','run_journal'),('loops','runs'),('plans','runs'),('jobs','jobs')):
        if role+'.sqlite3' not in names:continue
        with closing(sqlite3.connect(Path(folder)/(role+'.sqlite3'))) as db:
            for run_id,owner,raw in db.execute('SELECT id,owner,document FROM '+table):
                doc=json.loads(raw)
                if role=='jobs' and doc.get('recovery')=='journal' and doc['status']=='interrupted' and 'plans.sqlite3' not in names:
                    raise RuntimeBackupError('interrupted_plan_journal_required')
                candidates=([doc.get('snapshot',{}),(doc.get('result') or {}).get('run',{})]
                            if role in ('runs','jobs') else [doc['state']])
                for state in candidates:
                    for eid,payload in state.get('evidence',{}).items():
                        if payload.get('externalized'):
                            if 'evidence.sqlite3' not in names:raise RuntimeBackupError('referenced_evidence_required')
                            try:reader.load(owner,run_id,eid,payload)
                            except Exception:raise RuntimeBackupError('published_evidence_unavailable') from None


def sqlite_copy(source,target,role):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as src:
        sqlite_check(src,role)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        with closing(sqlite3.connect(target)) as dest:
            start=time.monotonic()
            def progress(*args):
                if time.monotonic()-start>30:raise RuntimeBackupError('sqlite_snapshot_timeout')
            src.backup(dest,pages=256,progress=progress,sleep=.05)
            dest.execute('PRAGMA journal_mode=DELETE')
            return sqlite_check(dest,role)


def pg_env(dsn):
    from psycopg.conninfo import conninfo_to_dict
    # Never put credentials in command arguments, receipts, errors or inherited PG settings.
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LD_LIBRARY_PATH','SYSTEMROOT')}
    mapping={'host':'PGHOST','hostaddr':'PGHOSTADDR','port':'PGPORT','user':'PGUSER','password':'PGPASSWORD',
             'dbname':'PGDATABASE','sslmode':'PGSSLMODE','sslrootcert':'PGSSLROOTCERT','sslcert':'PGSSLCERT',
             'sslkey':'PGSSLKEY','connect_timeout':'PGCONNECT_TIMEOUT'}
    options=conninfo_to_dict(dsn)
    if set(options)-set(mapping):raise RuntimeBackupError('unsupported_dsn_options')
    env.update({mapping[k]:v for k,v in options.items()});env.setdefault('PGCONNECT_TIMEOUT','5')
    return env


def pg_tables(db):
    return {s+'.'+t for s,t in db.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')")}


def pg_check(db):
    if pg_tables(db)!=PG_TABLES:raise RuntimeBackupError('dedicated_checkpoint_database_required')
    if db.execute('SELECT version FROM tcm_jobs.job_meta').fetchall()!=[(1,)]:raise RuntimeBackupError('wrong_pg_schema')
    if db.execute("SELECT 1 FROM tcm_jobs.jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise RuntimeBackupError('active_jobs_stop_and_drain_first')
    return {table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in sorted(PG_TABLES)}


def pg_command(binary,args,dsn):
    result=subprocess.run([str(binary),*args],env=pg_env(dsn),stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
    if result.returncode:raise RuntimeBackupError('postgres_command_failed')


def create_bundle(files,out,*,workers_stopped=False,dsn=None,pg_bin=Path('/usr/bin'),cookie_name='research_chat_session'):
    if not workers_stopped:raise RuntimeBackupError('stop_all_runtime_writers_first')
    if not isinstance(cookie_name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',cookie_name):
        raise RuntimeBackupError('invalid_cookie_name')
    if not {'memory','projects'}<=set(files) or set(files)-ROLES or bool(dsn)==('jobs' in files):
        raise RuntimeBackupError('require_memory_projects_and_one_job_backend')
    if len({Path(p).resolve() for p in files.values()})!=len(files):raise RuntimeBackupError('duplicate_source')
    out=Path(out);owned=False
    try:
        out.mkdir(mode=0o700,parents=True,exist_ok=False);owned=True
        with ExitStack() as stack:
            # Freeze SQLite writers while taking all component snapshots. Do not use file copies of WAL databases.
            for path in files.values():
                path=Path(path)
                if path.is_symlink():raise RuntimeBackupError('symlink_source_rejected')
                db=stack.enter_context(closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=5)))
                db.execute('BEGIN IMMEDIATE')
            pg=None
            if dsn:
                import psycopg
                pg=stack.enter_context(psycopg.connect(dsn,connect_timeout=5))
                pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                pg.execute("SET LOCAL lock_timeout='5s'")
                pg_check(pg)
                for table in sorted(PG_TABLES):pg.execute('LOCK TABLE '+table+' IN SHARE MODE')
            entries={}
            for role,path in files.items():
                target=out/(role+'.sqlite3');counts=sqlite_copy(path,target,role)
                entries[target.name]={'sha256':digest(target),'rows':counts}
            if pg:
                counts=pg_check(pg);snapshot=pg.execute('SELECT pg_export_snapshot()').fetchone()[0]
                target=out/'runtime.dump'
                pg_command(Path(pg_bin)/'pg_dump',['--format=custom','--no-owner','--no-acl','--snapshot='+snapshot,'--file='+str(target)],dsn)
                target.chmod(0o600);entries[target.name]={'sha256':digest(target),'rows':counts}
            check_evidence_references(out,entries)
            manifest={'format':FORMAT,'created_at':datetime.now(timezone.utc).isoformat(),'files':entries,
                      'cookie_name':cookie_name,'backend':'postgres' if dsn else 'sqlite',
                      'scope':'core_only: conversations, project memory, jobs/events and postgres checkpoints',
                      'excluded':['source catalogs','PDFs','model assets','legacy research tasks/history','bookmarks','PubMed cache']+([] if 'loops' in files else ['durable loop journals'])+([] if 'plans' in files else ['chat plan journals'])+([] if 'runs' in files else ['structured run history'])+([] if 'evidence' in files else ['externalized evidence']),
                      'consistency':'offline_writers_stopped; sqlite writer locks; postgres shared locks and exported snapshot'}
            private_json(out/'manifest.json',manifest)
        return manifest
    except Exception as exc:
        if owned:shutil.rmtree(out)
        raise RuntimeBackupError(str(exc) if isinstance(exc,RuntimeBackupError) else 'backup_failed') from None


def inspect_bundle(bundle):
    bundle=Path(bundle)
    try:
        path=bundle/'manifest.json'
        if bundle.is_symlink() or path.is_symlink() or path.stat().st_size>65536:raise RuntimeBackupError('invalid_bundle')
        manifest=json.loads(path.read_text())
        expected={'memory.sqlite3','projects.sqlite3', 'runtime.dump' if manifest['backend']=='postgres' else 'jobs.sqlite3'}
        if 'loops.sqlite3' in manifest['files']:expected.add('loops.sqlite3')
        for name in ('runs.sqlite3','plans.sqlite3','evidence.sqlite3'):
            if name in manifest['files']:expected.add(name)
        if manifest['format']!=FORMAT or manifest['backend'] not in ('postgres','sqlite') or set(manifest['files'])!=expected:
            raise RuntimeBackupError('invalid_manifest')
        if not isinstance(manifest['cookie_name'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',manifest['cookie_name']):
            raise RuntimeBackupError('invalid_cookie_name')
        for name,item in manifest['files'].items():
            path=bundle/name
            if path.is_symlink() or not path.is_file() or digest(path)!=item['sha256']:raise RuntimeBackupError('checksum_mismatch')
            if name.endswith('.sqlite3'):
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    if sqlite_check(db,name[:-8])!=item['rows']:raise RuntimeBackupError('row_count_mismatch')
        check_evidence_references(bundle,manifest['files'])
        return manifest
    except Exception as exc:
        raise RuntimeBackupError(str(exc) if isinstance(exc,RuntimeBackupError) else 'invalid_bundle') from None


def restore_bundle(bundle,target,*,dsn=None,pg_bin=Path('/usr/bin')):
    manifest=inspect_bundle(bundle);bundle=Path(bundle);target=Path(target)
    if (manifest['backend']=='postgres')!=bool(dsn):raise RuntimeBackupError('restore_backend_mismatch')
    owned=False
    try:
        target.mkdir(mode=0o700,parents=True,exist_ok=False);owned=True
        if dsn:
            import psycopg
            with psycopg.connect(dsn,connect_timeout=5) as db:
                # Reject tables AND views/functions/types before invoking pg_restore.
                objects=db.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'").fetchone()[0]
                namespaces={r[0] for r in db.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'")}
                functions=db.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'").fetchone()[0]
                types=db.execute("SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'").fetchone()[0]
                if objects or functions or types or namespaces!={'public'}:raise RuntimeBackupError('restore_database_must_be_empty')
        for name in manifest['files']:
            if name.endswith('.sqlite3'):sqlite_copy(bundle/name,target/name,name[:-8])
        if dsn:
            pg_command(Path(pg_bin)/'pg_restore',['--single-transaction','--exit-on-error','--no-owner','--no-acl','--dbname='+pg_env(dsn)['PGDATABASE'],str(bundle/'runtime.dump')],dsn)
            with psycopg.connect(dsn,connect_timeout=5) as db:
                if pg_check(db)!=manifest['files']['runtime.dump']['rows']:raise RuntimeBackupError('restored_pg_counts_mismatch')
        receipt={'status':'restored','backend':manifest['backend'],'cookie_name':manifest['cookie_name'],
                 'source_manifest_sha256':digest(bundle/'manifest.json'),'auto_started':False}
        private_json(target/'restore.json',receipt)
        return receipt
    except Exception as exc:
        if owned:shutil.rmtree(target)
        # Never drop a database on failure. A completed pg_restore may remain if later validation fails.
        raise RuntimeBackupError(str(exc) if isinstance(exc,RuntimeBackupError) else 'restore_failed_check_isolated_target') from None
