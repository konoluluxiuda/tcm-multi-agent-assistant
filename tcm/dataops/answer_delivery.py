"""Owner-bound query delivery: execute once, aggregate, persist, and restore."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from agents.conversation_store import MemoryUnavailable, ConversationConflict, SessionExpired
from agents.runtime import session_turn
from tcm.answer_text import plain_answer
from tcm.request_journal import RequestJournal
from tcm.dataops import diagnostics
from tcm.dataops.data_access import SourceChanged

VERSION = 'answer-delivery-v2'
RESULT_KEYS = {'evidence_answer': 'evidence_answer', 'assistant_result': 'assistant',
               'tcm_result': 'tcm', 'research_result': 'research', 'pubmed_result': 'pubmed',
               'delivery_error': 'delivery_error'}
COMPLETE = {'ok', 'candidates', 'complete', 'context_cleared'}
INCOMPLETE = {'partial', 'partial_evidence', 'partial_fetch', 'no_evidence', 'no_match',
              'not_found', 'no_results', 'clarification_needed', 'name_mapping_candidates',
              'name_not_resolved', 'field_not_found', 'queued', 'running', 'waiting_input'}


def status_code(result):
    return 503 if result['status'] == 'unavailable' else 200


def merge_answer_datas(datas, routes):
    if not routes or len(datas) != len(routes):
        raise ValueError('Delivery must cover every route')
    steps = []
    for index, (data, route) in enumerate(zip(datas, routes), 1):
        key = next((k for k in RESULT_KEYS if data.get(k) is not None), None)
        obj = data.get(key) if key else {'status': 'invalid_tool_result', 'message': '工具未返回可核对的结果。'}
        status = obj.get('status') or 'invalid_tool_result'
        outcome = 'success' if status in COMPLETE else 'partial' if status in INCOMPLETE else 'unavailable'
        message = obj.get('message') or (data.get('messages') or [AIMessage(content='工具未提供结果说明。')])[-1].content
        text = plain_answer({RESULT_KEYS.get(key, 'delivery_error'): obj, 'response': message})
        steps.append({'step_id': f's{index}', 'tool': route.get('tool'), 'result_key': key,
                      'status': status, 'outcome': outcome, 'result': obj,
                      'route': {k: route[k] for k in ('tool', 'request') if k in route},
                      'message': message, 'text': text})
    overall = ('success' if all(s['outcome'] == 'success' for s in steps) else
               'unavailable' if all(s['outcome'] == 'unavailable' for s in steps) else 'partial')
    unfinished = [{'step_id': s['step_id'], 'tool': s['tool'], 'status': s['status'],
                   'message': s['message'], 'missing_fields': s['result'].get('missing_fields', []),
                   'failure': s['result'].get('failure')} for s in steps if s['outcome'] != 'success']
    merged = {'status': overall, 'answer_steps': steps, 'unfinished': unfinished,
              'response': '\n\n'.join(s['text'] for s in steps if s['text'])}
    # Preserve the existing single-tool API contract. Multi-tool consumers must
    # use answer_steps, rather than mistake a first-tool alias for the full result.
    if len(steps) == 1 and steps[0]['result_key'] in RESULT_KEYS and steps[0]['result_key'] != 'delivery_error':
        merged[RESULT_KEYS[steps[0]['result_key']]] = steps[0]['result']
    return merged


def execute_routes(process_query, question, session_id, routes, *, request_key=None, context_sink=None):
    """Execute read tools without committing intermediate conversation turns."""
    if not session_id or not question or not routes or len(routes) > 100:
        raise ValueError('A bounded delivery requires a question and stable session')
    datas = []
    for index, route in enumerate(routes, 1):
        step = f's{index}'
        diagnostics.record('answer_step', 'started', step_id=step, tool=route.get('tool'))
        try:
            data = process_query(question, session_id=session_id, resolved_request=route,
                                 persist_turn=False, request_key=f'{request_key}#{step}' if request_key else None)
        except (MemoryUnavailable, ConversationConflict, SessionExpired, SourceChanged):
            raise
        except HTTPException as exc:
            if exc.status_code in (401, 403, 409, 410):
                raise
            data = _failed(exc)
        except Exception as exc:
            data = _failed(exc)
        # Invalid/malformed tool output is also a failed step, not a lost delivery.
        try:
            one = merge_answer_datas([data], [route])
        except Exception as exc:
            data = _failed(exc)
            one = merge_answer_datas([data], [route])
        diagnostics.record('answer_step', 'returned', step_id=step, tool=route.get('tool'),
                           tool_status=one['answer_steps'][0]['status'], outcome=one['status'])
        datas.append(data)
        if context_sink is not None:
            context_sink.append({k: deepcopy(data[k]) for k in
                                 ('tcm_context', 'research_context', 'relation_context') if k in data})
    return merge_answer_datas(datas, routes)


def _failed(exc):
    diagnostics.record('answer_step_error', 'failed', exception_type=type(exc).__name__,
                       location=diagnostics.error_location(exc))
    message = '此项查询执行失败，已保留其他结果；未自动重试。'
    return {'delivery_error': {'status': 'tool_execution_failed', 'message': message,
            'failure': {'code': 'tool_execution_failed', 'stage': 'query_execution', 'message': message}},
            'messages': [AIMessage(content=message)]}


def _journal(root):
    return RequestJournal(Path(root) / 'answer_deliveries.sqlite3', label='查询交付')


def read_answer(root, owner, key):
    saved = _journal(root).get(owner, key)
    if saved is None:
        raise KeyError('No completed delivery')
    return {**deepcopy(saved['answer']), 'replayed': True, 'model_calls': 0,
            'understanding_requests_this_submission': 0, 'tool_calls_this_submission': 0,
            'counts_scope': 'this_submission'}


def _commit(memory, session_id, key, saved, history_limit):
    snapshot = memory.load(session_id)
    old = snapshot.tcm_context
    context = deepcopy(old) if isinstance(old, dict) and old.get('kind') == 'agent_contexts_v1' else {
        'kind': 'agent_contexts_v1', 'herb': old, 'research': None, 'pubmed': None, 'relation': None}
    # Multiple objects in the same tool have no unambiguous implicit follow-up.
    # Full results remain available by delivery reference, not in the model history.
    for field, key_name in [('tcm_context', 'herb'), ('research_context', 'research'), ('relation_context', 'relation')]:
        values = [x[field] for x in saved['contexts'] if field in x and x[field] is not None]
        if values:
            context[key_name] = values[0] if all(v == values[0] for v in values) else None
    answer = saved['answer']
    summary = f"查询{ {'success':'完成','partial':'部分完成','unavailable':'未完成'}[answer['status']]}，共{len(answer['answer_steps'])}项；完整结果与出处已保存。"
    messages = snapshot.messages + [HumanMessage(content=saved['question']), AIMessage(content=summary,
        additional_kwargs={'answer_delivery_key': key})]
    return memory.save(session_id, messages[-history_limit:], context, revision=snapshot.revision,
                       turn_key='answer:' + key)


def deliver_saved_query(*, root, memory, session_id, request_key, sources_provider,
                        process_query, history_limit=30, understanding_calls=0):
    """Both HTTP entry points use this coordinator; clients never supply routes."""
    if not memory.valid_cookie(session_id):
        raise HTTPException(401, '请先初始化会话。')
    owner = memory.key(session_id)
    with session_turn(session_id):
        memory.load(session_id)  # reject deleted/expired sessions before any execution
        proposal = RequestJournal(Path(root) / 'question_proposals.sqlite3', label='任务解析').get(owner, request_key)
        if not proposal or proposal.get('status') != 'query':
            raise KeyError('No owner-bound query proposal')
        question = proposal.get('question')
        if not question or not proposal.get('source_revision'):
            raise HTTPException(409, '旧提案缺少执行绑定，请使用新的请求编号重新提交。')
        routes = proposal.get('routes') or [proposal['route']]
        diagnostics.bind(root, owner, question=question, request_key=request_key)
        signature = hashlib.sha256(json.dumps({'version': VERSION, 'proposal': proposal},
                                              ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        journal = _journal(root)
        with sources_provider() as sources:
            if sources.revision != proposal['source_revision']:
                raise SourceChanged('Source changed since understanding')
            saved = journal.reserve(owner, request_key, signature)
            replayed = saved is not None
            if saved is None:
                contexts = []
                answer = execute_routes(process_query, question, session_id, routes,
                                        request_key=request_key, context_sink=contexts)
                sources.assert_unchanged()
                answer.update(agent='资料查询', request_key=request_key, source_revision=sources.revision,
                              original_provider_requests=proposal.get('external_model_calls', 0),
                              tool_calls_total=len(routes), delivery_version=VERSION)
                saved = {'answer': answer, 'question': question, 'contexts': contexts}
                # Persist before committing memory. save(turn_key=...) makes recovery
                # after either write idempotent, including after history truncation.
                journal.complete(owner, request_key, saved)
            _commit(memory, session_id, request_key, saved, history_limit)
        diagnostics.record('answer_delivery', 'replayed' if replayed else 'completed',
                           step_count=len(routes), result_status=saved['answer']['status'])
        return {**deepcopy(saved['answer']), 'replayed': replayed,
                'model_calls': understanding_calls, 'understanding_requests_this_submission': understanding_calls,
                'tool_calls_this_submission': 0 if replayed else len(routes), 'counts_scope': 'this_submission'}
