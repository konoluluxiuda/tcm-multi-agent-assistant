"""Bounded field interpretation for reports; source/entity guards remain authoritative."""
import re
from tcm.mineru_retrieval import INTENTS, PARAMETERS, normalized

VERSION = 1
# Complete request patterns, not unrestricted substring entity replacement.
STORAGE = r'(?:买回来|买回后|购入后)?(?:放在哪里保存|应该怎么保存|怎么保存|如何保存|怎样存放|怎么储存)'
USAGE = r'(?:它)?(?:主要|通常|一般)?(?:用来做什么|用来干什么|有什么用途|有什么作用)'


def interpret_report_request(herb, question):
    if not isinstance(question, str) or len(question) > PARAMETERS['max_query_chars']:
        raise ValueError('Report query must be text of at most 500 characters')
    text = normalized(question)
    entity = re.escape(normalized(herb))
    tail = r'[？?。.!！]*'
    field = None
    if re.fullmatch(entity + r'(?:的)?' + STORAGE + tail, text):
        field = '贮藏'
    elif re.fullmatch(r'(?:整理|查阅|查看)' + entity + r'的资料[，,]?(?:想知道|想了解|了解一下)' + USAGE + tail, text) or re.fullmatch(entity + r'(?:的)?' + USAGE + tail, text):
        field = '功能与主治'
    fields = [label for label, aliases in INTENTS.items() if any(a in text for a in aliases)]
    if field and field not in fields:
        fields.append(field)
    priority = None
    match = re.search(r'(?:重点是|优先展示|先列|先看|先展示)([^，,。；;？?！!]+)', text)
    if match and not text[:match.start()].endswith(('不要', '不必', '无需', '别', '不')):
        priorities = [label for label, aliases in INTENTS.items()
                      if any(a in match[1] for a in aliases)]
        if len(priorities) == 1 and priorities[0] in fields:
            priority = priorities[0]
    return {'version': VERSION, 'herb': herb, 'original_question': question,
            'fields': fields, 'priority_field': priority,
            'parse_source': 'bounded_paraphrase' if field else 'existing_field_aliases',
            'search_question': herb + '的' + field if field else question}


def organize_fields(hits, request, limit=6):
    """Explicit presentation preference takes precedence over relevance ordering."""
    priority = request['priority_field']
    ordered = sorted(hits, key=lambda h: h['label'] != priority) if priority else list(hits)
    selected = ordered[:limit]
    observed = {h['label'] for h in selected}
    if observed & {'性味', '性味与归经'}:
        observed.update(('性味', '性味与归经'))
    missing = [f for f in request['fields'] if f not in observed]
    return selected, missing


def bounded_field_request(question):
    """Extract a complete written entity; the source retriever must validate it."""
    if not isinstance(question, str) or not 1 <= len(question) <= PARAMETERS['max_query_chars']:
        return None
    text = normalized(question)
    entity = r'(?P<herb>[\u4e00-\u9fff]{2,20}?)'
    tail = r'[？?。.!！]*'
    patterns = (entity + r'(?:的)?' + STORAGE + tail,
                r'(?:整理|查阅|查看)' + entity + r'的资料[，,]?(?:想知道|想了解|了解一下)' + USAGE + tail,
                entity + r'(?:的)?' + USAGE + tail)
    for pattern in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return interpret_report_request(match['herb'], question)
    return None
