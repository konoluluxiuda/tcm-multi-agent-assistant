"""Deterministic research tool conversation; source text is never model instructions."""
from pathlib import Path
import re

from agents.conversation_store import ConversationStore, SessionExpired
from agents.conversation_store import MemoryUnavailable
from agents.runtime import session_turn
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import name_key, INTENTS

MESSAGES = {
    'candidates': '药典记载如下。',
    'clarify_entity': '当前没有已确认药材，请先明确药名和要查询的字段。',
    'context_stale': '上次检索的数据或规则版本已改变，请明确药名重新查询。',
    'variant_scope_unresolved': '指定炮制品范围未解析，不能用母药字段替代。',
    'reference_unresolved': '该字段正文缺失或引用尚未展开，查询未完成；请核对来源片段。',
    'partial_evidence': '部分字段尚未完整解析，请分别核对候选与未完成片段。',
    'name_not_resolved': '未确认药名，请使用已收录的中文药名，不自动猜测别名。',
    'multiple_names': '问题涉及多个药名，请分别查询，暂不比较或选择用药。',
    'ambiguous_name': '该药名对应多个条目，需要先明确来源。',
    'ambiguous_field': '该药名下有重复字段，需要核对炮制品与原文范围。',
    'field_not_resolved': '请明确要查的字段，例如功效、用量、贮藏或来源。',
    'field_not_observed': '当前条目未检出所需字段，不能据此推断没有相关作用或注意事项。',
    'empty_query': '请输入药名和研究问题。',
    'no_match': '未检出候选证据，系统未补造内容。',
}
FOLLOWUP = re.compile(r'^(它|这味药|刚才那味药|刚才的药材)')
SOURCE_QUERY = {'出处呢', '出处', '查看原页', '看看出处', '证据呢'}


MAX_COMPARISON_NAMES = 3
MAX_COMPARISON_FIELDS = 4


def comparison_request(question):
    """Parse explicit bounded lists, without inferring aliases or clinical intent."""
    if not isinstance(question, str) or not re.search(r'比较|对比|区别|差异|放一起', question):
        return None
    error = {'error': '请明确2至3味药材和最多4个药典字段，例如“比较黄芪、党参和人参的性味、归经、功效、用量”；这里只并列原文，不判断选药或替代关系。'}
    if len(question) > 500:
        return error
    text = re.sub(r'\s+', '', question).rstrip('？?。！!')
    names_pattern = r'([\u4e00-\u9fff、，,]{2,100}?)'
    match = re.fullmatch(r'(?:请帮我|帮我|请)?(?:比较|对比)' + names_pattern + r'的(.+)', text)
    if not match:
        match = re.fullmatch(r'(?:请)?' + names_pattern + r'的(.+?)(?:有什么区别|有何不同|有什么差异|的区别|的差异|对比|比较)', text)
    if not match:
        match = re.fullmatch(r'(?:请帮我|帮我|请)?把' + names_pattern + r'放一起(?:看看|看一下|比较一下)?[吧，,]*[，,](.+?)(?:都想比较一下|都想比较|都比较一下)', text)
    if not match:
        return error
    names_text, field_text = match.groups()
    names = re.split(r'[和与、及，,]', names_text)
    if (not 2 <= len(names) <= MAX_COMPARISON_NAMES or
            any(not re.fullmatch(r'[\u4e00-\u9fff]{2,20}', name) for name in names) or
            len({name_key(name) for name in names}) != len(names)):
        return error
    aliases = {alias: ('来源' if label == 'origin' else label)
               for label, values in INTENTS.items() for alias in (*values, label) if alias != 'origin'}
    pattern = '|'.join(re.escape(s) for s in sorted(aliases, key=len, reverse=True))
    # Consume whole field labels before separators: 性味与归经 is one source field.
    tokens = []
    remaining = field_text
    while remaining:
        field = re.match('(?:' + pattern + ')', remaining)
        if not field:
            return error
        tokens.append(aliases[field.group()])
        remaining = remaining[field.end():]
        if remaining:
            if remaining[0] not in '和与、及，,':
                return error
            remaining = remaining[1:]
            if not remaining:
                return error
    fields = list(dict.fromkeys(tokens))
    if not fields or len(fields) > MAX_COMPARISON_FIELDS:
        return error
    return {'names': names, 'fields': fields}


