"""Synthetic isolated PostgreSQL backup/restore and unfinished pair checkpoint recovery."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import psycopg
from psycopg import sql
from agents.conversation_store import ConversationStore
from tcm.project_memory import ProjectStore,ProjectConflict
from tcm.project_semantic import owner_key
from tcm.postgres_jobs import PostgresJobStore
from tcm.report_checkpoint import run_checkpoint_report
from tcm.runtime_backup import create_bundle,restore_bundle,inspect_bundle,RuntimeBackupError,digest
from tests.test_pair_research_report import report,targets


async def execute(doc,owner,out,*,pause=False):
    async def backend(branch,herb,question):
        with (out/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':herb+'_'+branch})+'\n')
        if pause and herb=='b' and branch=='structured':await asyncio.Event().wait()
        return report(herb)[branch]
    async def target(branch,herb,question):
        with (out/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':'target'})+'\n')
        return targets(json.loads(question))
    return await run_checkpoint_report(owner,doc,backend=backend,target_backend=target,fingerprint='backup-synthetic-v1')


async def resume_child(args):
    cookie=sys.stdin.read().strip();owner=owner_key(cookie)
    memory=ConversationStore(args.out/'restored/memory.sqlite3')
    assert memory.valid_cookie(cookie)
    store=PostgresJobStore(os.environ['RESEARCH_JOB_DSN'])
    doc=store.list(owner)[0];store.resume(owner,doc['id']);_,doc=store.claim('restored-worker')
    result=await execute(doc,owner,args.out)
    store.save(owner,doc,'restored-worker',status=result['status'],result=result)
    print('restored_checkpoint_completed')


async def main(args):
    if args.resume:
        await resume_child(args);return
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    admin=os.environ['BACKUP_TEST_ADMIN_DSN']
    base=psycopg.conninfo.conninfo_to_dict(admin)
    name='tcm_backup_'+uuid.uuid4().hex[:10]
    names=[name+'_source',name+'_restored']
    dsns=[]
    with psycopg.connect(admin,autocommit=True) as db:
        for database in names:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            dsns.append(psycopg.conninfo.make_conninfo(**{**base,'dbname':database}))
    source,target=dsns
    os.environ['RESEARCH_JOB_DSN']=os.environ['RESEARCH_CHECKPOINT_DSN']=source
    from scripts.migrate_research_postgres import main as migrate
    await migrate()
    memory_path=args.out/'source/memory.sqlite3';project_path=args.out/'source/projects.sqlite3'
    memory=ConversationStore(memory_path);cookie=memory.new_cookie();owner=owner_key(cookie)
    memory.save_serialized(cookie,[{'type':'human','data':{'content':'synthetic study'}}],{'kind':'fixture'},revision=0)
    projects=ProjectStore(project_path);project=projects.save(owner,{'title':'合成两药研究','herbs':['a','b'],'question':'功能'})
    project=projects.save(owner,{'title':'合成两药研究','herbs':['a','b'],'question':'功能及出处'},identifier=project['id'],revision=1)
    jobs=PostgresJobStore(source);doc,_=jobs.enqueue(owner,'合成比较','backup-fixture',payload={'kind':'pair_report','herbs':['a','b'],'question':'功能'})
    _,doc=jobs.claim('source-worker');doc=jobs.save(owner,{**doc,'recovery':'checkpoint'},'source-worker')
    try:
        await asyncio.to_thread(create_bundle,{'memory':memory_path,'projects':project_path},args.out/'active-refused',
                                workers_stopped=True,dsn=source,pg_bin=args.pg_bin)
        raise AssertionError('Running queue backed up')
    except RuntimeBackupError as exc:assert str(exc)=='active_jobs_stop_and_drain_first'
    task=asyncio.create_task(execute(doc,owner,args.out,pause=True))
    thread=hashlib.sha256((owner+':'+doc['id']).encode()).hexdigest()
    try:
        for _ in range(200):
            with psycopg.connect(source) as db:
                channels={r[0] for r in db.execute('SELECT channel FROM checkpoint_writes WHERE thread_id=%s',(thread,))}
            if {'left_text','left_structured','right_text'}<=channels:break
            if task.done():await task;raise AssertionError('Unexpected completion')
            await asyncio.sleep(.05)
        else:raise AssertionError('No pending checkpoint')
    finally:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
    doc=jobs.save(owner,doc,'source-worker',status='interrupted')
    files={'memory':memory_path,'projects':project_path};before={k:digest(p) for k,p in files.items()}
    bundle=args.out/'bundle'
    manifest=await asyncio.to_thread(create_bundle,files,bundle,workers_stopped=True,dsn=source,pg_bin=args.pg_bin,cookie_name='backup_fixture_session')
    assert inspect_bundle(bundle)==manifest
    assert before=={k:digest(p) for k,p in files.items()}
    # A populated source database must never become a restore target.
    try:
        await asyncio.to_thread(restore_bundle,bundle,args.out/'refused',dsn=source,pg_bin=args.pg_bin)
        raise AssertionError('Existing database overwritten')
    except RuntimeBackupError as exc:assert str(exc)=='restore_database_must_be_empty'
    restored=await asyncio.to_thread(restore_bundle,bundle,args.out/'restored',dsn=target,pg_bin=args.pg_bin)
    restored_memory=ConversationStore(args.out/'restored/memory.sqlite3')
    assert restored_memory.valid_cookie(cookie)
    assert restored_memory.load_serialized(cookie).messages==memory.load_serialized(cookie).messages
    restored_projects=ProjectStore(args.out/'restored/projects.sqlite3')
    assert restored_projects.get(owner,project['id'])==project
    try:restored_projects.get('other-owner',project['id']);raise AssertionError('Owner bypass')
    except KeyError:pass
    try:restored_projects.save(owner,{'title':'x','herbs':['a'],'question':'q'},identifier=project['id'],revision=1);raise AssertionError('Revision bypass')
    except ProjectConflict:pass
    target_jobs=PostgresJobStore(target)
    assert target_jobs.get(owner,doc['id'])==doc
    assert target_jobs.events(owner,doc['id'],-1)==jobs.events(owner,doc['id'],-1)
    try:target_jobs.resume('other-owner',doc['id']);raise AssertionError('Job owner bypass')
    except KeyError:pass
    with psycopg.connect(target) as db:
        binding=db.execute('SELECT fingerprint FROM tcm_jobs.checkpoint_bindings WHERE thread_id=%s',(thread,)).fetchone()
    with psycopg.connect(source) as db:
        assert binding==db.execute('SELECT fingerprint FROM tcm_jobs.checkpoint_bindings WHERE thread_id=%s',(thread,)).fetchone()
    env={**os.environ,'RESEARCH_JOB_DSN':target,'RESEARCH_CHECKPOINT_DSN':target}
    child=await asyncio.create_subprocess_exec(sys.executable,'-m','scripts.check_runtime_backup','--out',str(args.out),'--resume',env=env,
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:stdout,stderr=await asyncio.wait_for(child.communicate(cookie.encode()),30)
    finally:
        if child.returncode is None:child.kill();await child.wait()
    assert child.returncode==0,stderr.decode()
    final=target_jobs.get(owner,doc['id']);assert final['status']=='complete'
    assert final['result']['targets']['items'][0]['sources'][0]['witnesses'][0]['source_pointer']=='/x'
    assert jobs.get(owner,doc['id'])['status']=='interrupted'
    counts=Counter(json.loads(x)['branch'] for x in (args.out/'calls.jsonl').read_text().splitlines())
    assert counts=={'a_text':1,'a_structured':1,'b_text':1,'b_structured':2,'target':1},counts
    receipt={'status':'passed','data':'synthetic_only','external_calls':0,'databases':names,
             'session_signing_key_continuity':True,'project_revision':2,'owner_isolation':True,
             'job_events_equal_before_resume':True,'source_unchanged':True,'nonempty_target_refused':True,
             'active_job_backup_refused':True,
             'checkpoint_binding_preserved':True,'new_process_checkpoint_resumed':True,'source_pointers_preserved':True,
             'branch_calls':dict(counts),'bundle_manifest_sha256':digest(bundle/'manifest.json')}
    (args.out/'summary.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--pg-bin',type=Path,default=Path('/usr/bin'));p.add_argument('--resume',action='store_true')
    asyncio.run(main(p.parse_args()))
