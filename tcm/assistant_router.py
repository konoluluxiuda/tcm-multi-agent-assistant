"""Bounded natural-language routing. Resolves tools, never invents entities or SQL."""
import re

from features import env_bool
from tcm.agent_pubmed import parse_request as pubmed_request
from tcm.agent_research import parse_request as research_request, runtime
from tcm.chat import parse_request as herb_request, FIELD_NAMES
from tcm.mineru_chat import SOURCE_QUERY, comparison_request
from tcm.mineru_retrieval import INTENTS, usage_paraphrase

FIELDS = sorted({a for aliases in INTENTS.values() for a in aliases}, key=len, reverse=True)
FIELD_PATTERN = '|'.join(map(re.escape, FIELDS))
HERB_PATTERN = '|'.join(map(re.escape, sorted(FIELD_NAMES, key=len, reverse=True)))
POLITE = r'(?:请帮我|帮我|麻烦你|麻烦|请)?\s*'


def route(tool, request, reason, mode='natural'):
    return {'tool': tool, 'request': request, 'reason': reason, 'mode': mode}


def resolve(query, *, herb_context=None, research_context=None, pubmed_context=None, relation_context=None):
    if isinstance(query, dict):
        if 'image' in query:
            return None
        query = query.get('text', '')
    if not isinstance(query, str):
        return None
    text = query.strip()
    # Commands preserve the existing parser contracts, including errors and bounds.
    if text.startswith('/'):
        from tcm.analysis_chat import parse as analysis_request
        parsed=analysis_request(text,explicit=True)
        if parsed is not None:return route('assistant',parsed,'explicit_analysis','explicit')
        from tcm.relation_queries import parse as relation_request
        parsed = relation_request(text, explicit=True)
        if parsed is not None:
            return route('assistant', parsed, 'explicit_relation_query', 'explicit')
        for tool, parser in [('pubmed', pubmed_request), ('research', research_request), ('herb', herb_request)]:
            parsed = parser(text)
            if parsed is not None:
                return route(tool, parsed, 'explicit_command', 'explicit')
        return None
    if not env_bool('ENABLE_NATURAL_ROUTING'):
        parsed = herb_request(text)
        return route('herb', parsed, 'legacy_herb_query', 'legacy') if parsed is not None else None
    if not text or len(text) > 2000:
        return None
    short = re.sub(r'\s+', '', text).rstrip('？?。！!')
    from tcm.dataset_info import parse as dataset_request
    dataset = dataset_request(text)
    if dataset is not None:
        return route('assistant', dataset, 'dataset_description')
    from tcm.intent_guidance import recognize as guidance
    clarified = guidance(text, relation_context)
    if clarified:
        return route('assistant', clarified, 'relation_query_with_provenance'
                     if clarified.get('source_evidence_requested') else 'bounded_intent_guidance')
    from tcm.analysis_chat import parse as analysis_request
    analysis=analysis_request(text)
    if analysis is not None:return route('assistant',analysis,'target_analysis_query')
    from tcm.relation_queries import parse as relation_request
    parsed = relation_request(text)
    if parsed is not None:
        return route('assistant', parsed, 'etcm_relation_query')
    # Compound relationship requests must retain the entire question. Hand them
    # to the existing reviewed task workflow instead of guessing two names or
    # discarding clauses that the direct-query grammar could not consume.
    from tcm.task_request import local_deliverable_hints
    if any(token in text for token in local_deliverable_hints(text)['source_relations']):
        return route('assistant', {'topic':'research_task_review','question':query},
                     'local_task_inventory_review')
    # Coverage questions describe this service, rather than requesting evidence fusion.
    if (len(short) <= 160 and
            re.search(r'^(?:请问)?(?:这里|你这里|这个(?:系统|助手|平台)|本(?:系统|平台))', short) and
            re.search(r'(?:能|可以|支持|有)(?:查到|查询|查|提供|收录|哪些)', short) and
            re.search(r'(?:哪些|什么).*(?:资料|内容|数据)|(?:资料|内容|数据).*(?:范围|包括|涵盖)', short)):
        return route('assistant', {'topic':'capabilities'}, 'source_coverage_question')
    if re.search(r'(?:你|系统|这个项目|这个助手|平台).*(?:能做什么|能干什么|有什么功能|支持什么|怎么用|可以做什么)', short) or short in ('帮助', '使用帮助', '有哪些功能', '支持哪些功能', '功能介绍', '可以查什么', '能查什么', '怎么用', '能做什么', '有什么功能'):
        return route('assistant', {'topic': 'capabilities'}, 'capability_question')
    count_question = bool(re.search(r'多少|几种|几条|有哪些|几类|有多大', short))
    dataset_question = bool(re.search(r'药材|中草药|中药|数据库|收录|支持|ETCM|索引', short, re.I))
    category_question = bool(re.fullmatch(r'(?:目前|当前|系统|一共|总共)?(?:有)?(?:(?:有哪些|有多少种|有多少类)(?:功效|功能)(?:分类)?|多少种功效|(?:功能|功效)(?:分类)?(?:有多少种|有多少类|有哪些|有几类|有多少))[？?]*', short))
    specific_field = any(a in short for a in ('用量', '几克', '多少克', '剂量', '它的', '这味', '刚才'))
    if (count_question and dataset_question and not specific_field) or category_question or short in ('数据概览', '查看数据', '数据库概览', '药材概览'):
        return route('assistant', {'topic': 'overview'}, 'dataset_question')
    usage = usage_paraphrase(text)
    concept = re.fullmatch(r'(?:什么是(.{1,40})|(.{1,40}?)(?:是什么|是什么意思|的概念是什么|怎么理解))', short)
    if concept and usage is None and not any('的'+a in short for a in [*FIELDS, *FIELD_NAMES]):
        return route('assistant', {'topic': 'concept', 'term': next(x for x in concept.groups() if x)}, 'concept_source_needed')
    literature = bool(re.search(r'文献|论文|现代研究|临床研究|pubmed', text, re.I))
    if literature and re.search(r'下载|导出|笔记', text):
        return route('assistant', {'topic': 'literature_term', 'wants_export': True}, 'literature_report_clarification')
    if literature and re.search(r'药典|传统记载|ETCM|结构化', text, re.I):
        return route('assistant', {'topic': 'clarify_sources'}, 'multiple_sources')
    if short in ('回看文献', '上次的文献', '刚才的文献', '上次的检索结果'):
        return route('pubmed', {'action': 'last'}, 'literature_replay')
    match = re.fullmatch(POLITE + r'(?:在\s*)?PubMed\s*(?:上|里|中)?\s*(?:检索|搜索|查找|查询|查一下|查|找)?\s*[:：]?\s*(.+)', text, re.I)
    if not match:
        match = re.fullmatch(POLITE + r'(?:查找|检索|搜索|查询|找一下|查一下|找|查)\s*(.+?)\s*(?:的)?(?:相关)?(?:文献|论文)[？?。！!]*', text)
    if match:
        term = match.group(1).strip().rstrip('？?。！!')
        term = re.sub(r'(?:的)?相关$', '', term).strip()
        if re.search(r'[\u4e00-\u9fff]', term) and not re.search(r'[A-Za-z]', term):
            return route('assistant', {'topic': 'literature_term', 'term': term}, 'unverified_name_translation')
        return route('pubmed', {'action': 'search', 'query': term}, 'literature_query') if term else route('assistant', {'topic': 'literature_term'}, 'missing_search_term')
    if literature:
        return route('assistant', {'topic': 'literature_term',
                                  'wants_export': bool(re.search(r'下载|导出|笔记', text))}, 'literature_query_needs_term')
    prose_source = re.fullmatch(
        r'(?:这段(?:内容|文字)?是药典原话吗[？?，,。]*)?'
        r'(?:我)?(?:能|可以)(?:看看|看一下|查看)(?:它)?在(?:书|药典)(?:里|中)?哪一页(?:吗)?', short)
    if short in SOURCE_QUERY or prose_source:
        if relation_context:
            return route('assistant',{'topic':'etcm_relations','followup':True,'action':'replay'},'active_relation_source')
        if pubmed_context:
            return route('pubmed', {'action': 'last'}, 'active_literature_source')
        if herb_context:
            return route('herb', {'followup': True, 'field': 'source'}, 'active_herb_source')
        return route('research', {'question': '出处呢' if prose_source else text}, 'research_source_followup')
    # Only this session's committed focus can supply an omitted entity.
    followup = re.fullmatch(r'(?:那|那么|还有)?(?:它|这味药|刚才那味药|刚才的药材)?的?(' + FIELD_PATTERN + '|' + HERB_PATTERN + r')(?:是什么|是多少|呢|怎么样|怎么用)?[？?。！!]*', short)
    if followup:
        field = followup.group(1)
        if herb_context and field in FIELD_NAMES:
            return route('herb', {'followup': True, 'field': FIELD_NAMES[field]}, 'active_herb_followup')
        if herb_context:
            return route('assistant', {'topic': 'research_name_needed'}, 'cross_source_entity_unverified')
        return route('research', {'question': '它的' + field}, 'active_research_followup' if research_context else 'missing_entity')
    from tcm.attribute_intent import parse as attribute_request
    # Source selection is kept separate from entity resolution in the chosen tool.
    structured = re.fullmatch(POLITE + r'(?:在\s*)?(?:ETCM|结构化数据库)\s*(?:中|里)?\s*(?:查询|查一下|查)?\s*(.+)', text, re.I)
    if structured:
        body = structured.group(1).strip()
        attributes=attribute_request(body)
        if attributes is not None:return route('herb',attributes,'herb_attribute_query')
        named = herb_request('查询' + body)
        return route('herb', named or {'name': body}, 'explicit_structured_source')
    if comparison_request(text) is not None:
        return route('research', {'question':text}, 'pharmacopoeia_comparison')
    named = re.fullmatch(POLITE + r'(?:查询|查一下|查)?\s*(.{1,256}?)的(' + HERB_PATTERN + r')(?:是什么|呢)?[？?。！!]*', text)
    if named and named.group(1).strip().isascii() and re.search(r'[A-Za-z]',named.group(1)):
        return route('herb', {'name':named.group(1).strip(),'field':FIELD_NAMES[named.group(2)]}, 'structured_named_field_query')
    from tcm.report_request import bounded_field_request
    if bounded_field_request(text) is not None:
        return route('research', {'question': text}, 'shared_bounded_field_request')
    if usage is not None:
        # Pass the original question; the tool independently checks exact
        # source names and preparation scope before accepting this field.
        return route('research', {'question': text}, 'pharmacopoeia_usage_paraphrase')
    if len(text) <= 500 and any(word in text for word in FIELDS):
        return route('research', {'question': text}, 'pharmacopoeia_field_query')
    if named:
        return route('herb', {'name': named.group(1).strip(), 'field': FIELD_NAMES[named.group(2)]}, 'structured_field_query')
    attributes=attribute_request(text)
    if attributes is not None:
        return route('herb',attributes,'herb_attribute_query')
    introductory = re.fullmatch(
        POLITE + r'(?:查询|查找|找一下|查一下|看看|看一下)\s*([\u4e00-\u9fff]{2,20})'
        r'[，,。\s]*(?:我)?(?:主要)?想(?:看|了解)(?:一下)?药典(?:中|里)?(?:怎么写的|的记载|如何记载)(?:[？?。！!])*', text)
    if introductory:
        # Entire grammar is consumed; preparation prefixes remain in the entity.
        return route('research', {'question':introductory.group(1)}, 'pharmacopoeia_name_introduction')
    lookup = re.fullmatch(POLITE + r'(?:查询|查一下|介绍一下|了解一下|查)\s*([\u4e00-\u9fff]{2,20})[？?。！!]*', text)
    if lookup:
        return route('research', {'question': lookup.group(1)}, 'herb_name_query')
    if re.fullmatch(r'[\u4e00-\u9fff]{2,12}', short) and runtime.recognizes_name(short):
        return route('research', {'question': short}, 'known_pharmacopoeia_name')
    return None
