"""Best-effort, owner-scoped evidence of decisions; never a retry/execution journal.

Each HTTP submission has its own server-generated ID. Background jobs reattach
to the first reservation's ID without changing queue payloads or idempotency.
"""
from contextlib import contextmanager, closing
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from threading import RLock
from uuid import uuid4

LOG=logging.getLogger(__name__)
CURRENT=ContextVar('dataops_diagnostic_context',default=None)
SPAN_DEPTH=ContextVar('dataops_diagnostic_span_depth',default=0)
ID=re.compile(r'^[0-9a-f]{32}$')
MAX_EVENTS=256
# Thread-local contention should not discard first-request schema creation.
# SQLite still arbitrates other processes; tracing remains best effort.
DB_LOCKS=tuple(RLock() for _ in range(32))
KNOWN_REJECTIONS={
    'Invalid relationship scope':'query_relation_scope_invalid',
    'Invalid multi-step analysis shape':'query_plan_shape_invalid',
    'Invalid analysis scope':'query_analysis_scope_invalid',
    'Unresolved requirements cannot be silently executed':'query_unresolved_requirements',
    'Ungrounded or narrowed entity':'query_entity_not_grounded',
    'Unsupported requirements cannot disappear':'query_requirement_omitted',
    'Unsupported analysis scope':'query_analysis_requirement_unsupported',
    'Requested result count changed':'query_result_count_changed',
    'Ungrounded analysis name':'query_analysis_name_not_grounded',
    'Invalid pharmacopoeia scope':'query_document_scope_invalid',
    'Stopped task cannot query':'query_stopped_task_has_actions',
    'Ungrounded object':'query_entity_not_grounded',
    'Uncovered pair requirements':'pair_requirement_coverage_failed',
    'Relation requirements omitted or duplicated':'relation_requirement_coverage_failed',
    'Choose a supported field and a nonblank value':'filter_contract_invalid',
}


class ContractDiagnosticError(ValueError):
    def __init__(self,code,message,details):
        super().__init__(message);self.code=code;self.details=details


def reject(code,message,**details):
    raise ContractDiagnosticError(code,message,details)


