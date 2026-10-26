"""Shared conservative request checks, not a general entity recognizer."""
import re

UNSUPPORTED = re.compile(r'价格|采购|售价|购买|处方|配药')
# For unseparated Chinese mentions, require a known language boundary instead
# of accepting any substring. Unknown constructions can ask for clarification.
LEFT = re.compile(r'(?:请把|把|将|给|帮|一张|一份|关于|有关|查询|查找|查一下|看看|查看|看|查|整理|介绍|了解|比较|对照|对比|和|与|及|、|药材|中药|中草药|药典中的|药典中|药典里)$')
RIGHT = re.compile(r'^(?:的|能|可|有|是|找|怎么|怎样|如何|先|请|每|放|与|和|及|都|相关|资料|信息|功效|作用|用量|注意|性状|性味|归经|贮藏|靶点|成分)')


def unsupported_requirement(question):
    return bool(UNSUPPORTED.search(question))


def chinese_context(before, after):
    left = not before or not re.match(r'[\u4e00-\u9fff]',before[-1]) or LEFT.search(before)
    right = not after or not re.match(r'[\u4e00-\u9fff]',after[0]) or RIGHT.search(after)
    return bool(left and right)
