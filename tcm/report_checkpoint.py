"""PostgreSQL checkpoints for local read-only single/pair report graphs."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from tcm.cross_source_report import run_report, CONFIG
from tcm.runtime_versions import framework_versions


def source_fingerprint():
    root=Path(__file__).resolve().parent
    files=list(root.glob('*.py'))
    for key in ('TCM_DB','ETCM_ENRICHMENT_DB'):
        if os.getenv(key):files.append(Path(os.environ[key]))
    if os.getenv('PHARMACOPOEIA_INDEX'):
        files.extend(Path(os.environ['PHARMACOPOEIA_INDEX'])/x for x in ('manifest.json','research.sqlite3'))
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        return h.hexdigest()
    content={'files':{str(p.resolve()):digest(p) for p in files},
             'config':{k:os.getenv(k) for k in CONFIG}, 'framework':framework_versions()}
    return hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()


async def run_checkpoint_report(owner,doc,*,on_state=None,backend=None,target_backend=None,fingerprint=None):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from tcm.mcp_client import report_branch
    thread=hashlib.sha256((owner+':'+doc['id']).encode()).hexdigest()
    lock=int(thread[:15],16)
    binding=hashlib.sha256(json.dumps({'payload':doc['payload'],'source':fingerprint or await asyncio.to_thread(source_fingerprint),
                                     'framework':framework_versions()},sort_keys=True).encode()).hexdigest()
    async with AsyncPostgresSaver.from_conn_string(os.environ['RESEARCH_CHECKPOINT_DSN']) as saver:
        # Session lock fences simultaneous resumptions, in addition to queue lease checks.
        cur=await saver.conn.execute('SELECT pg_try_advisory_lock(%s) AS locked',(lock,))
        if not (await cur.fetchone())['locked']:raise RuntimeError('Checkpoint already running')
        try:
            cur=await saver.conn.execute('SELECT fingerprint FROM tcm_jobs.checkpoint_bindings WHERE thread_id=%s',(thread,))
            old=await cur.fetchone()
            if old and old['fingerprint']!=binding:raise ValueError('Source or execution contract changed')
            if not old:await saver.conn.execute('INSERT INTO tcm_jobs.checkpoint_bindings VALUES(%s,%s)',(thread,binding))
            if doc['payload'].get('kind') == 'pair_report':
                from tcm.pair_research_report import run_pair_report
                from tcm.cross_source_report import local_branch
                result=await run_pair_report(doc['payload'],on_state=on_state,
                    branch_backend=backend or report_branch,target_backend=target_backend or local_branch,
                    checkpointer=saver,thread_id=thread)
            elif doc['payload'].get('kind') == 'cross_source':
                result=await run_report(doc['payload'],on_state=on_state,backend=backend or report_branch,
                                        checkpointer=saver,thread_id=thread)
            else:
                raise ValueError('Unsupported checkpoint report kind')
            if fingerprint is None:
                final=hashlib.sha256(json.dumps({'payload':doc['payload'],'source':await asyncio.to_thread(source_fingerprint),
                                               'framework':framework_versions()},sort_keys=True).encode()).hexdigest()
                if final!=binding:raise ValueError('Source changed during execution')
            return result
        finally:
            await saver.conn.execute('SELECT pg_advisory_unlock(%s)',(lock,))