class ResearchTool:
    """Stateless tool turn shared by standalone and main-agent workflows."""
    def __init__(self, service):
        self.service = service
        root = Path(__file__).parent
        self.binding = {'kind': 'mineru_research_chat_v1', 'index_sha256': service.receipt['index_sha256'],
                        'view_sha256': service.receipt['view_sha256'],
                        'retriever_sha256': digest(root / 'mineru_retrieval.py'),
                        'guards_sha256': digest(root / 'mineru_guards.py'),
                        'request_sha256': digest(root / 'report_request.py')}

    def valid_context(self, context):
        return (isinstance(context, dict) and all(context.get(k) == v for k, v in self.binding.items())
                and isinstance(context.get('name'), str) and 1 <= len(context['name']) <= 200
                and (context.get('last_question') is None or isinstance(context['last_question'], str)
                     and len(context['last_question']) <= 500))

    def execute(self, question, context):
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError("Question must contain 1–500 characters")
        self.service.check()
        stripped = question.strip()
        comparison=comparison_request(stripped)
        if comparison is not None:
            return self.compare(stripped,comparison), None
        is_source = stripped.rstrip('？?。！!') in SOURCE_QUERY
        followup = bool(FOLLOWUP.match(stripped)) or is_source
        result, resolved = None, stripped
        if followup and not context:
            status = 'clarify_entity'
        elif followup and not self.valid_context(context):
            status = 'context_stale'
        elif is_source and not context.get('last_question'):
            status = 'field_not_resolved'
        else:
            if is_source:
                resolved = context['last_question']
            elif followup:
                resolved = FOLLOWUP.sub(context['name'], stripped, count=1)
            from tcm.report_request import bounded_field_request, interpret_report_request, organize_fields
            request = bounded_field_request(resolved) or interpret_report_request('', resolved)
            result = self.service.search(request['search_question'], method='name_field', k=20)
            # Keep the original resolved sentence for source replay and session memory.
            result['request_interpretation'] = request
            if result['status'] == 'candidates':
                hits, missing = organize_fields(result['hits'],
                    {**request, 'fields': result.get('fields', [])}, limit=5)
                result = {**result, 'hits': hits}
                if missing:
                    result.update(status='partial_evidence', missing_fields=missing, lookup_complete=False)
            else:
                result = {**result, 'hits': result.get('hits', [])[:5]}
            status = result['status']
        new_context = context if is_source and status == 'field_not_resolved' and result is None else None
        usable_focus = (status == 'candidates' or status == 'partial_evidence' and
                        result and result.get('missing_fields') and not result.get('unresolved_evidence'))
        if result and usable_focus and len({h['name'] for h in result['hits']}) == 1:
            new_context = {**self.binding, 'name': result['hits'][0]['name'], 'last_question': resolved}
        elif result and status == 'field_not_resolved' and len(result.get('names', [])) == 1:
            if name_key(stripped) == name_key(result['names'][0]):
                new_context = {**self.binding, 'name': result['names'][0], 'last_question': None}
        message = MESSAGES.get(status, "当前无法完成查询，请明确药名和字段。")
        if status == 'field_not_resolved' and new_context:
            message = f'想了解{new_context["name"]}的哪些内容？可问功效、用量或注意事项。'
        if result and result.get('missing_fields'):
            message += ' 本次未检出字段：' + '、'.join(result['missing_fields']) + '；未检出不等于不存在。'
        return {"status": status, "message": message, "tool_result": result,
                "focus_name": new_context["name"] if new_context else None, "followup": followup,
                "resolved_question": resolved if result else None, "research_only": True,
                "human_signoff": False, "llm_calls": 0, "workflow": "deterministic_research_tool_router"}, new_context

    def compare(self, question, request):
        base={'focus_name':None,'followup':False,'resolved_question':question,
              'research_only':True,'human_signoff':False,'llm_calls':0,
              'workflow':'deterministic_research_comparison','tool_result':None}
        if 'error' in request:
            return {**base,'status':'clarify_comparison','message':request['error']}
        # Check the complete original question before splitting; preserve preparation guards.
        probe=self.service.search(question,method='name_field',k=5)
        if probe['status'] not in ('multiple_names','candidates'):
            return {**base,'status':probe['status'],'message':MESSAGES.get(probe['status'],'请核对药名和原文范围。'),
                    'tool_result':probe}
        if {name_key(n) for n in probe.get('names',[])}!={name_key(n) for n in request['names']}:
            return {**base,'status':'name_not_resolved','message':'未能确认全部完整药名；不会只展示已识别药材，或使用母药替代。'}
        groups=[]
        for name in request['names']:
            cells=[]
            for field in request['fields']:
                query=name+'的'+field
                result=self.service.search(query,method='name_field',k=5)
                cells.append({'field':field,'status':result['status'],'query':query,'result':result,
                              'message':MESSAGES.get(result['status'],'当前字段无法完成检索，请核对原页。')})
            groups.append({'name':name,'cells':cells})
        self.service.check()
        complete=all(c['status']=='candidates' for g in groups for c in g['cells'])
        return {**base,'status':'candidates' if complete else 'partial_evidence',
                'message':'、'.join(request['names'])+'：'+'、'.join(request['fields'])+'对比。'+
                          ('' if complete else '部分字段未完整取得，见表中提示。'),
                'tool_result':{'comparison':{'names':request['names'],'fields':request['fields'],'groups':groups},
                               'index_sha256':self.service.receipt['index_sha256'],'hits':[]}}


