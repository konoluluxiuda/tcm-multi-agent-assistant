"""Bounded local question compiler. Unconsumed text prevents execution.

This grammar is NOT a general LLM planner. Catalog aliases are read locally;
candidate names remain unverified. No tool selection, model dispatch or writes.
"""
import re

from tcm.dataops.contracts import Goal, SID


DOCUMENT_ALIASES = {
    '功能与主治': ('功能与主治', '功能和主治', '功效与主治', '功效', '主治', '功能', '作用'),
    '注意': ('注意事项', '注意', '禁忌'),
    '贮藏': ('贮藏', '储藏', '储存', '保存方法', '保存条件'),
    '炮制': ('炮制方法', '炮制'),
    '来源': ('药典来源', '基原'),
    '性味与归经': ('性味与归经', '性味归经', '性味', '归经'),
    '用法与用量': ('用法与用量', '用法用量', '用量', '用法', '剂量'),
}
ATTRIBUTE_ALIASES = {'medicinal_part': ('药用部位',), 'latin_name': ('拉丁名', '拉丁学名'),
    'name_pinyin': ('拼音名',), 'english_name': ('英文名',), 'family': ('所属科', '科名'),
    'habitat': ('产地', '生境'), 'efficacy_class': ('功效分类',),
    'properties': ('ETCM性味', '原始性味'), 'flavors': ('原始五味',),
    'meridians': ('ETCM归经', '原始归经'), 'indications': ('适应描述',)}
NUMBERS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9}


def number(text):
    if text.isascii() and text.isdecimal():
        return int(text)
    if text in NUMBERS:
        return NUMBERS[text]
    if text.count('十') == 1:
        left, right = text.split('十')
        if (not left or left in NUMBERS) and (not right or right in NUMBERS):
            return NUMBERS.get(left, 1) * 10 + NUMBERS.get(right, 0)
    return None


