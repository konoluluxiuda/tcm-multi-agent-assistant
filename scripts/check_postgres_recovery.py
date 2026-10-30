"""Real PostgreSQL/kill/restart acceptance using synthetic local-only report branches."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import psycopg
from tcm.postgres_jobs import PostgresJobStore
from tcm.report_checkpoint import run_checkpoint_report
from tcm.research_jobs import JobConflict


def store():return PostgresJobStore(os.environ['RESEARCH_JOB_DSN'])


async def worker(args):
    db=store();doc=db.get(args.owner,args.job)
    async def backend(branch,herb,question):
        with (args.out/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':herb+'_'+branch if args.pair else branch})+'\n')
        if branch=='structured' and (not args.pair or herb=='b'):
            while not (args.out/'release').exists():await asyncio.sleep(.05)
            if not args.pair:return {'status':'ok','rows':[{'label':'合成属性','value':'fixture'}]}
        if args.pair:
            from tests.test_pair_research_report import report
            return report(herb)[branch]
        return {'status':'candidates','hits':[{'name':'示例叶','label':'功能与主治','text':'fixture','evidence':[]}]}
    async def target(branch,herb,question):
        from tests.test_pair_research_report import targets
        with (args.out/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':'target'})+'\n')
        return targets(json.loads(question))
    async def persist(snapshot):
        nonlocal doc
        doc=db.save(args.owner,doc,args.worker,snapshot=snapshot)
    result=await run_checkpoint_report(args.owner,doc,backend=backend,target_backend=target,on_state=persist,fingerprint='synthetic-snapshot-v1')
    db.save(args.owner,doc,args.worker,status=result['status'],result=result)


async def main(args):
    if args.worker:
        await worker(args);return
    args.out.mkdir(parents=True,exist_ok=False)
    db=store();owner='acceptance-'+uuid4().hex
    payload=({'kind':'pair_report','herbs':['a','b'],'question':'功效'} if args.pair else
             {'kind':'cross_source','herb':'示例叶','question':'功效'})
    doc,_=db.enqueue(owner,'合成恢复验收',uuid4().hex,payload=payload)
    claimed=db.claim('crash');assert claimed and claimed[1]['id']==doc['id']
    doc=db.save(owner,{**claimed[1],'recovery':'checkpoint'},'crash')
    thread=hashlib.sha256((owner+':'+doc['id']).encode()).hexdigest()
    cmd=[sys.executable,'-m','scripts.check_postgres_recovery','--out',str(args.out),'--owner',owner,'--job',doc['id']]
    if args.pair:cmd.append('--pair')
    child=await asyncio.create_subprocess_exec(*cmd,'--worker','crash')
    try:
        ready=False
        for _ in range(300):
            with psycopg.connect(os.environ['RESEARCH_CHECKPOINT_DSN']) as conn:
                channels={r[0] for r in conn.execute('SELECT channel FROM checkpoint_writes WHERE thread_id=%s',(thread,))}
                ready=({'left_text','left_structured','right_text'} if args.pair else {'text'}) <= channels
            if ready:break
            if child.returncode is not None:raise RuntimeError('Worker exited before checkpoint')
            await asyncio.sleep(.05)
        assert ready,'Text checkpoint not persisted'
        child.kill();await child.wait()
    finally:
        if child.returncode is None:child.kill();await child.wait()
    expired=PostgresJobStore(os.environ['RESEARCH_JOB_DSN'],clock=lambda:time.time()+31)
    assert expired.claim('reaper') is None
    assert db.get(owner,doc['id'])['status']=='interrupted'
    try:db.resume('other-owner',doc['id']);raise AssertionError('Owner bypass')
    except KeyError:pass
    db.resume(owner,doc['id'])
    resumed=db.claim('resume');assert resumed[1]['id']==doc['id']
    try:db.save(owner,doc,'crash',status='complete');raise AssertionError('Old worker wrote')
    except JobConflict:pass
    # Version mismatch is rejected before the backend is entered.
    try:
        await run_checkpoint_report(owner,resumed[1],backend=lambda *x:None,fingerprint='changed-source')
        raise AssertionError('Changed source accepted')
    except ValueError:pass
    (args.out/'release').touch()
    child=await asyncio.create_subprocess_exec(*cmd,'--worker','resume')
    try:await asyncio.wait_for(child.wait(),20)
    finally:
        if child.returncode is None:child.kill();await child.wait()
    assert child.returncode==0
    final=db.get(owner,doc['id']);assert final['status']=='complete'
    counts=Counter(json.loads(line)['branch'] for line in (args.out/'calls.jsonl').read_text().splitlines())
    expected={'a_text':1,'a_structured':1,'b_text':1,'b_structured':2,'target':1} if args.pair else {'text':1,'structured':2}
    assert counts==expected,counts
    _,events=db.events(owner,doc['id'],-1)
    assert [r['revision'] for r in events]==list(range(final['revision']+1))
    db.delete(owner,doc['id'])
    with psycopg.connect(os.environ['RESEARCH_CHECKPOINT_DSN']) as conn:
        assert not conn.execute('SELECT 1 FROM checkpoints WHERE thread_id=%s',(thread,)).fetchone()
        assert not conn.execute('SELECT 1 FROM tcm_jobs.checkpoint_bindings WHERE thread_id=%s',(thread,)).fetchone()
    receipt={'status':'passed','sigkill_recovered':True,'branch_calls':dict(counts),'owner_isolation':True,
             'stale_worker_fenced':True,'source_change_rejected':True,'events_monotonic':True,'checkpoint_delete_atomic':True,
             'external_model_calls':0,'data':'synthetic_only'}
    (args.out/'summary.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--pair',action='store_true');p.add_argument('--worker');p.add_argument('--owner');p.add_argument('--job')
    asyncio.run(main(p.parse_args()))
