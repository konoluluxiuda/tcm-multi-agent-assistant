"""Shared local hints; unrecognized wording is not a capability rejection."""
import re


def followup(question):
    return bool(re.match(r'\s*(?:请|帮我|再|继续|同时)*\s*(?:增加|新增|添加|补充|加一列|只保留|改为|重查)', question)
                or re.search(r'上一(?:份|版|张)|原来的(?:表|内容)|当前(?:表格|结果)|沿用刚才|旧列', question))


def matches(question):
    if not isinstance(question, str) or question.lstrip().startswith('/'):
        return False
    return followup(question) or bool(re.search(
        r'表格|资料表|对照表|核对表|资料卡|出处表|表里|[Ee]xcel|[Xx][Ll][Ss][Xx]|(?:做成|做个|生成|制作).{0,12}表', question))


def requires_delivery(question):
    return bool(re.search(r'表格|资料表|对照表|核对表|出处表|表里|[Ee]xcel|[Xx][Ll][Ss][Xx]|(?:做成|做个|生成|制作).{0,12}表',question))


def needs_fallback(question, selected):
    if not isinstance(question, str) or question.lstrip().startswith('/'):
        return False
    if selected is None:
        return True
    request=selected.get('request',{})
    if request.get('unsupported'):
        return False
    return (selected.get('reason')=='pharmacopoeia_field_query' or
            request.get('topic') in ('intent_clarification','research_task_review') or
            request.get('topic')=='research_plan' and not any(request.get(k) for k in ('plan','selection','replay')))
