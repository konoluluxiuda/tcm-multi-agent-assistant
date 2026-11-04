"""Shared local hints; unrecognized wording is not a capability rejection."""
import re


def followup(question):
    return bool(re.match(r'\s*(?:请|帮我|再|继续|同时)*\s*(?:增加|新增|添加|补充|加一列|只保留|改为|重查)', question)
                or re.search(r'上一(?:份|版|张)|原来的(?:表|内容)|当前(?:表格|结果)|沿用刚才|旧列', question))


def matches(question):
    if not isinstance(question, str) or question.lstrip().startswith('/'):
        return False
    selection=bool(re.search(r'(?:挑选|挑出|选出|找出|筛出|选取|挑)\s*[0-9一二两三四五六七八九十]+(?:条|味|个)',question)
                   and re.search(r'齐全|完整|都有',question) and re.search(r'功效|注意|贮藏|用量|字段',question))
    return followup(question) or vague_task(question) or selection or bool(re.search(
        r'表格|资料表|对照表|核对表|资料卡|出处表|表里|[Ee]xcel|[Xx][Ll][Ss][Xx]|(?:做成|做个|生成|制作).{0,12}表', question))


def vague_task(question):
    return bool(re.fullmatch(r'\s*(?:请帮我|帮我|请|麻烦你)?\s*(?:整理|汇总|归纳)(?:一下|下|一些|这些|点)?(?:资料|数据|内容)[。！？?!\s]*',question))


def requires_delivery(question):
    return bool(re.search(r'资料卡|表格|资料表|对照表|核对表|出处表|表里|[Ee]xcel|[Xx][Ll][Ss][Xx]|(?:做成|做个|生成|制作).{0,12}表',question))


def needs_fallback(question, selected):
    if not isinstance(question, str) or question.lstrip().startswith('/'):
        return False
    from tcm.dataops.understanding_contract import policy
    if policy()=='unified_v2':
        # Only exact controls and host-bound selections bypass understanding.
        # A plausible regex route alone is not evidence of complete understanding.
        if question.strip().rstrip('。？！?!') in ('你能做什么','当前能做什么','数据概览','介绍两个数据库'):
            return False
        request=(selected or {}).get('request',{})
        if request.get('selection') or request.get('replay') or request.get('followup'):
            return False
        return True
    if selected is None:
        return True
    request=selected.get('request',{})
    if request.get('unsupported'):
        return False
    return (selected.get('reason')=='pharmacopoeia_field_query' or
            request.get('topic') in ('intent_clarification','research_task_review') or
            request.get('topic')=='research_plan' and not any(request.get(k) for k in ('plan','selection','replay')))