def error_location(exc):
    """Project source location only; no exception text, traceback locals or paths."""
    try:
        root=Path(__file__).resolve().parents[2];tb=exc.__traceback__;location=None
        while tb:
            path=Path(tb.tb_frame.f_code.co_filename).resolve()
            relative=path.relative_to(root) if path.is_relative_to(root) else None
            project_file=relative is not None and relative.parts and (relative.parts[0] in ('tcm','agents','scripts','tests') or
                                                   str(relative) in ('app.py','config.py','features.py','source_access.py'))
            if project_file and path.is_file() and not (path==Path(__file__).resolve() and tb.tb_frame.f_code.co_name=='reject'):
                location={'file':str(relative),'line':tb.tb_lineno,
                          'function':tb.tb_frame.f_code.co_name,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            tb=tb.tb_next
        return location
    except Exception:return None  # Missing diagnostic source must not hide the original failure.


def validation_detail(exc):
    from pydantic import ValidationError
    location=error_location(exc)
    if isinstance(exc,ContractDiagnosticError):return {'code':exc.code,**exc.details,'location':location}
    if isinstance(exc,ValidationError):
        fields=[]
        for e in exc.errors(include_input=False,include_url=False)[:30]:
            cause=e.get('ctx',{}).get('error')
            code=KNOWN_REJECTIONS.get(str(cause or ''))
            fields.append({'path':list(e['loc']),'type':e['type'],**({'code':code} if code else {}),
                           'location':error_location(cause) if isinstance(cause,BaseException) else None})
        return {'code':fields[0].get('code','schema_validation_failed') if len(fields)==1 else 'schema_validation_failed','fields':fields,'location':location}
    return {'code':KNOWN_REJECTIONS.get(str(exc),'contract_validation_failed'),'exception_type':type(exc).__name__,'location':location}


def model_return(reply):
    """Parsed arguments only. Invalid JSON is fingerprinted, not exported as text."""
    invalid=[]
    for call in getattr(reply,'invalid_tool_calls',[]) or []:
        if not isinstance(call,dict):
            invalid.append({'code':'invalid_tool_call_shape','value_type':type(call).__name__});continue
        raw=str(call.get('args') or '')
        invalid.append({'name':call.get('name'),'code':'tool_json_parse_failed',
                        'args_sha256':hashlib.sha256(raw.encode()).hexdigest(),'args_chars':len(raw)})
    encoded=json.dumps(reply.tool_calls,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return {'tool_calls':reply.tool_calls,'invalid_tool_calls':invalid,
            'tool_calls_sha256':hashlib.sha256(encoded).hexdigest(),
            'tool_calls_hash_scope':'parsed_sdk_calls_before_redaction'}


class Store:
    def __init__(self,root):self.path=Path(root)/'diagnostics.sqlite3'

    @contextmanager
    def db(self):
        lock=DB_LOCKS[int(hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:8],16)%len(DB_LOCKS)]
        if not lock.acquire(timeout=1):raise sqlite3.OperationalError('diagnostic_store_busy')
        try:
            with self._db() as con:yield con
        finally:lock.release()

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Create privately, including before SQLite first opens the file.
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd)
        with closing(sqlite3.connect(self.path,timeout=.25)) as con,con:
            con.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,owner TEXT NOT NULL,created REAL NOT NULL,workspace TEXT)')
            con.execute('CREATE INDEX IF NOT EXISTS workspace_diagnostics ON requests(owner,workspace,created)')
            con.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,event TEXT NOT NULL)')
            con.execute('CREATE INDEX IF NOT EXISTS request_diagnostic_events ON events(request_id,seq)')
            yield con

    def bind(self,identifier,owner):
        with self.db() as con:
            con.execute('INSERT OR IGNORE INTO requests VALUES(?,?,?,NULL)',(identifier,owner,time.time()))
            if con.execute('SELECT owner FROM requests WHERE id=?',(identifier,)).fetchone()[0]!=owner:
                raise ValueError('Diagnostic owner mismatch')
            old=[r[0] for r in con.execute('SELECT id FROM requests WHERE created<? OR id IN '
                '(SELECT id FROM requests WHERE owner=? ORDER BY created DESC LIMIT -1 OFFSET 1000)',(time.time()-30*86400,owner))]
            con.executemany('DELETE FROM events WHERE request_id=?',[(i,) for i in old])
            con.executemany('DELETE FROM requests WHERE id=?',[(i,) for i in old])

    def append(self,identifier,event):
        with self.db() as con:
            if con.execute('SELECT count(*) FROM events WHERE request_id=?',(identifier,)).fetchone()[0]<MAX_EVENTS:
                con.execute('INSERT INTO events(request_id,event) VALUES(?,?)',(identifier,json.dumps(event,ensure_ascii=False)))

    def link(self,identifier,owner,workspace):
        with self.db() as con:con.execute('UPDATE requests SET workspace=? WHERE id=? AND owner=?',(workspace,identifier,owner))

    def origin(self,owner,workspace):
        with self.db() as con:
            row=con.execute('SELECT id FROM requests WHERE owner=? AND workspace=? ORDER BY created,id LIMIT 1',(owner,workspace)).fetchone()
            return row[0] if row else None

    def get(self,owner,identifier):
        if not ID.fullmatch(identifier):raise KeyError(identifier)
        with self.db() as con:
            row=con.execute('SELECT workspace FROM requests WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if row is None:raise KeyError(identifier)
            events=[json.loads(r[0]) for r in con.execute('SELECT event FROM events WHERE request_id=? ORDER BY seq',(identifier,))]
        return {'request_id':identifier,'langfuse_trace_id':identifier,'workspace_id':row[0],
                'events':events,'scope':'recorded observations; not a claim of semantic task success',
                'retention_days':30,'max_events':MAX_EVENTS}


@contextmanager
def request_context(identifier=None,*,force=False):
    existing=CURRENT.get()
    if existing is not None and not force:
        yield existing;return
    identifier=identifier if identifier and ID.fullmatch(identifier) else uuid4().hex
    value={'request_id':identifier,'store':None,'owner':None,'buffer':[]}
    token=CURRENT.set(value);depth=SPAN_DEPTH.set(0)
    try:yield value
    finally:SPAN_DEPTH.reset(depth);CURRENT.reset(token)


def bind(root,owner,*,question=None,request_key=None):
    ctx=CURRENT.get()
    if ctx is None:return
    try:
        if ctx['owner'] is not None and ctx['owner']!=owner:raise ValueError('Diagnostic context owner changed')
        store=Store(root);store.bind(ctx['request_id'],owner)
        ctx.update(store=store,owner=owner)
        for event in ctx['buffer']:store.append(ctx['request_id'],event)
        ctx['buffer'].clear()
        if question is not None:
            record('request','received',question=question,
                   request_key_sha256=hashlib.sha256(str(request_key or '').encode()).hexdigest())
    except Exception as exc:LOG.warning('Local request diagnostics unavailable; execution unchanged; error_kind=%s',type(exc).__name__)


def record(stage,status,*,code=None,**details):
    ctx=CURRENT.get()
    if ctx is None:return
    try:
        from tcm.tracing import sanitize,observation
        # Private, owner-bound diagnostics retain nested typed tool parameters.
        # Langfuse keeps its default shallower export, size and credential guards.
        event={'stage':stage,'status':status,'code':code,'time':time.time(),'details':sanitize(details,max_depth=16)}
        if ctx['store'] is not None:ctx['store'].append(ctx['request_id'],event)
        elif len(ctx['buffer'])<MAX_EVENTS:ctx['buffer'].append(event)
        with observation('diagnostic.'+stage,metadata={'diagnostic_status':status,'diagnostic_code':code},input=event['details']):pass
    except Exception:LOG.warning('Request diagnostic event unavailable; execution unchanged')


def link_workspace(workspace):
    ctx=CURRENT.get()
    if ctx and ctx['store']:
        try:ctx['store'].link(ctx['request_id'],ctx['owner'],workspace)
        except Exception:LOG.warning('Diagnostic workspace link unavailable; execution unchanged')


@contextmanager
def job_context(root,owner,workspace):
    identifier=None
    try:identifier=Store(root).origin(owner,workspace)
    except Exception:LOG.warning('Diagnostic origin lookup unavailable; execution unchanged')
    # Force reset: long-lived workers must not inherit the first HTTP request.
    with request_context(identifier,force=True):
        bind(root,owner);link_workspace(workspace)
        record('execution','started',workspace_id=workspace,origin_record_found=identifier is not None)
        yield
