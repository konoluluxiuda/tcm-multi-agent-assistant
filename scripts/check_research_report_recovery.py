"""SIGKILL a metered report in the production loop, synthetic sources only."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
from tests.research_report_fixture import ReportFixture
from tcm.research_run_durable import run_durable
from tcm.research_run_local import LocalAnalysisTools,DeclaredActionsDriver
from tcm.research_run_scope import RunScope
from tcm.research_run import Limits


async def worker(args):
    root=args.out/args.case;config=json.loads((root/'config.json').read_text())
    os.environ.update(config['environment']);scope=RunScope.model_validate(config['scope'])
    backend=LocalAnalysisTools(None,herbs=config['environment']['TCM_DB'],
        enrichment=config['environment']['ETCM_ENRICHMENT_DB'],pharmacopoeia=config['environment']['PHARMACOPOEIA_INDEX'])
    from tcm.cross_source_report import local_branch
    async def pause():
        (root/'ready').touch();await asyncio.Event().wait()
    async def tracked(kind,herb,*a,**kw):
        with (root/'calls.jsonl').open('a') as stream:
            stream.write(json.dumps({'kind':kind,'herb':herb})+'\n');stream.flush();os.fsync(stream.fileno())
        if args.case=='inner_pending' and not args.resume:await pause()
        return await local_branch(kind,herb,*a,**kw)
    async def saved(state):
        if args.case=='report_saved' and not args.resume and state['last_node']=='execute':await pause()
    with patch('tcm.cross_source_report.local_branch',new=tracked):
        result=await run_durable('Synthetic metered pair report',DeclaredActionsDriver(scope),backend,
            path=root/'journal.sqlite3',owner='synthetic-owner',identifier='synthetic-report-run',
            contract=json.dumps(backend.contract,sort_keys=True),scope=scope,limits=Limits(tool_calls=16,seconds=120,call_seconds=30),
            on_state=saved,resume=args.resume)
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False)+'\n')


async def main(args):
    if args.case:return await worker(args)
    args.out.mkdir(parents=True,exist_ok=False);cases=[]
    for case in ('inner_pending','report_saved'):
        root=args.out/case;root.mkdir()
        with ReportFixture() as fixture:
            config={'environment':{k:os.environ[k] for k in ('TCM_DB','TCM_DATASET_VERSION','ETCM_ENRICHMENT_DB','PHARMACOPOEIA_INDEX')},
                    'scope':fixture.report_scope().model_dump()}
            (root/'config.json').write_text(json.dumps(config))
            command=[sys.executable,'-m','scripts.check_research_report_recovery','--out',str(args.out),'--case',case]
            with (root/'worker.log').open('w') as log:
                process=await asyncio.create_subprocess_exec(*command,stdout=log,stderr=log)
                try:
                    for _ in range(500):
                        if (root/'ready').exists():break
                        if process.returncode is not None:raise RuntimeError('Worker exited before boundary')
                        await asyncio.sleep(.05)
                    else:raise TimeoutError('Boundary not reached')
                finally:
                    if process.returncode is None:process.kill()
                    await process.wait()
                before=len((root/'calls.jsonl').read_text().splitlines())
                resumed=await asyncio.create_subprocess_exec(*command,'--resume',stdout=log,stderr=log)
                try:await asyncio.wait_for(resumed.wait(),30)
                finally:
                    if resumed.returncode is None:resumed.kill();await resumed.wait()
                assert resumed.returncode==0
            result=json.loads((root/'result.json').read_text());after=len((root/'calls.jsonl').read_text().splitlines())
            assert before==after==result['tool_calls']
            if case=='inner_pending':assert result['reason']=='uncertain_tool_dispatch' and not result['evidence']
            else:assert result['status']=='complete' and after==6 and result['action_calls']==1
            cases.append({'case':case,'status':result['status'],'reason':result['reason'],
                'source_reads_before':before,'source_reads_after':after,'tool_calls':result['tool_calls'],
                'action_calls':result['action_calls'],'replayed_reads':0})
    report={'status':'passed','cases':cases,'external_model_calls':0,'data':'synthetic_only',
        'limits':['composite pending stops rather than resuming individual inner leaves','not web queue lifecycle or machine power-loss testing']}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--case',choices=['inner_pending','report_saved']);p.add_argument('--resume',action='store_true')
    asyncio.run(main(p.parse_args()))
