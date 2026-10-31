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


def followup(question):
    return bool(re.match(r'\s*(?:请|帮我|再|继续|同时)*\s*(?:增加|新增|添加|补充|加一列|只保留|改为|重查)', question))


def matches(question):
    if question.lstrip().startswith('/'):
        return False
    return bool(followup(question) or re.search(
        r'(?:整理|核对|补充|导出|生成|制作|做成)[\s\S]*(?:表格|资料表|对照表|核对表|[Ee]xcel|[Xx][Ll][Ss][Xx])', question))


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
        store = service.store()
        existing = store.by_request(owner, key)
        if existing:
            if existing.get('question') != question:
                raise Conflict('Request key bound to a different question')
            output = card(existing)
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
            try:
                result = anyio.from_thread.run(service.submit_research_question if research else service.submit, owner, body)
                if not result.get('workspace'):raise ProposalNeedsInput(result['proposal'])
                output = card(result['workspace'])
            except ProposalNeedsInput as exc:
                output = {'topic': 'dataops_task', 'status': 'clarification_needed',
                          'message': '\n'.join(exc.proposal['issues']), 'question': question,
                          'unresolved': exc.proposal['unresolved'], 'llm_calls': 0, 'network_calls': 0}
        context = deepcopy(snapshot.tcm_context)
        if output.get('workspace_id'):
            if not isinstance(context, dict) or context.get('kind') != 'agent_contexts_v1':
                context = {'kind': 'agent_contexts_v1', 'herb': context, 'research': None,
                           'pubmed': None, 'relation': None}
            context['dataops'] = {'kind': 'dataops_chat_v1', 'workspace_id': output['workspace_id']}
        messages = snapshot.messages + [HumanMessage(content=question),
            AIMessage(content=output['message'], additional_kwargs={'dataops_card': output, 'dataops_request_key': key})]
        memory.save(session_id, messages[-history_limit:], context, revision=snapshot.revision)
        return output
