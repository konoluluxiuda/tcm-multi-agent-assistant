"""Opt-in local persistent tool loop demo: synthetic model and tools, no external calls."""
import argparse
import asyncio
import json
from pathlib import Path
from tcm.research_run_demo import OfflineModel,synthetic_tool
from tcm.research_run_durable import run_durable


async def main(args):
    result=await run_durable('Find the synthetic disease and read its target profile.',OfflineModel(),synthetic_tool,
        path=args.state,owner='local-demo',identifier=args.run_id,contract='synthetic-demo-v1',resume=args.resume)
    print(json.dumps({'mode':'synthetic_offline_model','external_calls':0,'run':result},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--state',type=Path,required=True)
    p.add_argument('--run-id',required=True);p.add_argument('--resume',action='store_true')
    asyncio.run(main(p.parse_args()))
