"""One DataOps submission service shared by chat and the workbench router."""
import asyncio
import os
from pathlib import Path

from features import env_bool
from tcm.dataops.contracts import CreateAgent
from tcm.dataops.goal_proposal import propose
from tcm.dataops.workspace import Workspaces, Conflict


class ProposalNeedsInput(ValueError):
    def __init__(self, proposal):
        super().__init__('Question requirements are not fully resolved')
        self.proposal = proposal


class DataOpsService:
    def __init__(self, memory_provider):
        from tcm.research_jobs import ResearchWorker
        self.memory_provider = memory_provider
        self.worker = ResearchWorker(self.queue, lambda: '')

    def store(self):
        memory = self.memory_provider()
        return Workspaces(os.getenv('DATAOPS_ROOT') or Path(memory.path).with_name('dataops'))

    def queue(self):
        from tcm.research_jobs import storage
        if os.getenv('RESEARCH_JOB_DSN'):
            raise Conflict('DataOps currently requires the SQLite queue')
        return storage(self.memory_provider().path)

    async def wake(self):
        self.worker.start()

    @staticmethod
    def require_agent():
        if not env_bool('ENABLE_DATAOPS') or not env_bool('ENABLE_DATAOPS_AGENT') or os.getenv('DATAOPS_DRIVER') != 'synthetic':
            raise Conflict('DataOps agent is disabled; only the explicit synthetic driver is available')

    @staticmethod
    def proposal(store, owner, body, sources):
        parent = store.get(owner, body.parent_id) if body.parent_id else None
        if parent and (parent['revision'] != body.expected_revision or parent['source_revision'] != sources.revision):
            raise Conflict('Parent or source revision changed')
        return propose(body.question, sources.name_catalog(), parent=parent)

    def enqueue(self, store, owner, body, sources):
        q = self.queue()
        doc, created = store.reserve_agent(owner, body.request_key, body.goal.model_dump(), sources.revision,
            question=body.question, parent_id=body.parent_id, expected_revision=body.expected_revision,
            refresh_source_ids=body.refresh_source_ids)
        payload = {'kind': 'dataops', 'root': str(store.root), 'workspace_id': doc['id'],
                   'workspace_revision': doc['revision']}
        if doc['status'] == 'failed' and not doc.get('job_id'):
            raise Conflict('Previous enqueue failed; use a fresh request key')
        try:
            job, _ = q.enqueue(owner, body.question, 'dataops-' + body.request_key, payload=payload)
        except Exception:
            if created:
                store.project(owner, doc['id'], status='failed', error='queue_not_enqueued')
            raise
        doc = store.project(owner, doc['id'], job_id=job['id'])
        return {'workspace': doc, 'job': job, 'created': created}

    async def submit(self, owner, body):
        self.require_agent()
        def action():
            from tcm.dataops.api import configured_sources
            store = self.store()
            self.queue()  # Check backend compatibility before reserving a workspace.
            with configured_sources() as sources:
                plan = self.proposal(store, owner, body, sources)
                if plan['status'] != 'ready':
                    raise ProposalNeedsInput(plan)
                task = CreateAgent(request_key=body.request_key, question=body.question, goal=plan['goal'],
                    parent_id=plan['parent_id'], expected_revision=plan['expected_revision'],
                    refresh_source_ids=plan['refresh_source_ids'])
                result = self.enqueue(store, owner, task, sources)
                return {**result, 'proposal': plan}
        result = await asyncio.to_thread(action)
        self.worker.start()
        return result
