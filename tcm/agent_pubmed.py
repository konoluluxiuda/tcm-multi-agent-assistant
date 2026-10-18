"""Explicit PubMed graph tool; only an opaque snapshot reference enters memory."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import BoundedSemaphore

from features import env_bool
from tcm.literature_store import LiteratureStore
from tcm.pubmed import PubMedClient

ROOT = Path(__file__).resolve().parents[1]


def parse_request(query):
    if isinstance(query, dict):
        if 'image' in query:
            return None
        query = query.get('text', '')
    if not isinstance(query, str):
        return None
    value = query.strip()
    if value in ('/pubmed-last', '/pubmed-clear'):
        return {'action': 'last' if value == '/pubmed-last' else 'clear'}
    match = re.fullmatch(r'/pubmed(?:\s+(.*))?', value, re.DOTALL)
    if not match:
        return None
    term = (match.group(1) or '').strip()
    return {'action': 'search', 'query': term} if 1 <= len(term) <= 2000 else {'action': 'invalid'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def context_from(snapshot):
    if isinstance(snapshot, dict) and snapshot.get('kind') == 'agent_contexts_v1':
        return snapshot.get('pubmed')
    return None


def status_result(status, message, data=None):
    return {'status': status, 'message': message, 'tool_result': data,
            'llm_calls': 0, 'workflow': 'langgraph_explicit_pubmed_tool'}


class PubMedRuntime:
    def __init__(self):
        self.slot = BoundedSemaphore(1)

    def execute(self, request, context):
        action = request.get('action')
        if action == 'clear':
            return status_result('context_cleared', '已清除本会话的文献批次焦点；文献台账按独立保留策略管理。'), None
        if action == 'invalid':
            return status_result('invalid_query', '用法：/pubmed Astragalus membranaceus[Title/Abstract]；/pubmed-last 回看本会话上次批次。'), None
        if not env_bool('ENABLE_PUBMED'):
            return status_result('pubmed_disabled', 'PubMed文献工具尚未启用。'), None
        if action == 'last' and not context:
            return status_result('no_active_run', '当前会话没有文献批次，请用 /pubmed 加明确检索式查询。'), None
        if not self.slot.acquire(blocking=False):
            return status_result('pubmed_busy', '文献工具正在处理其他请求，请稍后重试。'), None
        store = None
        try:
            store = LiteratureStore(os.getenv('PUBMED_DB') or ROOT / 'var/literature/pubmed.sqlite3')
            if action == 'last':
                if not isinstance(context, dict) or context.get('kind') != 'pubmed_run_v1':
                    return status_result('snapshot_unavailable', '文献批次引用无效，请重新检索。'), None
                data = store.get(context.get('run_id'))
                if data is None or _hash(data) != context.get('snapshot_sha256'):
                    return status_result('snapshot_unavailable', '已保存文献批次缺失或改变，请重新检索。'), None
                run_id = context['run_id']
            else:
                data = PubMedClient().search(request['query'], limit=5)
                run_id = store.save(data)
                data = store.get(run_id)
            selected = {'kind': 'pubmed_run_v1', 'run_id': run_id, 'snapshot_sha256': _hash(data)}
            messages = {'ok': '已获取本次文献元数据及可用摘要；未读取全文，未评议临床证据质量。',
                        'no_results': '本次有效检索式未命中；这不代表没有相关研究。',
                        'partial_fetch': '部分文献未能获取，请核对缺失PMID；未补造内容。',
                        'error': 'PubMed检索失败，不能据此判断没有相关研究。'}
            message = messages[data['status']]
            if any(article.get('assessment', {}).get('flags') or
                   article.get('identity', {}).get('same_doi_other_pmids') or
                   article.get('identity', {}).get('alerts') for article in data['articles']):
                message += ' 本批次存在来源关系或身份提示，请核对证据卡。'
            return status_result(data['status'], message, dict(data, run_id=run_id,
                                  replayed=action == 'last')), selected
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            return status_result('pubmed_unavailable', '文献工具或存储暂不可用，未切换到模型补答。'), None
        finally:
            if store is not None:
                store.close()
            self.slot.release()


runtime = PubMedRuntime()
