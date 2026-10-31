"""Adapter into the existing lease queue. No second journal or scheduling loop."""
import asyncio
import os
from pathlib import Path

from tcm.dataops.graph import Engine
from tcm.dataops.model import SyntheticDriver
from tcm.dataops.workspace import Workspaces, Conflict


def configured_driver():
    if os.getenv('DATAOPS_DRIVER') == 'synthetic':
        return SyntheticDriver()
    raise Conflict('No authorized DataOps model driver configured')


def research_sources():
    from tcm.dataops.research_sources import ReleaseSources
    return ReleaseSources(os.environ['RAG_RELEASE_PATH'])


def research_driver():
    from tcm.dataops.research_model import ResearchSynthetic, ResearchFixed, ResearchDriver, configured_model
    mode = os.getenv('DATAOPS_RESEARCH_DRIVER', 'disabled')
    if mode == 'synthetic':
        return ResearchSynthetic()
    if mode == 'fixed':
        return ResearchFixed()
    if mode == 'native':
        return ResearchDriver(configured_model(), authorized=True)
    raise Conflict('Research selection driver is disabled')


async def execute_job(owner, doc, *, guard, on_state, sources_factory=None, driver_factory=None):
    from tcm.dataops.api import configured_sources
    payload = doc['payload']
    root = Path(payload['root'])
    store = Workspaces(root)
    target = store.get(owner, payload['workspace_id'])
    if target['revision'] != payload['workspace_revision']:
        raise Conflict('Queue/workspace revision mismatch')
    # One graph node per thread invocation remains fenced by queue heartbeat.
    # Cancellation of the await does not stop a thread; the guard fences its next write.
    loop = asyncio.get_running_loop()
    def report(snapshot):
        asyncio.run_coroutine_threadsafe(on_state(snapshot), loop).result(timeout=15)
    if 'research' in target['goal']:
        from tcm.dataops.research_graph import ResearchEngine
        engine_type, default_sources, default_driver = ResearchEngine, research_sources, research_driver
    else:
        engine_type, default_sources, default_driver = Engine, configured_sources, configured_driver
    engine = engine_type(store, owner, target['id'], sources_factory or default_sources,
                    (driver_factory or default_driver)(), guard=guard, on_state=report)
    try:
        result = await asyncio.to_thread(engine.run, payload.get('answer'))
    finally:
        if hasattr(engine.driver, 'close'):
            engine.driver.close()
    return {'status': result['status'], 'workspace_id': target['id'], 'pending': result.get('pending'),
            'reason': result.get('error'), 'artifact': result.get('artifact')}
