"""Bounded natural-language routing. Resolves tools, never invents entities or SQL."""
import re

from features import env_bool
from tcm.agent_pubmed import parse_request as pubmed_request
from tcm.agent_research import parse_request as research_request, runtime
from tcm.chat import parse_request as herb_request, FIELD_NAMES
from tcm.mineru_chat import SOURCE_QUERY
from tcm.mineru_retrieval import INTENTS

FIELDS = sorted({a for aliases in INTENTS.values() for a in aliases}, key=len, reverse=True)
FIELD_PATTERN = '|'.join(map(re.escape, FIELDS))
HERB_PATTERN = '|'.join(map(re.escape, sorted(FIELD_NAMES, key=len, reverse=True)))
POLITE = r'(?:请帮我|帮我|麻烦你|麻烦|请)?\s*'


def route(tool, request, reason, mode='natural'):
    return {'tool': tool, 'request': request, 'reason': reason, 'mode': mode}


def resolve(query, *, herb_context=None, research_context=None, pubmed_context=None):
    if isinstance(query, dict):
        if 'image' in query:
            return None
        query = query.get('text', '')
    if not isinstance(query, str):
        return None
    text = query.strip()
    # Commands preserve the existing parser contracts, including errors and bounds.
    if text.startswith('/'):
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
    if re.search(r'(?:你|系统|这个项目|这个助手|平台).*(?:能做什么|能干什么|有什么功能|支持什么|怎么用|可以做什么)', short) or short in ('帮助', '使用帮助', '有哪些功能', '支持哪些功能', '功能介绍', '可以查什么', '能查什么', '怎么用', '能做什么', '有什么功能'):
        return route('assistant', {'topic': 'capabilities'}, 'capability_question')
    count_question = bool(re.search(r'多少|几种|几条|有哪些|几类|有多大', short))
    dataset_question = bool(re.search(r'药材|中草药|中药|数据库|收录|支持|ETCM|索引', short, re.I))
    category_question = bool(re.fullmatch(r'(?:目前|当前|系统|一共|总共)?(?:有)?(?:(?:有哪些|有多少种|有多少类)(?:功效|功能)(?:分类)?|多少种功效|(?:功能|功效)(?:分类)?(?:有多少种|有多少类|有哪些|有几类|有多少))[？?]*', short))
    specific_field = any(a in short for a in ('用量', '几克', '多少克', '剂量', '它的', '这味', '刚才'))
    if (count_question and dataset_question and not specific_field) or category_question or short in ('数据概览', '查看数据', '数据库概览', '药材概览'):
        return route('assistant', {'topic': 'overview'}, 'dataset_question')
    concept = re.fullmatch(r'(?:什么是(.{1,40})|(.{1,40}?)(?:是什么|是什么意思|的概念是什么|怎么理解))', short)
    if concept and not any('的'+a in short for a in [*FIELDS, *FIELD_NAMES]):
        return route('assistant', {'topic': 'concept', 'term': next(x for x in concept.groups() if x)}, 'concept_source_needed')
    literature = bool(re.search(r'文献|论文|现代研究|临床研究|pubmed', text, re.I))
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
        return route('assistant', {'topic': 'literature_term'}, 'literature_query_needs_term')
    if short in SOURCE_QUERY:
        if pubmed_context:
            return route('pubmed', {'action': 'last'}, 'active_literature_source')
        if herb_context:
            return route('herb', {'followup': True, 'field': 'source'}, 'active_herb_source')
        return route('research', {'question': text}, 'research_source_followup')
    # Only this session's committed focus can supply an omitted entity.
    followup = re.fullmatch(r'(?:那|那么|还有)?(?:它|这味药|刚才那味药|刚才的药材)?的?(' + FIELD_PATTERN + '|' + HERB_PATTERN + r')(?:是什么|是多少|呢|怎么样|怎么用)?[？?。！!]*', short)
    if followup:
        field = followup.group(1)
        if herb_context and field in FIELD_NAMES:
            return route('herb', {'followup': True, 'field': FIELD_NAMES[field]}, 'active_herb_followup')
        if herb_context:
            return route('assistant', {'topic': 'research_name_needed'}, 'cross_source_entity_unverified')
        return route('research', {'question': '它的' + field}, 'active_research_followup' if research_context else 'missing_entity')
    # Source selection is kept separate from entity resolution in the chosen tool.
    structured = re.fullmatch(POLITE + r'(?:在\s*)?(?:ETCM|结构化数据库)\s*(?:中|里)?\s*(?:查询|查一下|查)?\s*(.+)', text, re.I)
    if structured:
        body = structured.group(1).strip()
        named = herb_request('查询' + body)
        return route('herb', named or {'name': body}, 'explicit_structured_source')
    if len(text) <= 500 and any(word in text for word in FIELDS):
        return route('research', {'question': text}, 'pharmacopoeia_field_query')
    named = re.fullmatch(POLITE + r'(?:查询|查一下|查)?\s*(.{1,256}?)的(' + HERB_PATTERN + r')(?:是什么|呢)?[？?。！!]*', text)
    if named:
        return route('herb', {'name': named.group(1).strip(), 'field': FIELD_NAMES[named.group(2)]}, 'structured_field_query')
    lookup = re.fullmatch(POLITE + r'(?:查询|查一下|介绍一下|了解一下|查)\s*([\u4e00-\u9fff]{2,20})[？?。！!]*', text)
    if lookup:
        return route('research', {'question': lookup.group(1)}, 'herb_name_query')
    if re.fullmatch(r'[\u4e00-\u9fff]{2,12}', short) and runtime.recognizes_name(short):
        return route('research', {'question': short}, 'known_pharmacopoeia_name')
    return None
