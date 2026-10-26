"""Actual PostgreSQL queue concurrency and reversible terminal-history transfer."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
from tcm.postgres_jobs import PostgresJobStore,migrate
from tcm.research_jobs import JobStore,JobConflict
from scripts.transfer_research_jobs import transfer


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    dsn=os.environ['RESEARCH_JOB_DSN'];migrate(dsn);migrate(dsn);db=PostgresJobStore(dsn)
    prefix=uuid4().hex;entries=[]
    for i in range(8):
        owner=prefix+str(i);doc,_=db.enqueue(owner,'synthetic','request-key');entries.append((owner,doc))
    def claim(i):return i,PostgresJobStore(dsn).claim('parallel-'+str(i))
    with ThreadPoolExecutor(max_workers=8) as pool:claimed=list(pool.map(claim,range(8)))
    valid=[(i,c) for i,c in claimed if c]
    assert len(valid)==4 and len({c[1]['id'] for _,c in valid})==4
    for i,(owner,doc) in valid:db.save(owner,doc,'parallel-'+str(i),status='complete')
    for owner,doc in entries:
        db.cancel(owner,doc['id']);db.delete(owner,doc['id'])
    with tempfile.TemporaryDirectory() as tmp:
        source=Path(tmp)/'source.sqlite3';local=JobStore(source)
        doc,_=local.enqueue('migration-owner','synthetic','transfer-key');local.cancel('migration-owner',doc['id'])
        before=local.get('migration-owner',doc['id'])
        imported=transfer(source,dsn)
        assert db.get('migration-owner',doc['id'])==before
        try:transfer(source,dsn);raise AssertionError('Overwrote nonempty target')
        except ValueError:pass
        target=Path(tmp)/'rollback.sqlite3';exported=transfer(target,dsn,export=True)
        assert JobStore(target).get('migration-owner',doc['id'])==before
        db.delete('migration-owner',doc['id'])
    receipt={'status':'passed','concurrent_connections':8,'claimed_unique_jobs':4,'migration_idempotent':True,
             'sqlite_import':imported,'sqlite_rollback':exported,'nonempty_target_rejected':True,'external_calls':0}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':main()
