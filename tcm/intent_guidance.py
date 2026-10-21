"""Bounded clarifications for unsupported actions and missing source selections."""
import re


def relation_with_provenance(text):
    """Only remove a fully consumed evidence-display clause from an explicit query."""
    if not isinstance(text, str) or not 1 <= len(text) <= 800:
        return None
    match = re.fullmatch(
        r'([^，,；;！？?!\n\r]{1,500}?)[，,；;]\s*'
        r'(?:并|同时)?(?:请)?'
        r'(?:(?:把|将)(?:对应的|各条关联的|每条关联的)?'
        r'(?:原始记录|原始行)(?:和出处|与出处)?(?:也|一起|一并)?(?:列出来|展示出来)|'
        r'(?:附上|列出|给出)(?:对应的|各条关联的|每条关联的)?'
        r'(?:原始记录|原始行|出处)(?:和出处|与出处)?)'
        r'[？?。！!]*', text.strip())
    if not match:
        return None
    from tcm.relation_queries import parse
    request = parse(match.group(1).strip())
    if (request is None or request.get('error') or request.get('followup') or
            not request.get('entities')):
        return None
    for entity in request['entities']:
        # A record description or another action is not an explicit source name.
        if (not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,256}', entity) or
                re.search(r'这个|那个|这些|那些|刚才|上次|它|这味药|那味药|该成分|这条|那条|'
                          r'同时|然后|再查|并查|处方|配药|文献|论文|下载', entity)):
            return None
    return {**request, 'source_evidence_requested': True}


def recognize(text, relation_context=None):
    if len(text) > 800:
        return None
    if re.search(r'(?:帮我|给我)(?:选.{0,6}(?:味药|种药)|配药|开(?:个)?(?:方|处方))|'
                 r'(?:我该|我应该|适合我)(?:用|吃|喝).{0,8}药', text):
        return {'topic': 'personal_medication_boundary'}
    if (re.search(r'候选|选项|同名', text) and re.search(r'选哪个|怎么选|该选|几个|多个', text)):
        return {'topic': 'source_selection_help'}
    if ('成分' in text and re.search(r'实际检测|检测到|数据库.{0,15}(?:收录|收进去)', text)
            and re.search(r'是说|还是|意味着|是否', text)):
        return {'topic': 'association_evidence_scope'}
    relation = relation_with_provenance(text)
    if relation is not None:
        return relation
    if ('成分' in text and '靶点' in text and
            re.search(r'从哪.{0,6}来|原始记录|原来的记录|出处', text)):
        generic = re.fullmatch(r'这个成分(?:对应|关联)?靶点的出处(?:在哪|是什么)?[？?。！!]*', text.strip())
        if (generic and isinstance(relation_context, dict) and
                relation_context.get('query_kind') in ('herb_target_paths', 'target_ingredient')):
            return {'topic': 'etcm_relations', 'followup': True, 'action': 'replay'}
        return {'topic': 'relation_source_needed'}
    target = re.fullmatch(
        r'(?:我(?:手里)?(?:只有|有)(?:一个)?)?靶点(?:名字|名称|标识)?\s*[:：]?\s*'
        r'([A-Za-z0-9][A-Za-z0-9.-]{0,31})\s*[，,]?\s*'
        r'(?:能|可以)(?:从它)?(?:找到|查到|查询)(?:相关|关联)?'
        r'(药材和成分|成分和药材|药材|成分)(?:吗)?[？?。！!]*', text.strip())
    if target:
        token, direction = target.groups()
        if direction in ('药材', '成分'):
            return {'topic': 'etcm_relations', 'kind': 'target_herb' if direction == '药材' else 'target_ingredient',
                    'entities': [token], 'page': 1}
        return {'topic': 'target_direction_needed', 'target': token}
    return None
