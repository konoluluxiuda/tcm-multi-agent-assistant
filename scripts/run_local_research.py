"""Run a local disease task, optionally preserving its audit journal."""
import argparse
import asyncio
import json
from pathlib import Path
from tcm.research_run_local import run_local_profile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--diseases',type=Path)
    p.add_argument('--disease',help='Exact source name or source ID; no inferred aliases')
    p.add_argument('--state-db',type=Path)
    p.add_argument('--history',action='store_true')
    p.add_argument('--recover',help='Mark a running entry stale for over 180s interrupted; does not rerun')
    args=p.parse_args()
    if args.history and args.recover:p.error('Choose history or recovery')
    if (args.history or args.recover) and not args.state_db:p.error('--state-db required')
    if not (args.history or args.recover) and (not args.disease or not args.diseases):p.error('--diseases and --disease required')
    store=None
    if args.state_db:
        if args.diseases and args.state_db.resolve()==args.diseases.resolve():p.error('Journal must be separate from source database')
        from tcm.research_run_store import RunStore
        store=RunStore(args.state_db)
    if args.history:result=store.list('local-cli')
    elif args.recover:result=store.recover('local-cli',args.recover)
    elif store:
        from tcm.research_run_durable import local_profile
        result=asyncio.run(local_profile(store,'local-cli',args.diseases,args.disease))
    else:result=asyncio.run(run_local_profile(args.diseases,args.disease))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
