"""SIGKILL the actual queue worker and explicitly resume the same A plan."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from scripts.run_offline_ci import test_environment
from tests.research_report_fixture import ReportFixture,QUESTION
from tcm.research_jobs import JobStore,ResearchWorker

CASES=('initial_claim','leaf_saved','inner_pending','report_saved')


async def worker(args):
    root=args.out/args.case;config=json.loads((root/'config.json').read_text());os.environ.update(config['environment'])
    from tcm.research_run_local import LocalAnalysisTools,run_local_actions
    from tcm.cross_source_report import local_branch
    from tcm.research_plan_jobs import execute_job
    queue=JobStore(root/'jobs.sqlite3');identifier=config['id']
    async def pause():
        (root/'ready').touch();await asyncio.Future()
    def record(kind):
        with (root/'calls.jsonl').open('a') as out:
            out.write(json.dumps({'kind':kind})+'\n');out.flush();os.fsync(out.fileno())
    original=LocalAnalysisTools.__call__
    async def atomic(backend,name,query):record(name);return await original(backend,name,query)
    async def branch(kind,*a,**kw):
        record(kind)
        if args.case=='inner_pending' and not args.resume:await pause()
        return await local_branch(kind,*a,**kw)
    async def run(*a,on_state,**kw):
        async def persist(state):
            await on_state(state)
            if not args.resume and state.get('last_node')=='execute' and (
                    args.case=='leaf_saved' and state['tool_calls']==1 or args.case=='report_saved'):
                await pause()
        return await run_local_actions(*a,on_state=persist,**kw)
    async def job(*a,**kw):
        if args.case=='initial_claim' and not args.resume:await pause()
        return await execute_job(*a,**kw)
    service=ResearchWorker(lambda:queue,lambda:None,run_path=lambda:root/'runs.sqlite3')
    with patch.object(LocalAnalysisTools,'__call__',atomic),patch('tcm.cross_source_report.local_branch',branch),\
         patch('tcm.research_run_local.run_local_actions',run),patch('tcm.research_plan_jobs.execute_job',job):
        service.start()
        try:
            for _ in range(700):
                doc=queue.get('synthetic-owner',identifier)
                if doc['status'] in ('complete','partial','failed','cancelled','interrupted'):
                    (root/'result.json').write_text(json.dumps(doc,ensure_ascii=False)+'\n');return
                await asyncio.sleep(.05)
            raise TimeoutError('Worker did not finish')
        finally:await service.stop()


async def main(args):
    if args.case:return await worker(args)
    args.out.mkdir(parents=True,exist_ok=False);summaries=[]
    for case in CASES:
        root=args.out/case;root.mkdir()
        with ReportFixture() as fixture:
            from tcm.research_plan_run import signature,compile_scope
            from tcm.research_plan_intent import local_plan
            from tcm.research_plan_jobs import make_payload
            environment={k:os.environ[k] for k in ('TCM_DB','TCM_DATASET_VERSION','ETCM_ENRICHMENT_DB','PHARMACOPOEIA_INDEX')}
            environment.update(ENABLE_RESEARCH_TASKS='1',ENABLE_RESEARCH_PLAN_JOBS='1',ENABLE_PHARMACOPOEIA_RESEARCH='1',
                RESEARCH_JOB_DSN='',RESEARCH_CHECKPOINT_DSN='',RESEARCH_JOB_DB=str(root/'jobs.sqlite3'))
            question='Alpha的靶点画像，Beta的靶点画像' if case=='leaf_saved' else QUESTION
            plan=local_plan(question);scope,unresolved=compile_scope(plan,{});assert unresolved is None
            queue=JobStore(root/'jobs.sqlite3');doc,_=queue.enqueue('synthetic-owner',question,'synthetic-key',
                payload=make_payload(question,plan,{},signature(plan),scope))
            (root/'config.json').write_text(json.dumps({'environment':environment,'id':doc['id']}))
            (root/'calls.jsonl').touch()
            command=[sys.executable,'-m','scripts.check_research_plan_job_recovery','--out',str(args.out),'--case',case]
            with (root/'worker.log').open('w') as log:
                child=await asyncio.create_subprocess_exec(*command,env=test_environment(),stdout=log,stderr=log)
                try:
                    for _ in range(500):
                        if (root/'ready').exists():break
                        if child.returncode is not None:raise RuntimeError('Worker exited before boundary')
                        await asyncio.sleep(.05)
                    else:raise TimeoutError('Boundary not reached')
                finally:
                    if child.returncode is None:child.kill()
                    await child.wait()
                assert child.returncode<0
                before=len((root/'calls.jsonl').read_text().splitlines())
                # Expire only the killed worker's lease, without a 30-second sleep.
                fencer=JobStore(queue.path,clock=lambda:time.time()+31);assert fencer.claim('lease-auditor') is None
                interrupted=queue.get('synthetic-owner',doc['id']);assert interrupted['status']=='interrupted'
                queue.resume('synthetic-owner',doc['id'],expected_revision=interrupted['revision'])
                second=await asyncio.create_subprocess_exec(*command,'--resume',env=test_environment(),stdout=log,stderr=log)
                try:await asyncio.wait_for(second.wait(),35)
                finally:
                    if second.returncode is None:second.kill();await second.wait()
                assert second.returncode==0
            final=json.loads((root/'result.json').read_text());run=final.get('result',{}).get('run',{})
            after=len((root/'calls.jsonl').read_text().splitlines())
            if case=='inner_pending':
                assert final['status']=='partial' and run['reason']=='uncertain_tool_dispatch' and after==before
            else:
                expected=2 if case=='leaf_saved' else 6
                assert final['status']=='complete' and after==expected and run['tool_calls']==expected
                if case=='report_saved':assert before==after==6
                if case=='leaf_saved':assert before==1
                if case=='initial_claim':assert before==0
            item={'case':case,'status':final['status'],'reason':run.get('reason'),'reads_before_kill':before,
                  'reads_after_resume':after,'tool_calls':run.get('tool_calls'),'same_job_id':final['id']==doc['id'],
                  'replayed_saved_reads':0,'killed_process_exit':child.returncode}
            (root/'acceptance.json').write_text(json.dumps(item,indent=2)+'\n');summaries.append(item)
    report={'status':'passed','data':'synthetic_only','external_model_calls':0,'cases':summaries,
        'lease_expiry':'clock advanced only after killed child exited','limits':['single machine SQLite queue and file journal','not machine power loss or remote provider exactly-once']}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--case',choices=CASES);parser.add_argument('--resume',action='store_true')
    asyncio.run(main(parser.parse_args()))
