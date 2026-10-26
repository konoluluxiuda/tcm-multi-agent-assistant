"""Explicit initialization of a dedicated research runtime database; no source migration."""
import asyncio
import os
from tcm.postgres_jobs import migrate


async def main():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    jobs=os.environ['RESEARCH_JOB_DSN'];checkpoints=os.environ['RESEARCH_CHECKPOINT_DSN']
    migrate(jobs)
    if checkpoints!=jobs:migrate(checkpoints)
    async with AsyncPostgresSaver.from_conn_string(checkpoints) as saver:
        await saver.setup()
        await saver.conn.execute('CREATE TABLE IF NOT EXISTS tcm_jobs.checkpoint_bindings(thread_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL)')
    print('Research PostgreSQL schema ready; source catalogs unchanged')


if __name__=='__main__':asyncio.run(main())
