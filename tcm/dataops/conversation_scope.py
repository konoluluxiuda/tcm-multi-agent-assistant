"""Owner-local, bounded conversational scope; never treat a mention as verified identity."""
import re
from langchain_core.messages import HumanMessage

REFERENCE = re.compile(r'^(?:请)?(?:这|那)(?P<count>两|二|三|几|些|2|3)(?:(?:个|味)(?:草药|药材|中药|药)?|草药|药材|中药|药)(?:的)?(?P<field>.+)$')


def subject(question):
    text = question.strip().rstrip('。.!！?？')
    match = re.fullmatch(r'(?:请)?(?:我想了解|我想知道|帮我查|查一下|查看|查询|分别告诉我|分别查询|告诉我|给出|把)?(.+?)的(.+)', text)
    if not match or REFERENCE.fullmatch(text):
        return None
    names = re.split(r'[、和与]', match[1])
    if not 1 <= len(names) <= 5 or len(set(names)) != len(names):
        return None
    if any(not re.fullmatch(r'[\u4e00-\u9fffA-Za-z]{2,30}', n) for n in names):
        return None
    return names, match[2]


def prepare(question, snapshot):
    """Only the latest user turn supplies antecedents; no cross-session or stale search."""
    text = question.strip().rstrip('。.!！?？')
    ref = REFERENCE.fullmatch(text)
    current = subject(question)
    context = {'original_question': question, 'resolved_question': question,
               'object_mentions': current[0] if current else [], 'inherited': False}
    if not ref:
        return context
    previous = None
    for message in reversed(snapshot.messages):
        if isinstance(message, HumanMessage):
            previous = message
            break
    if previous is None:
        context['needs_context'] = True
        return context
    saved = previous.additional_kwargs.get('query_scope', {})
    names = saved.get('object_mentions') or (subject(previous.content) or ([], ''))[0]
    count = {'两': 2, '二': 2, '三': 3, '2': 2, '3': 3}.get(ref['count'])
    if not names or count is not None and len(names) != count:
        context['needs_context'] = True
        return context
    for message in reversed(snapshot.messages):
        if message is previous:
            break
        feedback = message.additional_kwargs.get('result_feedback')
        if isinstance(feedback, dict):
            context['previous_result_feedback'] = {k:feedback.get(k) for k in ('status','action','step_ids')}
            break
    field = re.sub(r'(?:是什么|有哪些)$', '', ref['field'])
    context.update(object_mentions=names, inherited=True, previous_question=previous.content[:500],
                   resolved_question='查询'+'和'.join(names)+'的'+field)
    return context


def remember_unhandled(memory, session_id, question, message, *, request_key=None, history_limit=30):
    """Persist a routing rejection without marking the request executed."""
    from agents.runtime import session_turn
    from langchain_core.messages import AIMessage
    with session_turn(session_id):
        snapshot = memory.load(session_id)
        if request_key:
            for item in snapshot.messages:
                if item.additional_kwargs.get('unhandled_request_key') == request_key:
                    if item.additional_kwargs.get('question') != question:
                        from fastapi import HTTPException
                        raise HTTPException(409, '此请求编号已用于另一问题。')
                    return
        messages = snapshot.messages + [HumanMessage(content=question,
            additional_kwargs={'query_scope': prepare(question, snapshot)}),
            AIMessage(content=message, additional_kwargs={'unhandled_request_key': request_key,
                'question': question, 'status': 'not_executed'})]
        memory.save(session_id, messages[-history_limit:], snapshot.tcm_context, revision=snapshot.revision)
