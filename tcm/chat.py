"""Explicit, read-only chat tool. Source text never becomes model instructions."""
import os
import re
import sqlite3
from pathlib import Path

from tcm.herbs import lookup
from typing import Literal, TypedDict


class HerbContext(TypedDict, total=False):
    status: Literal['selected', 'ambiguous_entity']
    source_record_id: str
    dataset_version: str
    file_sha256: str
    last_fields: list[str]


FIELD_NAMES = {
    '科名': 'family', '科': 'family', '药用部位': 'medicinal_part', '部位': 'medicinal_part',
    '药性': 'properties', '药味': 'flavors', '归经': 'meridians', '产地': 'habitat',
    '功效分类': 'efficacy_class', '适应证': 'indications', '文献ID': 'reference_pmids',
    'PMID': 'reference_pmids', '拉丁名': 'latin_name', '英文名': 'english_name',
    '拼音': 'name_pinyin', '来源': 'source',
}
FIELD_PATTERN = '|'.join(re.escape(name) for name in sorted(FIELD_NAMES, key=len, reverse=True))

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / 'var/tcm/herbs.sqlite3'
MESSAGES = {
    'ok': '找到一条药材来源记录，请查看资料卡。来源内容尚未核验，不代表临床结论。',
    'ambiguous_entity': '发现同名来源，请选择来源 ID；系统不会自动合并药材。',
    'not_found': '未找到匹配记录，请检查已收录的拼音、拉丁名、英文名或来源 ID。',
    'unsupported_name_mapping': '中文名和别名尚未建立可靠映射，请使用已收录的拼音、拉丁名、英文名或来源 ID。',
    'database_unavailable': '药材数据库暂不可用，请联系维护者检查导入和数据库配置。',
    'dataset_not_found': '指定的数据版本未导入，请联系维护者检查版本配置。',
    'clarify_entity': '当前会话没有已选药材，请先用 /herb 加药材名称或来源 ID 查询。',
    'clarify_source': '当前药名有多个来源，请先在候选资料卡中选择来源，再继续追问。',
    'context_stale': '上次所选来源或数据版本已发生变化，请重新查询并选择药材。',
    'context_cleared': '已清除本会话的药材选择。',
    'invalid_query': '用法：/herb BaiBu 或 /herb etcm2-local:<64位来源编号>。也可先查药材，再问“它的药用部位是什么？”。',
}


def parse_request(query):
    """Return None for ordinary chat/image requests; an explicit command owns errors."""
    if isinstance(query, dict):
        if 'image' in query:
            return None
        query = query.get('text', '')
    if not isinstance(query, str):
        return None
    query = query.strip()
    if query == '/herb-clear':
        return {'clear': True}
    followup = re.fullmatch(r'(?:它|这味药|刚才那味药|刚才的药材)的?(' + FIELD_PATTERN +
                            r')(?:是什么|是啥|呢|在哪|在哪里)?[？?。！!]*', query)
    if followup:
        return {'followup': True, 'field': FIELD_NAMES[followup.group(1)]}
    named = re.fullmatch(r'(?:查询|查一下|查)\s*(.{1,256}?)\s*的\s*(' + FIELD_PATTERN +
                         r')(?:是什么|是啥|呢)?[？?。！!]*', query)
    if named:
        return {'name': named.group(1).strip(), 'field': FIELD_NAMES[named.group(2)]}
    match = re.match(r'^/herb(?:\s+(.*))?$', query.strip(), re.DOTALL)
    if not match:
        return None
    value = (match.group(1) or '').strip()
    if not value or len(value) > 256:
        return {'error': 'invalid_query'}
    if value.startswith('etcm2-local:'):
        if not re.fullmatch(r'etcm2-local:[0-9a-fA-F]{64}', value):
            return {'error': 'invalid_query'}
        return {'source_record_id': value.lower()}
    return {'name': value}


def execute_request(request, *, dataset_version=None):
    if request.get('error'):
        result = {'status': 'invalid_query', 'records': []}
    else:
        try:
            result = lookup(os.environ.get('TCM_DB') or DEFAULT_DATABASE,
                            dataset_version=dataset_version or os.environ.get('TCM_DATASET_VERSION') or None,
                            **request)
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            # Don't leak private paths/SQL details or silently ask a model to invent an answer.
            result = {'status': 'database_unavailable', 'records': []}
    records = result.get('records', [])
    total = len(records)
    result['records'] = [
        {**{k: record[k] for k in ('source_record_id', 'source', 'warnings')},
         'fields': {key: {**field, 'source': {k: v for k, v in field['source'].items()
                                              if k != 'raw_item'} if field['source'] else None}
                    for key, field in record['fields'].items()}}
        for record in records[:20]
    ]
    result.update(total_record_count=total, returned_record_count=len(result['records']),
                  truncated=total > 20)
    # Only this fixed summary enters the conversational checkpoint, not raw source text.
    result['message'] = MESSAGES.get(result['status'], '该来源记录当前不可用，系统未补造字段。')
    return result


def _status_result(status):
    return {'status': status, 'message': MESSAGES[status], 'records': [],
            'total_record_count': 0, 'returned_record_count': 0, 'truncated': False}


def execute_turn(request, context: HerbContext | None):
    """Resolve one turn from the current session's slot; never infer from model prose."""
    if request.get('clear'):
        return _status_result('context_cleared'), None
    followup = request.get('followup', False)
    field = request.get('field')
    if followup:
        if not context:
            return _status_result('clarify_entity'), None
        if context['status'] == 'ambiguous_entity':
            return _status_result('clarify_source'), context
        configured = os.environ.get('TCM_DATASET_VERSION')
        if configured and configured != context['dataset_version']:
            return _status_result('context_stale'), None
        result = execute_request({'source_record_id': context['source_record_id']},
                                 dataset_version=context['dataset_version'])
        if result['status'] != 'database_unavailable' and (
                result['status'] != 'ok' or
                result['records'][0]['source']['file_sha256'] != context['file_sha256']):
            return _status_result('context_stale'), None
    else:
        result = execute_request({k: v for k, v in request.items() if k != 'field'})
    if result['status'] == 'ambiguous_entity':
        return result, {'status': 'ambiguous_entity'}
    if result['status'] != 'ok':
        return result, None
    record = result['records'][0]
    requested_fields = (context.get('last_fields', []) if followup and field == 'source' else
                        [] if field in (None, 'source') else [field])
    new_context: HerbContext = {
        'status': 'selected', 'source_record_id': record['source_record_id'],
        'dataset_version': result['dataset_version'], 'file_sha256': record['source']['file_sha256'],
        'last_fields': requested_fields,
    }
    if requested_fields:
        keep = {'name_pinyin', *requested_fields}
        record['fields'] = {key: value for key, value in record['fields'].items() if key in keep}
    result.update(requested_fields=requested_fields, followup=followup)
    return result, new_context
