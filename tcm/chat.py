"""Explicit, read-only chat tool. Source text never becomes model instructions."""
import os
import re
import sqlite3
from pathlib import Path

from tcm.herbs import lookup

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / 'var/tcm/herbs.sqlite3'
MESSAGES = {
    'ok': '找到一条药材来源记录，请查看资料卡。来源内容尚未核验，不代表临床结论。',
    'ambiguous_entity': '发现同名来源，请选择来源 ID；系统不会自动合并药材。',
    'not_found': '未找到匹配记录，请检查已收录的拼音、拉丁名、英文名或来源 ID。',
    'unsupported_name_mapping': '中文名和别名尚未建立可靠映射，请使用已收录的拼音、拉丁名、英文名或来源 ID。',
    'database_unavailable': '药材数据库暂不可用，请联系维护者检查导入和数据库配置。',
    'dataset_not_found': '指定的数据版本未导入，请联系维护者检查版本配置。',
    'invalid_query': '用法：/herb BaiBu 或 /herb etcm2-local:<64位来源编号>。每次请给出完整名称或来源 ID。',
}


def parse_request(query):
    """Return None for ordinary chat/image requests; an explicit command owns errors."""
    if isinstance(query, dict):
        if 'image' in query:
            return None
        query = query.get('text', '')
    if not isinstance(query, str):
        return None
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


def execute_request(request):
    if request.get('error'):
        result = {'status': 'invalid_query', 'records': []}
    else:
        try:
            result = lookup(os.environ.get('TCM_DB') or DEFAULT_DATABASE,
                            dataset_version=os.environ.get('TCM_DATASET_VERSION') or None,
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
