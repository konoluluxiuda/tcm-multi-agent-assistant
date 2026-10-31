"""One DataOps submission service shared by chat and the workbench router."""
import asyncio
import os
from pathlib import Path

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
        from tcm.capabilities import dataops_runtime
        if not dataops_runtime()['agent']:
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

    async def submit_research_question(self,owner,body):
        """Understand once, then enqueue through the existing task service."""
        def action():
            import hashlib
            from fastapi import HTTPException
            from tcm.capabilities import research_runtime
            from tcm.dataops.jobs import research_sources
            from tcm.dataops.research_contracts import ResearchCreate
            from tcm.dataops.research_question import bounded_catalog,local_proposal,request_proposal,needs,VERSION,QuestionNeedsInput
            from tcm.dataops.goal_model import GoalResponseRejected
            from tcm.dataops.data_access import canonical,fingerprint
            from tcm.request_journal import RequestJournal
            runtime=research_runtime()
            if not runtime['enabled']:raise Conflict('Research question service disabled')
            if body.use_model and not runtime['question_model_enabled']:
                raise HTTPException(409,'本部署未启用单次模型理解，可使用本地解析或表单。')
            store=self.store();self.queue()
            with research_sources() as sources:
                parent=store.get(owner,body.parent_id) if body.parent_id else None
                if parent and (parent['revision']!=body.expected_revision or parent['source_revision']!=sources.revision):
                    raise Conflict('Parent/source version changed')
                journal=RequestJournal(store.root/'question_proposals.sqlite3',label='任务解析')
                signature=hashlib.sha256(canonical({'body':body.model_dump(),'sources':sources.revision,
                    'compiler':VERSION,'code':fingerprint(Path(__file__).with_name('research_question.py')),
                    'parent_artifact':parent.get('artifact') if parent else None,
                    'model':os.getenv('RAG_MODEL') if body.use_model else None,
                    'recipient':os.getenv('RAG_BASE_URL') if body.use_model else None}).encode()).hexdigest()
                proposal=journal.reserve(owner,body.request_key,signature);replayed=proposal is not None
                if proposal is None:
                    try:catalog=bounded_catalog(body.question,sources.name_catalog(),parent)
                    except QuestionNeedsInput as exc:proposal=needs(body.question,str(exc));catalog={}
                    if proposal is None and body.use_model:
                        from tcm.dataops.research_model import configured_model
                        model=configured_model(max_requests=1)
                        try:
                            proposal=request_proposal(model,body.question,catalog,parent=parent,authorized=True)
                        except GoalResponseRejected:
                            proposal=needs(body.question,'模型未返回可靠的完整目标，请明确字段或使用表单。',compiler='native_research_question_v2',calls=1)
                        except Exception:
                            raise HTTPException(503,'目标理解状态未知，本请求未自动重试。') from None
                        finally:model.http_client.close()
                    elif proposal is None:proposal=local_proposal(body.question,catalog,parent=parent)
                    sources.assert_unchanged();journal.complete(owner,body.request_key,proposal)
                if proposal['status']!='ready':return {'proposal':proposal,'replayed':replayed}
                task=ResearchCreate(request_key=body.request_key,question=body.question,goal=proposal['goal'],
                    parent_id=body.parent_id,expected_revision=body.expected_revision)
                result=self.enqueue(store,owner,task,sources)
                result['workspace']=store.project(owner,result['workspace']['id'],goal_understanding={
                    'compiler':proposal['compiler'],'provider_requests':proposal['external_model_calls'],
                    'usage':proposal.get('usage',{})})
                return {**result,'proposal':proposal,'replayed':replayed}
        result=await asyncio.to_thread(action)
        if result.get('workspace'):self.worker.start()
        return result