class ResearchChat:
    def __init__(self, service, database, *, ttl_seconds=30 * 86400):
        self.service = service
        database = Path(database).resolve()
        protected = {service.directory / 'research.sqlite3', *service.sources.values(),
                     Path(__file__).resolve().parents[1] / 'var/memory/conversations.sqlite3',
                     Path(__file__).resolve().parents[1] / 'var/tcm/herbs.sqlite3'}
        if database in protected:
            raise ValueError('Research conversations require a separate database')
        self.store = ConversationStore(database, ttl_seconds=ttl_seconds)
        with self.store.connection() as con:
            marker = con.execute("SELECT value FROM settings WHERE key='research_chat_kind'").fetchone()
            if marker is None:
                if con.execute('SELECT 1 FROM conversations LIMIT 1').fetchone() or con.execute("SELECT 1 FROM settings WHERE key='cookie_secret'").fetchone():
                    raise MemoryUnavailable('Not a dedicated research conversation database')
                con.execute("INSERT INTO settings VALUES ('research_chat_kind','mineru_research_chat_v1')")
            elif marker != ('mineru_research_chat_v1',):
                raise MemoryUnavailable('Unsupported research conversation schema')
        self.store.purge_expired()
        self.tool = ResearchTool(service)
        self.binding = self.tool.binding

    def session(self, cookie):
        if self.store.valid_cookie(cookie):
            try:
                self.store.load_serialized(cookie)
                return cookie
            except SessionExpired:
                pass
        return self.store.new_cookie()

    def valid_context(self, context):
        return self.tool.valid_context(context)

    def load(self, cookie):
        snapshot = self.store.load_serialized(cookie)
        if not isinstance(snapshot.messages, list) or any(
                not isinstance(m, dict) or m.get('type') not in ('human', 'ai')
                or not isinstance(m.get('data'), dict) or not isinstance(m['data'].get('content'), str)
                for m in snapshot.messages):
            raise MemoryUnavailable('Invalid research conversation messages')
        return snapshot

    def conversation(self, cookie):
        self.service.check()
        with session_turn(cookie):
            snapshot = self.load(cookie)
            context = snapshot.tcm_context
            return {'messages': [{'role': 'user' if m['type'] == 'human' else 'assistant',
                                  'content': m['data']['content']} for m in snapshot.messages],
                    'revision': snapshot.revision,
                    'focus_name': context.get('name') if self.valid_context(context) else None,
                    'context_stale': bool(context) and not self.valid_context(context),
                    'retention_days': self.store.ttl_seconds / 86400}

    def turn(self, cookie, question, revision):
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError('Question must contain 1–500 characters')
        if type(revision) is not int or revision < 0:
            raise ValueError('Invalid conversation revision')
        self.service.check()
        with session_turn(cookie):
            snapshot = self.load(cookie)
            from agents.conversation_store import ConversationConflict
            if snapshot.revision != revision:
                raise ConversationConflict('Reload conversation before retrying')
            result, new_context = self.tool.execute(question, snapshot.tcm_context)
            message = result["message"]
            # Store user text and fixed summaries only, never raw OCR or candidate quotations.
            messages = snapshot.messages + [{'type': 'human', 'data': {'content': question}},
                                             {'type': 'ai', 'data': {'content': message}}]
            self.service.check()
            self.store.save_serialized(cookie, messages[-20:], new_context, revision=revision)
            return {**result, "revision": revision + 1}

    def delete(self, cookie):
        with session_turn(cookie):
            self.store.delete(cookie)