def propose(question, catalog, *, parent=None):
    remaining = list(question)
    recognized, issues, defaults = [], [], []

    def consume(pattern, kind, flags=0):
        matches = list(re.finditer(pattern, ''.join(remaining), flags))
        for match in matches:
            recognized.append({'kind': kind, 'text': question[match.start():match.end()],
                               'start': match.start(), 'end': match.end()})
            remaining[match.start():match.end()] = ' ' * (match.end() - match.start())
        return matches

    strict = bool(consume(r'(?:要求|必须)?(?:全部字段补齐|字段全部补齐|不得有缺项|不能有缺项)', 'completion_policy'))
    refresh = bool(consume(r'(?:同时)?重查(?:有问题|存在对应问题|存在差异)的记录', 'refresh_conflicts'))
    consume(r'(?:保留|附上|标注|带上|提供)(?:原始)?(?:来源|出处)(?:及(?:JSON位置|页码))?', 'provenance')
    consume(r'(?:保留旧(?:值|字段)|不重查旧字段|生成(?:下|新)一版|生成v[2-9]|生成V[2-9])', 'revision')
    replace_matches = consume(r'只保留|改为|只查', 'replace_fields')
    replace = bool(replace_matches)
    consume(r'增加|新增|添加|补充|再加', 'add_fields')

    filters = []
    for match in consume(r'(药用部位|功效分类|所属科)(缺失|包含|等于|为)(?:[“"]([^”"]{1,200})[”"]|([A-Za-z0-9_-]+))?', 'filter'):
        field, operation, quoted, plain = match.groups()
        value = quoted or plain or ''
        if operation != '缺失' and not value or operation == '缺失' and value:
            issues.append('筛选值不明确；可写“药用部位包含Root”，或用双引号括住中文原始值。')
        else:
            filters.append({'field': {'药用部位': 'medicinal_part', '功效分类': 'efficacy_class', '所属科': 'family'}[field],
                            'op': {'为': 'equals', '等于': 'equals', '包含': 'contains', '缺失': 'missing'}[operation],
                            'value': value})

    quantities = consume(r'(?:前)?([0-9一二两三四五六七八九十百]+)(?:条|味|个)(?:药材|记录)?', 'limit')
    limits = [number(m.group(1)) for m in quantities]
    if any(n is None or not 1 <= n <= 50 for n in limits) or len(set(limits)) > 1:
        issues.append('每次请选择1–50条记录，并只指定一个条数。')
    limit = limits[0] if limits and limits[0] is not None and 1 <= limits[0] <= 50 else 20
    explicit_ids = [m.group() for m in consume(SID.strip('^$'), 'source_id')]

    names = []
    # Longest local alias first; Latin aliases must match whole words.
    for name in sorted(catalog, key=lambda x: (-len(x), x)):
        pattern = re.escape(name)
        if name.isascii():
            pattern = r'(?<![A-Za-z0-9_])' + pattern + r'(?![A-Za-z0-9_])'
        if consume(pattern, 'name', re.IGNORECASE):
            names.append(name)
    # Two aliases of the same record should not multiply the selected objects.
    selected = sorted({sid for name in names for sid in catalog[name]} | set(explicit_ids))
    if selected and not limits:
        limit = 50
    if len(selected) > 50:
        issues.append('名称对应超过50条来源记录，请缩小对象范围。')
    if not selected and not limits and not parent:
        defaults.append('未指定条数，按来源ID稳定排序取前20条。')
    if explicit_ids and names:
        issues.append('请使用药材名称或来源ID中的一种方式指定对象。')

    fields, attributes = [], []
    aliases = [(alias, field, output) for output, mapping in ((fields, DOCUMENT_ALIASES), (attributes, ATTRIBUTE_ALIASES))
               for field, words in mapping.items() for alias in words]
    for alias, field, output in sorted(aliases, key=lambda item: -len(item[0])):
        if consume(re.escape(alias), 'field') and field not in output:
            output.append(field)
    if replace_matches and any(r['kind'] == 'field' and r['end'] <= replace_matches[0].start() for r in recognized):
        issues.append('字段替换要求不明确；请写“只保留贮藏”等最终字段列表。')
    # Only connective/task words are ignored; unknown requirements survive verbatim.
    words = ('请帮我', '帮我', '我想要', '我需要', '我想', '麻烦', '请', '帮忙', '整理', '核对', '查询', '查看',
             '筛选', '选取', '导出', '生成', '制作', '对比', '比较', '表格', '核对表', '资料', '药材', '记录',
             '药典', '字段', '一列', '几列', '一份', '一个', '这些', '它们', '它的', '当前结果', '当前', '之前',
             '原来', '继续', '再', '的', '和', '与', '及', '以及', '并且', '并', '同时', '把', '将', '给', '为',
             '中', '里', '等', '呢', '吧', '一下', '一下子', '保留', '下载', 'ETCM', 'etcm')
    consume('|'.join(re.escape(word) for word in sorted(words, key=lambda x: -len(x))), 'connective')
    remainder = ''.join(remaining)
    # Punctuation/whitespace is not a requirement. Keep every other unmatched span.
    unresolved = [s for s in re.split(r'[\s，,。；;！!？?、：:（）()“”"‘’]+', remainder) if s]
    if unresolved:
        issues.append('以下要求尚不能可靠处理：' + '、'.join(unresolved))
    if not selected and not filters and not limits and not parent:
        issues.append('请提供药材名称、明确筛选条件或要整理的记录条数。')
    if refresh and not parent:
        issues.append('重查问题记录需要先打开已有结果。')
    if replace and not parent:
        issues.append('修改字段需要先打开已有结果。')

    refresh_ids = []
    if parent:
        if parent['status'] not in ('complete', 'partial') or not parent.get('result') or not parent.get('artifact'):
            issues.append('当前任务尚未形成可修改的结果。')
        old = parent['goal']
        parent_ids = sorted(r['source_id'] for r in (parent.get('result') or {}).get('rows', []))
        if selected or filters or limits:
            issues.append('连续修改保留原记录集合；更换对象或筛选条件请新建任务。')
        if not fields and not attributes and not refresh:
            issues.append('请说明要增加或修改的字段。')
        query = {'source_ids': parent_ids, 'limit': 50}
        if replace:
            defaults.append('字段替换仅调整药典列，ETCM属性及原记录集合继续保留。')
        fields = list(dict.fromkeys(([] if replace else old['document_fields']) + fields))
        attributes = list(dict.fromkeys(old['attributes'] + attributes))
        strict = strict or old['completion_policy'] == 'require_all_fields'
        if refresh:
            problem_ids = {item['source_id'] for item in parent['result'].get('issues', [])}
            refresh_ids = [r['source_id'] for r in parent['result']['rows']
                           if r['source_id'] in problem_ids or r['mapping_status'] != 'fields_consistent_not_verified']
    else:
        query = {'source_ids': selected, 'limit': limit} if explicit_ids else {
            'names': names, 'filters': filters, 'limit': limit}
    if not fields:
        fields = ['功能与主治', '注意']
        defaults.append('未指定药典字段，使用功能与主治、注意。')
    if not attributes:
        attributes = ['medicinal_part', 'latin_name']
        defaults.append('附带ETCM药用部位、拉丁名。')
    goal = None
    try:
        goal = Goal(query=query, attributes=attributes, document_fields=fields,
                    completion_policy='require_all_fields' if strict else 'audit').model_dump()
    except ValueError:
        issues.append('目标超出支持范围；每次最多4个药典字段、50条记录和5个筛选条件。')
    return {'status': 'needs_input' if issues else 'ready', 'question': question,
            'compiler': 'local_bounded_grammar_v1', 'external_model_calls': 0,
            'goal': None if issues else goal, 'recognized': recognized, 'unresolved': unresolved,
            'issues': list(dict.fromkeys(issues)), 'defaults': defaults,
            'refresh_source_ids': refresh_ids, 'parent_id': parent['id'] if parent else None,
            'expected_revision': parent['revision'] if parent else None}
