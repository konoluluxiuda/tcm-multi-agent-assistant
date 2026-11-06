"""Chat handoff to the same DataOps queue; never invoke the legacy herb parser."""
from copy import deepcopy
import hashlib
import re
from uuid import uuid4

import anyio
from langchain_core.messages import HumanMessage, AIMessage

from agents.runtime import session_turn
from tcm.dataops.contracts import SubmitQuestion
from tcm.dataops.service import ProposalNeedsInput
from tcm.dataops.workspace import Conflict
from tcm.dataops.request_policy import followup, matches


def card(doc):
    from tcm.capabilities import research_runtime
    research='research' in doc.get('goal',{})
    driver=research_runtime()['driver'] if research else 'synthetic'
    result=(doc.get('result') or {}).get('research') or {}
    calls=(doc.get('goal_understanding') or {}).get('provider_requests',0)+result.get('provider_requests',0)
    execution_driver=(doc.get('progress') or {}).get('driver',driver)
    if research and not result and (driver=='native' or execution_driver.startswith('research_native_')):calls=None
    return {'topic': 'dataops_task', 'status': doc['status'], 'workspace_id': doc['id'],
            'message': '资料整理任务已保存，结果将在下方更新。', 'llm_calls': calls, 'network_calls': calls,
            'counts_scope':'workspace_total',
            'counts_final':calls is not None and doc['status'] in ('complete','partial','failed','cancelled'),
            'driver':execution_driver}


def restore(snapshot, service, owner):
    if not snapshot.messages:
        return None
    last = snapshot.messages[-1]
    saved = getattr(last, 'additional_kwargs', {}).get('dataops_card')
    if not isinstance(saved, dict) or saved.get('topic') != 'dataops_task':
        return None
    if not saved.get('workspace_id'):
        return saved
    try:
        return card(service.store().get(owner, saved['workspace_id']))
    except KeyError:
        return {'topic': 'dataops_task', 'status': 'unavailable',
                'message': '这条整理任务已不可用，可到资料工作台查看任务记录。'}


def execute(service, memory, session_id, question, *, request_key=None, history_limit=30):
    """Called from FastAPI's sync worker; bridge to its existing async queue owner."""
    from tcm.capabilities import research_runtime
    research=research_runtime()['enabled']
    if not research:service.require_agent()
    owner = hashlib.sha256(session_id.encode()).hexdigest()
    key = request_key or uuid4().hex
    with session_turn(session_id):
        snapshot = memory.load(session_id)
        from tcm.dataops.conversation_scope import prepare
        conversation = prepare(question, snapshot)
        store = service.store()
        existing = store.by_request(owner, key)
        if existing:
            if existing.get('question') != question:
                raise Conflict('Request key bound to a different question')
            if existing['status']=='queue_busy' and not existing.get('job_id'):
                existing=anyio.from_thread.run(service.retry_busy,owner,key,question)['workspace']
            output = {**card(existing),'replayed':True,'understanding_requests_this_submission':0}
            anyio.from_thread.run(service.wake)
            if any(getattr(m, 'additional_kwargs', {}).get('dataops_request_key') == key for m in snapshot.messages):
                return output
        else:
            focus = snapshot.tcm_context.get('dataops') if isinstance(snapshot.tcm_context, dict) else None
            parent = None
            if followup(question) and isinstance(focus, dict) and focus.get('workspace_id'):
                parent = store.get(owner, focus['workspace_id'])
            if parent and 'research' not in parent['goal']:research=False
            from tcm.dataops.research_contracts import ResearchQuestion
            body_type=ResearchQuestion if research else SubmitQuestion
            body = body_type(question=question, request_key=key,
                parent_id=parent['id'] if parent else None, expected_revision=parent['revision'] if parent else None)
            result=None
            try:
                async def submit():
                    return await service.submit_research_question(owner,body,allow_queries=True,conversation=conversation) if research else await service.submit(owner,body)
                result = anyio.from_thread.run(submit)
                conversation = result.get('proposal',{}).get('conversation_context', conversation)
                if result.get('proposal',{}).get('status')=='query':
                    proposal=result['proposal'];calls=proposal['external_model_calls']
                    routes=proposal.get('routes') or [proposal['route']]
                    meta={'mode':'model','reason':'unified_request_fallback',
                          'intent_model_calls':0 if result['replayed'] else calls,
                          'original_provider_requests':calls,'understanding_replayed':result['replayed']}
                    return {'topic':'query_handoff','request_key':key,
                            'understanding_requests_this_submission':meta['intent_model_calls'],
                            'route':{**routes[0],**meta},
                            'routes':[{**r,**meta} for r in routes]}
                if not result.get('workspace'):raise ProposalNeedsInput(result['proposal'])
                output = {**card(result['workspace']),'replayed':result.get('replayed',False),
                          'understanding_requests_this_submission':0 if result.get('replayed') else
                              result.get('proposal',{}).get('external_model_calls',0)}
            except ProposalNeedsInput as exc:
                from tcm.dataops.conversation_scope import subject
                parsed = subject(question)
                names = conversation.get('object_mentions', [])
                message = '\n'.join(exc.proposal['issues'])
                if names and parsed and parsed[1] in ('资料','属性','相关信息','信息'):
                    message = '已记下'+'、'.join(names)+'。你想查询哪些资料？例如性味、归经、药用部位或药典功能与主治。'
                output = {'topic': 'dataops_task', 'status': 'clarification_needed',
                          'message': message, 'question': question,
                          'unresolved': exc.proposal['unresolved'],
                          'failure':exc.proposal.get('failure'),
                          'llm_calls':exc.proposal.get('external_model_calls',0),
                          'network_calls':exc.proposal.get('external_model_calls',0),
                          'counts_scope':'understanding_result_total',
                          'replayed':bool(result and result.get('replayed')),
                          'understanding_requests_this_submission':0 if result and result.get('replayed') else
                              exc.proposal.get('external_model_calls',0)}
        if any(getattr(m, 'additional_kwargs', {}).get('dataops_request_key') == key for m in snapshot.messages):
            return output
        context = deepcopy(snapshot.tcm_context)
        if output.get('workspace_id'):
            if not isinstance(context, dict) or context.get('kind') != 'agent_contexts_v1':
                context = {'kind': 'agent_contexts_v1', 'herb': context, 'research': None,
                           'pubmed': None, 'relation': None}
            context['dataops'] = {'kind': 'dataops_chat_v1', 'workspace_id': output['workspace_id']}
        messages = snapshot.messages + [HumanMessage(content=question, additional_kwargs={'query_scope': conversation}),
            AIMessage(content=output['message'], additional_kwargs={'dataops_card': output, 'dataops_request_key': key})]
        memory.save(session_id, messages[-history_limit:], context, revision=snapshot.revision)
        return output
