"""Shared scalar-field source policy; no corpus access or source fallback."""
import re


# (namespace, stored fields, user-facing aliases). Shared labels default to
# ETCM; an explicit pharmacopoeia qualifier selects its combined source field.
FIELD_GROUPS = (
    ('etcm', ('properties', 'flavors'), ('性味',)),
    ('etcm', ('properties',), ('药性',)),
    ('etcm', ('flavors',), ('药味',)),
    ('etcm', ('meridians',), ('归经',)),
    ('etcm', ('properties', 'flavors', 'meridians'), ('性味与归经', '性味归经')),
    ('etcm', ('family',), ('科名', '所属科')),
    ('etcm', ('medicinal_part',), ('药用部位',)),
    ('etcm', ('latin_name',), ('拉丁名', '拉丁学名')),
    ('etcm', ('english_name',), ('英文名',)),
    ('etcm', ('name_pinyin',), ('拼音名',)),
    ('etcm', ('habitat',), ('产地', '生境')),
    ('etcm', ('efficacy_class',), ('功效分类',)),
    ('pharmacopoeia', ('功能与主治',), ('功能与主治', '功能和主治', '功效与主治', '功效', '主治', '功能', '作用')),
    ('pharmacopoeia', ('用法与用量',), ('用法与用量', '用法用量', '剂量', '用量', '用法')),
    ('pharmacopoeia', ('注意',), ('注意事项', '注意', '禁忌')),
    ('pharmacopoeia', ('贮藏',), ('贮藏', '储藏', '储存', '保存方法', '保存条件')),
    ('pharmacopoeia', ('炮制',), ('炮制方法', '炮制')),
    ('pharmacopoeia', ('来源',), ('基原',)),
    ('pharmacopoeia', ('性状',), ('性状',)),
    ('pharmacopoeia', ('鉴别',), ('鉴别',)),
    ('pharmacopoeia', ('检查',), ('检查',)),
    ('pharmacopoeia', ('含量测定',), ('含量测定',)),
)
DEFAULTS = {alias: (ns, fields) for ns, fields, aliases in FIELD_GROUPS for alias in aliases}
FIELDS = {ns: {alias: fields for alias, (source, fields) in DEFAULTS.items() if source == ns}
          for ns in ('etcm', 'pharmacopoeia')}
FIELDS['pharmacopoeia'].update({alias: ('性味与归经',)
    for alias in ('性味', '药性', '药味', '归经', '性味与归经', '性味归经')})
FIELDS['pharmacopoeia']['来源'] = ('来源',)  # Bare 来源 still needs clarification.


def parse_fields(text):
    """Consume a complete field list, keeping an explicit source until changed.

    With no qualifier, each field uses its own default, allowing 性味和剂量.
    An unsupported field under an explicit source fails instead of switching.
    """
    fields = []
    source = None
    while text:
        qualifier = re.match(r'(ETCM|药典)(?:中|里)?(?:的)?', text, re.I)
        if qualifier:
            source = 'etcm' if qualifier[1].upper() == 'ETCM' else 'pharmacopoeia'
            text = text[qualifier.end():]
        mapping = FIELDS[source] if source else DEFAULTS
        label = next((label for label in sorted(mapping, key=len, reverse=True)
                      if text.startswith(label)), None)
        if label is None:
            return None
        ns, names = (source, mapping[label]) if source else mapping[label]
        fields.extend((ns, name) for name in names)
        text = text[len(label):]
        if not text:
            break
        separator = re.match(r'以及|[、和与及，,]', text)
        if not separator or separator.end() == len(text):
            return None
        text = text[separator.end():]
    return ([{'namespace': ns, 'name': name} for ns, name in dict.fromkeys(fields)]
            if fields else None)


def source_policy_prompt():
    rows = ['/'.join(aliases) + ' → ' + ns + ':' + ','.join(fields)
            for ns, fields, aliases in FIELD_GROUPS]
    return ('\n字段默认来源（仅在用户未指定来源时使用，不默认添加任何未请求字段）：\n'
            + '\n'.join(rows)
            + '\n明确指定来源优先；药典的性味、药性、药味、归经读取其“性味与归经”合并字段。'
            '同一明确来源下连续列举的字段沿用该来源，直到另行指定。'
            '未指定来源的“性味和剂量”分别读取上述ETCM与药典字段，无需再次确认来源。'
            '剂量仅查询原文用法与用量，不推断个体用药。'
            '指定来源不支持字段时说明限制；资料缺失保留缺失，不自动换源或叠加另一来源。'
            '单独“来源”需区分基原与引用出处；“属性/相关信息”需澄清具体字段。')
