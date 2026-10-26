"""Offline queue transfer; require stopped workers, terminal jobs and an empty target."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tcm.postgres_jobs import PostgresJobStore
from tcm.research_jobs import JobStore


def transfer(sqlite_path,dsn,*,export=False):
    pg=PostgresJobStore(dsn)
    if export:
        if sqlite_path.exists():raise FileExistsError('Rollback target must be new')
        JobStore(sqlite_path)
    if sqlite_path.is_symlink():raise ValueError('No symlink transfer')
    try:
        with closing(pg.connect()) as database,database:
            with closing(sqlite3.connect(sqlite_path.resolve().as_uri()+('?mode=rw' if export else '?mode=ro'),uri=True)) as local,local:
                if local.execute('SELECT version FROM job_meta').fetchall()!=[(1,)]:raise ValueError('Wrong SQLite schema')
                source,target=(database,local) if export else (local,database)
                if not export:local.execute('BEGIN')
                if source.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]:
                    raise ValueError('Drain active jobs before transfer')
                if target.execute('SELECT count(*) FROM jobs').fetchone()[0] or target.execute('SELECT count(*) FROM job_events').fetchone()[0]:
                    raise ValueError('Target must be empty')
                rows=source.execute('SELECT * FROM jobs').fetchall()
                for row in rows:
                    # Checkpoint runs remain archived on SQLite rollback, never silently restarted.
                    target.execute('INSERT INTO jobs VALUES('+','.join('?' for _ in row)+')',row)
                events=source.execute('SELECT * FROM job_events').fetchall()
                for row in events:target.execute('INSERT INTO job_events VALUES(?,?,?)',row)
        return {'jobs':len(rows),'events':len(events),'direction':'postgres_to_sqlite' if export else 'sqlite_to_postgres'}
    except Exception:
        if export:sqlite_path.unlink(missing_ok=True)
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sqlite',type=Path,required=True)
    p.add_argument('--export',action='store_true');args=p.parse_args()
    print(transfer(args.sqlite,os.environ['RESEARCH_JOB_DSN'],export=args.export))
