"""Conservative query and field completeness checks; never infer medical equivalence."""
import re

VERSION = 1
PROCESS_PREFIXES = ('酒炙', '醋炙', '蜜炙', '盐炙', '酒制', '醋制', '姜制', '盐制',
                    '炒', '炙', '煅', '蒸', '煮', '烫', '煨', '酒', '醋', '蜜', '盐', '姜',
                    '生', '熟', '制', '清', '法', '焦')
# These are supported language contexts, not an open-ended Chinese entity parser.
LEFT_CONTEXT = re.compile(r'(?:查询|查找|查一下|查一查|查|介绍一下|介绍|解释|请问|问一下|问|关于|药典中|药典中的|药典里|药典记载的|药典|比较|对比|以及|还是|和|与|用|中药材|中草药|中药|药材|根据药典)$')
RIGHT_CONTEXT = re.compile(r'^(?:的|有|是|可|来|应|该|能|需|要|归|以|怎|用|每|入|取|服|剂|功|注|贮|主|性|储|保|治疗|价格|市场|今天|厂家|和|与|还是|及|、|[，,。.;；：:?？!！）)」】>])')


def context_check(text, spans, field_words=()):
    """Separate exact mentions, unsupported preparations and unmatched noun context."""
    accepted, variants, rejected = set(), [], set()
    for start, end, name in spans:
        before, after = text[:start], text[end:]
        prefix = next((p for p in PROCESS_PREFIXES if before.endswith(p)), None)
        if prefix:
            variants.append({'requested_name': prefix + name, 'parent_name': name})
            continue
        left_ok = not before or not before[-1].isalnum() or LEFT_CONTEXT.search(before)
        right_ok = (not after or RIGHT_CONTEXT.search(after) or after.startswith('配伍')
                    or any(after.startswith(word) for word in field_words))
        if left_ok and right_ok:
            accepted.add(name)
        else:
            rejected.add(name)
    return sorted(accepted), variants, sorted(rejected)


def field_body(text):
    return re.sub(r'\s+', '', re.sub(r'【[^】]*】', '', text))


def reference_target(text):
    match = re.fullmatch(r'(?:同|参见|见)([\u4e00-\u9fff]{2,20})[。.]?', field_body(text))
    return match.group(1) if match else None
