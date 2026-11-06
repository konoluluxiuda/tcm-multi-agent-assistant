"""One DataOps submission service shared by chat and the workbench router."""
import asyncio
import os
from pathlib import Path

from tcm.dataops.contracts import CreateAgent
from tcm.dataops.goal_proposal import propose
from tcm.dataops.workspace import Workspaces, Conflict
from tcm.dataops import diagnostics as diagnostic


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
        diagnostic.bind(store.root,owner,question=body.question,request_key=body.request_key)
        self.queue()  # Reject incompatible backends before reserving a workspace.
        doc, created = store.reserve_agent(owner, body.request_key, body.goal.model_dump(), sources.revision,
            question=body.question, parent_id=body.parent_id, expected_revision=body.expected_revision,
            refresh_source_ids=body.refresh_source_ids)
        diagnostic.link_workspace(doc['id'])
        diagnostic.record('workspace','reserved',workspace_id=doc['id'],created=created,revision=doc['revision'])
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
            diagnostic.record('queue','busy',code='queue_busy',workspace_id=doc['id'])
            # JobBusy is a known transaction rollback, not an uncertain dispatch.
            # Keep the original goal/usage for an explicit same-key retry.
            if not doc.get('job_id'):
                store.project(owner, doc['id'], status='queue_busy', error='queue_busy')
            raise
        except Exception:
            diagnostic.record('queue','failed',code='queue_admission_unknown',workspace_id=doc['id'])
            if not doc.get('job_id'):
                store.project(owner, doc['id'], status='failed', error='queue_not_enqueued')
            raise
        doc = store.project(owner, doc['id'], job_id=job['id'])
        diagnostic.record('queue','accepted',workspace_id=doc['id'],job_id=job['id'])
        return {'workspace': doc, 'job': job, 'created': created}

    async def retry_busy(self, owner, request_key, question):
        """Retry only a proven unqueued reservation; never repeat understanding."""
        def action():
            from tcm.dataops.jobs import research_sources
            from tcm.dataops.api import configured_sources
            store=self.store();doc=store.by_request(owner,request_key)
            diagnostic.bind(store.root,owner,question=question,request_key=request_key)
            if not doc or doc.get('question')!=question:
                raise Conflict('Request key is not bound to this question')
            if doc['status']!='queue_busy' or doc.get('job_id'):
                raise Conflict('Only a known busy reservation can be retried')
            factory=research_sources if 'research' in doc['goal'] else configured_sources
            with factory() as sources:
                if sources.revision!=doc['source_revision']:
                    raise Conflict('Sources changed after queue admission failed')
                sources.assert_unchanged()
                diagnostic.link_workspace(doc['id'])
                diagnostic.record('understanding','replayed',code='queue_retry_without_understanding',workspace_id=doc['id'])
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

    async def submit_research_question(self,owner,body,*,allow_queries=False,conversation=None):
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
            from tcm.dataops.data_access import canonical,fingerprint,SourceChanged
            from tcm.request_journal import RequestJournal
            runtime=research_runtime()
            understanding_policy=runtime['question_policy']
            unified=understanding_policy=='unified_v2'
            if not runtime['enabled']:raise Conflict('Research question service disabled')
            allow_model=runtime['question_model_enabled'] if body.use_model is None else body.use_model
            if allow_model and not runtime['question_model_enabled']:
                raise HTTPException(409,'本部署未启用模型理解，可使用本地解析或表单。')
            store=self.store()
            diagnostic.bind(store.root,owner,question=body.question,request_key=body.request_key)
            self.queue()
            with research_sources() as sources:
                diagnostic.record('source_snapshot','bound',source_revision=sources.revision)
                parent=store.get(owner,body.parent_id) if body.parent_id else None
                if parent and (parent['revision']!=body.expected_revision or parent['source_revision']!=sources.revision):
                    raise Conflict('Parent/source version changed')
                journal=RequestJournal(store.root/'question_proposals.sqlite3',label='任务解析')
                previous=journal.get(owner,body.request_key)
                context=(previous or {}).get('conversation_context', conversation) or {}
                effective_question=context.get('resolved_question',body.question)
                scope_catalog=sources.name_catalog()
                try:
                    mentions=bounded_catalog(effective_question,scope_catalog,parent)
                    # Resolve mentions before signing the request so replay uses the same scope.
                    context={**context,'object_mentions':sorted(mentions,
                        key=lambda name:effective_question.casefold().find(name.casefold()))}
                except QuestionNeedsInput:
                    pass  # The normal validation path below owns unsupported names.
                signature=hashlib.sha256(canonical({'body':body.model_dump(),'sources':sources.revision,
                    'conversation_context':context,
                    'conversation_scope':fingerprint(Path(__file__).with_name('conversation_scope.py')),
                    'compiler':VERSION,'code':fingerprint(Path(__file__).with_name('research_question.py')),
                    'policy':understanding_policy,'allow_queries':allow_queries,
                    'result_feedback_enabled':os.getenv('ENABLE_DATAOPS_RESULT_FEEDBACK')=='1',
                    'requirements_code':fingerprint(Path(__file__).with_name('understanding_contract.py')),
                    'query_coverage_code':fingerprint(Path(__file__).with_name('query_coverage.py')),
                    'semantic_regex_guards':runtime['semantic_regex_guards'],
                    'request_policy':fingerprint(Path(__file__).with_name('request_policy.py')),
                    'preset_requests':fingerprint(Path(__file__).with_name('preset_requests.py')),
                    'query_policy':fingerprint(Path(__file__).parents[1]/'intent_planner.py') if allow_queries else None,
                    'requirements_v2':fingerprint(Path(__file__).with_name('requirements_v2.py')),
                    'requirements_planner':fingerprint(Path(__file__).with_name('requirements_planner.py')),
                    'requirements_understanding':fingerprint(Path(__file__).with_name('requirements_understanding.py')),
                    'requirements_pipeline':fingerprint(Path(__file__).with_name('requirements_pipeline.py')),
                    'requirements_feedback':fingerprint(Path(__file__).with_name('requirements_feedback.py')) if understanding_policy=='requirements_feedback_v1' else None,
                    'capability_matrix':fingerprint(Path(__file__).with_name('capability_matrix.py')),
                    'parent_artifact':parent.get('artifact') if parent else None,
                    'model':os.getenv('RAG_MODEL') if allow_model else None,
                    'recipient':os.getenv('RAG_BASE_URL') if allow_model else None}).encode()).hexdigest()
                proposal=journal.reserve(owner,body.request_key,signature);replayed=proposal is not None
                diagnostic.record('understanding','replayed' if replayed else 'started',policy=understanding_policy,
                                  semantic_regex_guards=runtime['semantic_regex_guards'],
                                  allow_model=allow_model,allow_queries=allow_queries,compiler=VERSION)
                if proposal is None:
                    try:
                        catalog=bounded_catalog(effective_question,scope_catalog,parent)
                        diagnostic.record('name_resolution','resolved',matched_catalog=catalog,parent_bound=bool(parent))
                    except QuestionNeedsInput as exc:
                        diagnostic.record('name_resolution','needs_input',code='unverified_name_variant')
                        proposal=needs(body.question,str(exc));catalog={}
                    if proposal is None and context.get('needs_context'):
                        proposal=needs(body.question,'请明确这次指的是哪些药材；当前会话没有数量一致的对象范围。')
                    if proposal is None and parent is None and understanding_policy=='local_then_one_model_v1':
                        from tcm.dataops.preset_requests import proposal as preset_proposal
                        proposal=preset_proposal(effective_question,catalog)
                        if proposal and proposal['status']=='query':
                            proposal['source_revision']=sources.revision
                    if proposal is None and understanding_policy=='requirements_feedback_v1':
                        from tcm.request_boundaries import unsupported_requirement,personal_treatment_request
                        if unsupported_requirement(body.question) or personal_treatment_request(body.question):
                            proposal=needs(body.question,'当前仅支持已有资料查询与整理，请明确资料字段。',
                                           compiler=understanding_policy,calls=0,code='unsupported_requirement')
                    if proposal is None and understanding_policy in ('requirements_v3','requirements_feedback_v1'):
                        if not allow_model:
                            proposal=needs(body.question,
                                '需求契约策略需要模型理解；本部署未启用模型或已显式选择本地解析，请使用表单明确对象与字段。',
                                compiler=understanding_policy,calls=0,code='requirements_unresolved')
                        else:
                            if parent and isinstance(parent.get('goal'),dict):
                                _names=sources.name_catalog()
                                for _name in parent['goal'].get('query',{}).get('names',[]):
                                    if _name not in catalog and _name in _names:
                                        catalog[_name]=_names[_name]
                            from tcm.dataops.requirements_pipeline import run_requirements_v3
                            from tcm.dataops.research_model import configured_model
                            feedback_enabled=understanding_policy=='requirements_feedback_v1'
                            from tcm.dataops.requirements_feedback import SourceProbeFailed
                            model=configured_model(max_requests=3 if feedback_enabled else 2)
                            try:
                                if feedback_enabled:
                                    from tcm.dataops.requirements_feedback import run_requirements_feedback
                                    pipeline=run_requirements_feedback(model,body.question,catalog,parent=parent,
                                                                       sources=sources,authorized=True,conversation=context)
                                else:
                                    pipeline=run_requirements_v3(model,body.question,catalog,parent=parent,authorized=True)
                            except SourceProbeFailed:
                                diagnostic.record('requirements_pipeline','failed',code='source_probe_failed')
                                raise HTTPException(503,'本地资料预检失败，本请求未执行且未自动重试。') from None
                            except SourceChanged:
                                diagnostic.record('requirements_pipeline','rejected',code='source_unavailable')
                                raise HTTPException(409,'资料版本已变化，本请求未执行，请使用新的请求编号。') from None
                            except Exception:
                                diagnostic.record('requirements_pipeline','unknown',code='model_dispatch_unknown')
                                raise HTTPException(503,'目标理解状态未知，本请求未自动重试。') from None
                            finally:
                                model.http_client.close()
                            diagnostic.record('requirements_pipeline','returned',pipeline_status=pipeline['status'],
                                              external_model_calls=pipeline.get('external_model_calls',0))
                            if pipeline['status']=='ready' and 'goal' in pipeline:
                                proposal={'status':'ready','goal':pipeline['goal'],'compiler':understanding_policy,
                                          'external_model_calls':pipeline['external_model_calls'],
                                          'usage':pipeline.get('usage',{}),
                                          'requirements':pipeline['requirements'].model_dump()}
                            elif pipeline['status']=='ready' and pipeline.get('query_routes'):
                                _routes=pipeline['query_routes']
                                proposal={'status':'query','route':_routes[0],'routes':_routes,
                                          'source_revision':sources.revision,
                                          'compiler':understanding_policy,
                                          'external_model_calls':pipeline['external_model_calls'],
                                          'usage':pipeline.get('usage',{}),
                                          'requirements':pipeline['requirements'].model_dump()}
                            else:
                                code=pipeline.get('failure',{}).get('code',pipeline['status'])
                                message=pipeline.get('failure',{}).get('message') or '；'.join(pipeline.get('errors') or ['需求未完整确定'])
                                proposal=needs(body.question,message,compiler=understanding_policy,
                                               calls=pipeline.get('external_model_calls',0),code=code)
                                proposal['usage']=pipeline.get('usage',{})
                            proposal['understanding_attempts']=pipeline.get('understanding_attempts',[])
                            proposal['tool_feedback']=pipeline.get('tool_feedback',[])
                    local=None
                    if proposal is None:
                        local=local_proposal(body.question,catalog,parent=parent)
                        diagnostic.record('local_understanding','returned',proposal=local)
                        task_hint=matches(body.question)
                        boundary=preflight(body.question,catalog,parent) if not allow_queries or parent or vague_task(body.question) or requires_delivery(body.question) and catalog else None
                        diagnostic.record('understanding_policy','selected',task_hint=task_hint,boundary=bool(boundary),
                                          model_fallback=not boundary and (unified or not(local['status']=='ready' and (not allow_queries or task_hint))) and allow_model)
                        if boundary:proposal=boundary
                        elif local['status']=='ready' and (not allow_queries or task_hint) and not(unified and allow_model):proposal=local
                        elif not allow_model:proposal=local
                    if proposal is None and allow_model:
                        from tcm.dataops.research_model import configured_model
                        model=configured_model(max_requests=2 if unified else 1)
                        try:
                            proposal=request_proposal(model,body.question,catalog,parent=parent,authorized=True,allow_queries=allow_queries,policy=understanding_policy)
                        except GoalResponseRejected as exc:
                            diagnostic.record('understanding','rejected',**exc.diagnostics)
                            from tcm.dataops.failures import public_failure
                            rejection=exc.diagnostics.get('rejection',{})
                            failure=public_failure(rejection.get('code'),rejection.get('validation_stage','proposal_validation'))
                            proposal=needs(body.question,failure['message'],compiler=VERSION,calls=1,code=failure['code'])
                            proposal['failure']=failure
                            proposal['usage']=exc.diagnostics.get('usage',{})
                        except Exception:
                            diagnostic.record('understanding','unknown',code='model_dispatch_unknown')
                            raise HTTPException(503,'目标理解状态未知，本请求未自动重试。') from None
                        finally:model.http_client.close()
                    if local is not None:
                        proposal['understanding']={'policy':understanding_policy,'local_status':local['status'],
                                                  'model_fallback_used':proposal['external_model_calls']>0}
                    if proposal.get('status') == 'query':
                        proposal.update(question=body.question, source_revision=sources.revision)
                    proposal['result_feedback_enabled']=(proposal.get('status')=='query' and allow_model and understanding_policy=='requirements_feedback_v1'
                        and os.getenv('ENABLE_DATAOPS_RESULT_FEEDBACK')=='1')
                    proposal['conversation_context']=context
                    sources.assert_unchanged();journal.complete(owner,body.request_key,proposal)
                diagnostic.record('understanding_result','returned',proposal=proposal,replayed=replayed)
                if proposal['status']!='ready':return {'proposal':proposal,'replayed':replayed}
                task=ResearchCreate(request_key=body.request_key,question=body.question,goal=proposal['goal'],
                    parent_id=body.parent_id,expected_revision=body.expected_revision)
                result=self.enqueue(store,owner,task,sources,goal_understanding={
                    'compiler':proposal['compiler'],'provider_requests':proposal['external_model_calls'],
                    'policy':understanding_policy,'requirements':proposal.get('requirements'),
                    'requirements_basis':proposal.get('requirements_basis'),
                    'attempts':proposal.get('understanding_attempts',[]),
                    'usage':proposal.get('usage',{})})
                return {**result,'proposal':proposal,'replayed':replayed}
        with diagnostic.request_context():
            result=await asyncio.to_thread(action)
        if result.get('workspace'):self.worker.start()
        return result
