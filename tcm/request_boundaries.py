"""Shared conservative request checks, not a general entity recognizer."""
import re

UNSUPPORTED = re.compile(r'价格|采购|售价|购买|处方|配药|(?P<transaction>下单|联系(?:商家|药店|卖家))')
# For unseparated Chinese mentions, require a known language boundary instead
# of accepting any substring. Unknown constructions can ask for clarification.
LEFT = re.compile(r'(?:请把|把|将|给|帮|一张|一份|关于|有关|查询|查找|查一下|看看|查看|看|查|整理|介绍|了解|知道|告诉我|核实|核对|比较|对照|对比|和|与|及|、|药材|中药|中草药|(?:ETCM|药典)(?:中的|里的|中|里)?)$',re.I)
RIGHT = re.compile(r'^(?:在ETCM|在药典|的|能|可|有|是|找|怎么|怎样|如何|先|请|每|放|与|和|及|都|各(?:自)?(?:有|的|查|列|是)|分别(?:有|查|列|是)|均(?:有|是)|相关|资料|信息|功效|作用|用量|注意|性状|性味|归经|贮藏|靶点|成分)',re.I)


def unsupported_requirement(question):
    for match in UNSUPPORTED.finditer(question):
        if match.group('transaction') and re.search(r'(?:不要|不需要|无需|别)(?:替我|帮我|给我)?\s*$',question[:match.start()]):continue
        return True
    return False


def personal_treatment_request(question):
    """Explicit personal treatment directives, not mentions in research text."""
    for match in re.finditer(r'(?:帮我|给我|为我|替我)(?:治愈|治好|治疗|诊断|开药|开处方)',question):
        if not re.search(r'(?:不要|不需要|无需|别)\s*$',question[:match.start()]):return True
    # Personal directives can refer to family members rather than the speaker.
    # Require both an addressee and a treatment action; research mentions alone
    # (e.g. looking up chemotherapy-related records) are not treatment requests.
    for clause in re.split('[，,。；;！？?!]',question):
        if re.search(r'(?:不要|不需要|无需|别)\s*(?:再)?(?:请)?(?:给|为|替|帮)',clause):continue
        addressee=r'(?:我|他|她|患者|病人|我(?:的)?(?:父亲|母亲|爸爸|妈妈|家人|孩子)|(?:这位|这名)(?:患者|病人))'
        if re.search(r'(?:给|为|替|帮)'+addressee+r'.{0,12}(?:治愈|治好|治疗|诊断|开药|开处方|(?:安排|推荐|配|选|开).{0,12}(?:中药|药方|用药|处方))',clause):return True
    return False


def chinese_context(before, after):
    left = not before or not re.match(r'[\u4e00-\u9fff]',before[-1]) or LEFT.search(before)
    right = not after or not re.match(r'[\u4e00-\u9fff]',after[0]) or RIGHT.search(after)
    return bool(left and right)
