"""Explicit /research tool adapter; lazy, opt-in source access, no model fallback."""
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock

from features import env_bool
from tcm.mineru_chat import ResearchTool
from tcm.mineru_research import ResearchService, SnapshotChanged

ROOT = Path(__file__).resolve().parents[1]


def parse_request(query):
    if isinstance(query, dict):
        if 'image' in query:
            return None
        query = query.get('text', '')
    if not isinstance(query, str):
        return None
    value = query.strip()
    if value == '/research-clear':
        return {'clear': True}
    match = re.fullmatch(r'/research(?:\s+(.*))?', value, re.DOTALL)
    if not match:
        return None
    question = (match.group(1) or '').strip()
    return {'question': question} if 1 <= len(question) <= 500 else {'error': 'invalid_query'}


def status_result(status, message):
    return {'status': status, 'message': message, 'tool_result': None, 'focus_name': None,
            'research_only': True, 'human_signoff': False, 'llm_calls': 0,
            'workflow': 'langgraph_explicit_research_tool'}


class ResearchRuntime:
    def __init__(self):
        self.lock = RLock()
        self.service = None
        self.key = None

    def get_service(self):
        if not env_bool('ENABLE_PHARMACOPOEIA_RESEARCH'):
            return None
        key = tuple(Path(os.getenv(name) or default).resolve() for name, default in (
            ('PHARMACOPOEIA_INDEX', ROOT / 'var/mineru_retrieval_v2'),
            ('PHARMACOPOEIA_PDF_HIGH', ROOT / 'data/《中国药典》2025年版 一部.pdf'),
            ('PHARMACOPOEIA_PDF_FULL', ROOT / 'data/《中国药典》2025年版 一部 全本.pdf')))
        with self.lock:
            if self.service is None or self.key != key:
                service = ResearchService(key[0], {'high': key[1], 'full': key[2]})
                self.service, self.key = service, key
            self.service.check()
            return self.service

    def execute(self, request, context):
        if request.get('clear'):
            return status_result('context_cleared', '已清除本会话的药典研究焦点。'), None
        if request.get('error'):
            return status_result('invalid_query', '用法：/research 甘草的功效；追问也请使用 /research 前缀。'), None
        try:
            service = self.get_service()
            if service is None:
                return status_result('research_disabled', '药典研究工具尚未启用；不会改用模型或联网补答。'), None
            result, selected = ResearchTool(service).execute(request['question'], context)
            return {**result, 'workflow': 'langgraph_explicit_research_tool'}, selected
        except SnapshotChanged:
            return status_result('research_unavailable', '药典来源快照已改变，请维护者校验并重启服务后重新查询。'), None
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            return status_result('research_unavailable', '药典研究来源或索引不可用，未改用模型补造内容。'), None


runtime = ResearchRuntime()


def split_contexts(context):
    if isinstance(context, dict) and context.get('kind') == 'agent_contexts_v1':
        return context.get('herb'), context.get('research')
    return context, None  # Legacy completed-turn snapshots need no database migration.


def pack_contexts(herb, research, pubmed=None):
    return {'kind': 'agent_contexts_v1', 'herb': herb, 'research': research,
            'pubmed': pubmed} if research is not None or pubmed is not None else herb
