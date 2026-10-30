"""SIGKILL two evidence publication boundaries, synthetic backend only."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
# Standalone synthetic checks must not inherit remote tracing.
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
from experiments.architecture_candidates.evidence_store import Scope
from experiments.architecture_candidates.offload_loop import execute
from experiments.architecture_candidates.offload_driver import Driver
from tests.large_observation_fixture import fixture,MARKER

async def main(args):
    if args.case:
        root=args.out/args.case
        async def backend():
            with (root/'calls.txt').open('a') as f:f.write('fetch\n');f.flush();os.fsync(f.fileno())
            return fixture(14000)
        async def hook(name):
            if name==args.case and not args.resume:
                (root/'ready').touch();await asyncio.Event().wait()
        r=await execute(Driver(),backend,path=root/'run.sqlite3',evidence_path=root/'evidence.sqlite3',
            model_contract='synthetic-sdk-v1',source_id='etcm-disease:'+'a'*64,
            scope=Scope('synthetic-owner','synthetic-run','context-synthetic-v1','synthetic'),
            resume=args.resume,hook=hook)
        (root/'result.json').write_text(json.dumps({k:v for k,v in r.items() if k!='messages'}));return
    args.out.mkdir(parents=True,exist_ok=False);rows=[]
    for case in ('evidence_saved','reference_saved','action_saved'):
        root=args.out/case;root.mkdir()
        cmd=[sys.executable,'-m','scripts.check_offload_loop_recovery','--out',str(args.out),'--case',case]
        with (root/'worker.log').open('w') as log:
            proc=await asyncio.create_subprocess_exec(*cmd,stdout=log,stderr=log)
            try:
                for _ in range(200):
                    if (root/'ready').exists():break
                    if proc.returncode is not None:raise RuntimeError('Worker exited early')
                    await asyncio.sleep(.05)
                else:raise TimeoutError('Boundary not reached')
            finally:
                if proc.returncode is None:proc.kill()
                await proc.wait()
            resumed=await asyncio.create_subprocess_exec(*cmd,'--resume',stdout=log,stderr=log)
            try:await asyncio.wait_for(resumed.wait(),20)
            finally:
                if resumed.returncode is None:resumed.kill();await resumed.wait()
            assert resumed.returncode==0
        r=json.loads((root/'result.json').read_text());calls=len((root/'calls.txt').read_text().splitlines())
        assert calls==1
        assert r['tool_calls']==(1 if case=='evidence_saved' else 2)
        if case=='evidence_saved':
            assert r['reason']=='uncertain_tool_dispatch' and r['reference'] is None
        else:
            assert r['status']=='complete' and r['read_result']['data']['items']==[MARKER]
        with sqlite3.connect(root/'evidence.sqlite3') as db:records=db.execute('SELECT count(*) FROM evidence').fetchone()[0]
        rows.append({'case':case,'status':r['status'],'reason':r['reason'],'backend_dispatches':calls,
                     'model_calls':r['model_calls'],'tool_calls':r['tool_calls'],
                     'max_model_input_bytes':max(r['model_input_bytes']),'evidence_records':records,'reference_published':bool(r['reference']),
                     'marker_recovered':bool(r['read_result'] and r['read_result']['data']['items']==[MARKER])})
    report={'status':'passed','cases':rows,'external_calls':0,'synthetic_only':True,
            'limits':['Fixed SDK substitute in isolated loop; not production research_run','Unpublished evidence retained, not automatically garbage-collected',
                      'No distributed transaction; unknown outcomes stop rather than refetch']}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--case',choices=['evidence_saved','reference_saved','action_saved']);p.add_argument('--resume',action='store_true')
    asyncio.run(main(p.parse_args()))
