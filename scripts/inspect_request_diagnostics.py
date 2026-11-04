"""Local administrator reads one existing request chain; never calls a model.

The HTTP endpoint remains cookie/owner scoped. This CLI requires filesystem
access to the private state directory and prints only stage summaries by default.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from tcm.dataops.diagnostics import Store,ID


def inspect(root,identifier):
    if not ID.fullmatch(identifier):raise ValueError('Expected the server X-Request-ID (32 hex characters)')
    store=Store(root)
    if not store.path.is_file():raise ValueError('No diagnostic database at this state path')
    with closing(sqlite3.connect('file:'+str(store.path.resolve())+'?mode=ro',uri=True)) as con:
        owner=con.execute('SELECT owner FROM requests WHERE id=?',(identifier,)).fetchone()
    if owner is None:raise KeyError('No record; historical missing outputs cannot be reconstructed')
    return store.get(owner[0],identifier)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True,help='DATAOPS_ROOT, typically STATE/dataops')
    parser.add_argument('--request-id',required=True);parser.add_argument('--out',type=Path)
    args=parser.parse_args();receipt=inspect(args.root,args.request_id)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Create mode 0600 atomically; never overwrite a previous diagnostic.
        import os
        fd=os.open(args.out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'request_id':receipt['request_id'],'langfuse_trace_id':receipt['langfuse_trace_id'],
        'workspace_id':receipt['workspace_id'],'business_acceptance':'not_evaluated',
        'timeline':[{'stage':e['stage'],'status':e['status'],'code':e['code']} for e in receipt['events']]},ensure_ascii=False))
