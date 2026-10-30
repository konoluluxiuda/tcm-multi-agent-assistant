"""Browser-only delayed synthetic tasks; not imported by the production service."""
import asyncio
import os
from tests.research_report_http_fixture import fixture,app
os.environ.update(ENABLE_RESEARCH_PLAN_JOBS='1',RESEARCH_JOB_DB=str(fixture.root/'jobs.sqlite3'))
from tcm import cross_source_report
from tcm import research_run_local,research_jobs
original=cross_source_report.local_branch
reads=[];workers=[];pause_saved=False;saved_ready=False
original_start=research_jobs.ResearchWorker.start
original_run=research_run_local.run_local_actions


def start(worker):
    if worker not in workers:workers.append(worker)
    return original_start(worker)


async def run(*args,on_state,**kwargs):
    async def persist(state):
        global pause_saved,saved_ready
        await on_state(state)
        if pause_saved and state.get('last_node')=='execute':
            pause_saved=False;saved_ready=True;await asyncio.Future()
    return await original_run(*args,on_state=persist,**kwargs)


async def delayed(kind,*args,**kwargs):
    reads.append(kind)
    await asyncio.sleep(1)
    return await original(kind,*args,**kwargs)


cross_source_report.local_branch=delayed
research_jobs.ResearchWorker.start=start
research_run_local.run_local_actions=run


@app.get('/__test__/plan-stats')
def stats():return {'source_reads':len(reads),'kinds':reads,'saved_ready':saved_ready}


@app.post('/api/research/jobs/__test__/pause-saved')
def pause():
    global pause_saved,saved_ready
    pause_saved=True;saved_ready=False;return {'armed':True}


@app.post('/api/research/jobs/__test__/interrupt')
async def interrupt():
    for worker in workers:await worker.stop()
    return {'interrupted':True}
