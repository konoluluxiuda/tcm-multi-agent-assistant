"""SIGKILL the production graph at evidence publication/action boundaries. Synthetic only."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
from tests.large_observation_fixture import fixture,MARKER
from tests.test_research_offload import Driver,scope
from tcm.research_evidence_store import for_run
from tcm.research_run import Limits
from tcm.research_run_durable import run_durable


async def worker(args):
    directory=args.out/args.case
    session=for_run(directory/'journal.sqlite3','synthetic-owner','synthetic-run')
    def record(kind):
        with (directory/'dispatches.jsonl').open('a') as stream:
            stream.write(json.dumps({'kind':kind})+'\n');stream.flush();os.fsync(stream.fileno())
    class Model(Driver):
        async def ainvoke(self,messages):
            record('model');return await super().ainvoke(messages)
    model=Model()
    async def backend(*args):record('backend');return fixture(14000)
    async def pause():
        (directory/'ready').touch();await asyncio.Event().wait()
    original=session.prepare
    def prepare(eid,payload):
        ref=original(eid,payload)
        if args.case=='evidence_saved' and not args.resume and ref.get('externalized'):
            (directory/'ready').touch();signal.pause()
        return ref
    session.prepare=prepare
    async def on_state(state):
        if args.resume:return
        if (args.case=='reference_saved' and state['last_node']=='execute' and state['tool_calls']==1 or
            args.case=='action_saved' and state['last_node']=='decide' and state['model_calls']==2):await pause()
    r=await run_durable('Synthetic production offload',model,backend,
        path=directory/'journal.sqlite3',owner='synthetic-owner',identifier='synthetic-run',
        contract='production-offload-synthetic-v1',scope=scope(),limits=Limits(seconds=120),
        evidence_store=session,on_state=on_state,resume=args.resume)
    (directory/'result.json').write_text(json.dumps({'run':r,'marker':model.marker,
        'max_resumed_input_bytes':max(model.input_bytes,default=0)},ensure_ascii=False)+'\n')


async def main(args):
    if args.case:return await worker(args)
    args.out.mkdir(parents=True,exist_ok=False);cases=[]
    for case in ('evidence_saved','reference_saved','action_saved'):
        directory=args.out/case;directory.mkdir()
        command=[sys.executable,'-m','scripts.check_research_offload_recovery','--out',str(args.out),'--case',case]
        with (directory/'worker.log').open('w') as log:
            process=await asyncio.create_subprocess_exec(*command,stdout=log,stderr=log)
            try:
                for _ in range(300):
                    if (directory/'ready').exists():break
                    if process.returncode is not None:raise RuntimeError('Worker exited before boundary')
                    await asyncio.sleep(.05)
                else:raise TimeoutError('Boundary not reached')
            finally:
                if process.returncode is None:process.kill()
                await process.wait()
            resumed=await asyncio.create_subprocess_exec(*command,'--resume',stdout=log,stderr=log)
            try:await asyncio.wait_for(resumed.wait(),25)
            finally:
                if resumed.returncode is None:resumed.kill();await resumed.wait()
            assert resumed.returncode==0
        result=json.loads((directory/'result.json').read_text());r=result['run']
        events=[json.loads(l) for l in (directory/'dispatches.jsonl').read_text().splitlines()]
        assert sum(e['kind']=='backend' for e in events)==1
        if case=='evidence_saved':
            assert r['reason']=='uncertain_tool_dispatch' and r['evidence']=={}
        else:
            assert r['status']=='complete' and result['marker']==[MARKER]
            assert (r['model_calls'],r['tool_calls'])==(3,2)
        with sqlite3.connect(directory/'evidence.sqlite3') as db:count=db.execute('SELECT count(*) FROM evidence').fetchone()[0]
        cases.append({'case':case,'status':r['status'],'reason':r['reason'],
            'backend_calls':1,'model_calls':r['model_calls'],'tool_calls':r['tool_calls'],
            'evidence_records':count,'marker_recovered':result['marker']==[MARKER],
            'max_resumed_input_bytes':result['max_resumed_input_bytes']})
    report={'status':'passed','execution':'production tcm.research_run via run_durable',
        'cases':cases,'external_model_calls':0,'data':'synthetic_only',
        'limits':['process SIGKILL, not machine power loss','unpublished evidence retained; no inferred publication','no semantic quality or provider-token claim']}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--case',choices=['evidence_saved','reference_saved','action_saved']);p.add_argument('--resume',action='store_true')
    asyncio.run(main(p.parse_args()))
