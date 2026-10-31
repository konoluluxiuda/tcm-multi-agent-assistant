"""Shared conservative request checks, not a general entity recognizer."""
import re

UNSUPPORTED = re.compile(r'价格|采购|售价|购买|处方|配药')
# For unseparated Chinese mentions, require a known language boundary instead
# of accepting any substring. Unknown constructions can ask for clarification.
LEFT = re.compile(r'(?:请把|把|将|给|帮|一张|一份|关于|有关|查询|查找|查一下|看看|查看|看|查|整理|介绍|了解|比较|对照|对比|和|与|及|、|药材|中药|中草药|药典中的|药典中|药典里|药典)$')
RIGHT = re.compile(r'^(?:在ETCM|在药典|的|能|可|有|是|找|怎么|怎样|如何|先|请|每|放|与|和|及|都|相关|资料|信息|功效|作用|用量|注意|性状|性味|归经|贮藏|靶点|成分)')


def unsupported_requirement(question):
    return bool(UNSUPPORTED.search(question))


def personal_treatment_request(question):
    """Explicit personal treatment directives, not mentions in research text."""
    for match in re.finditer(r'(?:帮我|给我|为我|替我)(?:治愈|治好|治疗|诊断|开药|开处方)',question):
        if not re.search(r'(?:不要|不需要|无需|别)\s*$',question[:match.start()]):return True
    return False


def chinese_context(before, after):
    left = not before or not re.match(r'[\u4e00-\u9fff]',before[-1]) or LEFT.search(before)
    right = not after or not re.match(r'[\u4e00-\u9fff]',after[0]) or RIGHT.search(after)
    return bool(left and right)
