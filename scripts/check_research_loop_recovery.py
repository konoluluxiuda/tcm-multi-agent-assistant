"""SIGKILL the actual tool loop at three boundaries; only synthetic SDK/tool calls."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
from tcm.research_run_demo import OfflineModel,synthetic_tool
from tcm.research_run_durable import run_durable
from tcm.research_run import Limits


async def worker(args):
    directory=args.out/args.case
    def count(kind):
        with (directory/'calls.jsonl').open('a') as stream:stream.write(json.dumps({'kind':kind})+'\n')
    async def pause():
        (directory/'ready').touch();await asyncio.Event().wait()
    class Model(OfflineModel):
        async def ainvoke(self,messages):
            count('model')
            if args.case=='model' and not args.resume:await pause()
            return await super().ainvoke(messages)
    async def backend(name,arguments):
        count('tool')
        if args.case=='tool' and not args.resume:await pause()
        return await synthetic_tool(name,arguments)
    async def on_state(snapshot):
        if args.case=='saved' and not args.resume and snapshot['last_node']=='execute':await pause()
    result=await run_durable('Synthetic disease lookup',Model(),backend,path=directory/'journal.sqlite3',
        owner='synthetic-owner',identifier='synthetic-run',contract='synthetic-sdk-tools-v1',
        resume=args.resume,on_state=on_state,limits=Limits(seconds=120))
    (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


async def main(args):
    if args.case:
        await worker(args);return
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700);results=[]
    for case in ('saved','model','tool'):
        directory=args.out/case;directory.mkdir(mode=0o700)
        command=[sys.executable,'-m','scripts.check_research_loop_recovery','--out',str(args.out),'--case',case]
        log=(directory/'worker.log').open('w')
        child=await asyncio.create_subprocess_exec(*command,stdout=log,stderr=log)
        try:
            for _ in range(200):
                if (directory/'ready').exists():break
                if child.returncode is not None:raise AssertionError('Worker exited early')
                await asyncio.sleep(.05)
            else:raise AssertionError('Boundary not reached')
            child.kill();await child.wait()
        finally:
            if child.returncode is None:child.kill();await child.wait()
        resumed=await asyncio.create_subprocess_exec(*command,'--resume',stdout=log,stderr=log)
        try:await asyncio.wait_for(resumed.wait(),20)
        finally:
            if resumed.returncode is None:resumed.kill();await resumed.wait()
            log.close()
        assert resumed.returncode==0
        result=json.loads((directory/'result.json').read_text())
        counts=Counter(json.loads(line)['kind'] for line in (directory/'calls.jsonl').read_text().splitlines())
        expected={'model':4,'tool':3} if case=='saved' else {'model':1} if case=='model' else {'model':1,'tool':1}
        assert counts==expected,(case,counts)
        assert result['status']==('complete' if case=='saved' else 'partial')
        if case!='saved':assert result['reason']=='uncertain_'+case+'_dispatch'
        assert result['model_calls']==counts['model'] and result['tool_calls']==counts.get('tool',0)
        results.append({'case':case,'status':result['status'],'reason':result['reason'],'calls':dict(counts),
                        'model_budget_used':result['model_calls'],'tool_budget_used':result['tool_calls'],
                        'estimated_input_units':result['estimated_input_units']})
    summary={'status':'passed','sigkill_cases':results,'data':'synthetic_only','external_calls':0,
             'limitation':'Uncertain dispatches stop; no provider idempotency or production UI integration claimed.'}
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--case',choices=['saved','model','tool']);p.add_argument('--resume',action='store_true')
    asyncio.run(main(p.parse_args()))
