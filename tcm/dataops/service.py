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

    def enqueue(self, store, owner, body, sources, *, goal_understanding=None):
        self.queue()  # Reject incompatible backends before reserving a workspace.
        doc, created = store.reserve_agent(owner, body.request_key, body.goal.model_dump(), sources.revision,
            question=body.question, parent_id=body.parent_id, expected_revision=body.expected_revision,
            refresh_source_ids=body.refresh_source_ids)
        if goal_understanding is not None:
            doc = store.project(owner, doc['id'], goal_understanding=goal_understanding)
        return self.dispatch_reserved(store, owner, doc, body.request_key, created=created)

    def dispatch_reserved(self, store, owner, doc, request_key, *, created=False):
        from tcm.research_jobs import JobBusy
        payload = {'kind': 'dataops', 'root': str(store.root), 'workspace_id': doc['id'],
                   'workspace_revision': doc['revision']}
        if doc['status'] == 'failed' and not doc.get('job_id'):
            raise Conflict('Previous enqueue failed; use a fresh request key')
        try:
            job, _ = self.queue().enqueue(owner, doc['question'], 'dataops-' + request_key, payload=payload)
        except JobBusy:
            # JobBusy is a known transaction rollback, not an uncertain dispatch.
            # Keep the original goal/usage for an explicit same-key retry.
            if not doc.get('job_id'):
                store.project(owner, doc['id'], status='queue_busy', error='queue_busy')
            raise
        except Exception:
            if not doc.get('job_id'):
                store.project(owner, doc['id'], status='failed', error='queue_not_enqueued')
            raise
        doc = store.project(owner, doc['id'], job_id=job['id'])
        return {'workspace': doc, 'job': job, 'created': created}

    async def retry_busy(self, owner, request_key, question):
        """Retry only a proven unqueued reservation; never repeat understanding."""
        def action():
            from tcm.dataops.jobs import research_sources
            from tcm.dataops.api import configured_sources
            store=self.store();doc=store.by_request(owner,request_key)
            if not doc or doc.get('question')!=question:
                raise Conflict('Request key is not bound to this question')
            if doc['status']!='queue_busy' or doc.get('job_id'):
                raise Conflict('Only a known busy reservation can be retried')
            factory=research_sources if 'research' in doc['goal'] else configured_sources
            with factory() as sources:
                if sources.revision!=doc['source_revision']:
                    raise Conflict('Sources changed after queue admission failed')
                sources.assert_unchanged()
                return self.dispatch_reserved(store,owner,doc,request_key)
        result=await asyncio.to_thread(action)
        self.worker.start()
        return result

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

    async def submit_research_question(self,owner,body,*,allow_queries=False):
        """Shared local-first understanding, at most one paid call per key."""
        def action():
            import hashlib
            from fastapi import HTTPException
            from tcm.capabilities import research_runtime
            from tcm.dataops.jobs import research_sources
            from tcm.dataops.research_contracts import ResearchCreate
            from tcm.dataops.research_question import bounded_catalog,local_proposal,request_proposal,preflight,needs,VERSION,QuestionNeedsInput
            from tcm.dataops.request_policy import matches,requires_delivery,vague_task
            from tcm.dataops.goal_model import GoalResponseRejected
            from tcm.dataops.data_access import canonical,fingerprint
            from tcm.request_journal import RequestJournal
            runtime=research_runtime()
            if not runtime['enabled']:raise Conflict('Research question service disabled')
            allow_model=runtime['question_model_enabled'] if body.use_model is None else body.use_model
            if allow_model and not runtime['question_model_enabled']:
                raise HTTPException(409,'本部署未启用单次模型理解，可使用本地解析或表单。')
            store=self.store();self.queue()
            with research_sources() as sources:
                parent=store.get(owner,body.parent_id) if body.parent_id else None
                if parent and (parent['revision']!=body.expected_revision or parent['source_revision']!=sources.revision):
                    raise Conflict('Parent/source version changed')
                journal=RequestJournal(store.root/'question_proposals.sqlite3',label='任务解析')
                signature=hashlib.sha256(canonical({'body':body.model_dump(),'sources':sources.revision,
                    'compiler':VERSION,'code':fingerprint(Path(__file__).with_name('research_question.py')),
                    'policy':'local_then_one_model_v1','allow_queries':allow_queries,
                    'request_policy':fingerprint(Path(__file__).with_name('request_policy.py')),
                    'query_policy':fingerprint(Path(__file__).parents[1]/'intent_planner.py') if allow_queries else None,
                    'parent_artifact':parent.get('artifact') if parent else None,
                    'model':os.getenv('RAG_MODEL') if allow_model else None,
                    'recipient':os.getenv('RAG_BASE_URL') if allow_model else None}).encode()).hexdigest()
                proposal=journal.reserve(owner,body.request_key,signature);replayed=proposal is not None
                if proposal is None:
                    try:catalog=bounded_catalog(body.question,sources.name_catalog(),parent)
                    except QuestionNeedsInput as exc:proposal=needs(body.question,str(exc));catalog={}
                    local=None
                    if proposal is None:
                        local=local_proposal(body.question,catalog,parent=parent)
                        task_hint=matches(body.question)
                        boundary=preflight(body.question,catalog,parent) if not allow_queries or parent or vague_task(body.question) or requires_delivery(body.question) and catalog else None
                        if local['status']=='ready' and (not allow_queries or task_hint):proposal=local
                        elif boundary:proposal=boundary
                        elif not allow_model:proposal=local
                    if proposal is None and allow_model:
                        from tcm.dataops.research_model import configured_model
                        model=configured_model(max_requests=1)
                        try:
                            proposal=request_proposal(model,body.question,catalog,parent=parent,authorized=True,allow_queries=allow_queries)
                        except GoalResponseRejected as exc:
                            proposal=needs(body.question,'模型提案没有通过对象或字段校验，尚未执行。可使用表单核对原要求。',compiler=VERSION,calls=1)
                            proposal['usage']=exc.diagnostics.get('usage',{})
                        except Exception:
                            raise HTTPException(503,'目标理解状态未知，本请求未自动重试。') from None
                        finally:model.http_client.close()
                    if local is not None:
                        proposal['understanding']={'policy':'local_then_one_model_v1','local_status':local['status'],
                                                  'model_fallback_used':proposal['external_model_calls']>0}
                    sources.assert_unchanged();journal.complete(owner,body.request_key,proposal)
                if proposal['status']!='ready':return {'proposal':proposal,'replayed':replayed}
                task=ResearchCreate(request_key=body.request_key,question=body.question,goal=proposal['goal'],
                    parent_id=body.parent_id,expected_revision=body.expected_revision)
                result=self.enqueue(store,owner,task,sources,goal_understanding={
                    'compiler':proposal['compiler'],'provider_requests':proposal['external_model_calls'],
                    'usage':proposal.get('usage',{})})
                return {**result,'proposal':proposal,'replayed':replayed}
        result=await asyncio.to_thread(action)
        if result.get('workspace'):self.worker.start()
        return result
