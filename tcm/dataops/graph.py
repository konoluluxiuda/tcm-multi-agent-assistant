"""Single-agent DataOps graph. Native SQLite checkpoints own all execution state.

Only JSON primitives are checkpointed. A separate dispatch receipt protects the
checkpoint/model boundary: an unknown request is halted, never resent on replay.
"""
from contextlib import closing
from collections import Counter
from copy import deepcopy
import fcntl
import hashlib
import json
import sqlite3
import time
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from tcm.dataops.artifacts import publish, verify
from tcm.dataops.baseline import collect, validate_result
from tcm.dataops.contracts import Goal
from tcm.dataops.data_access import canonical, fingerprint, unrequested_inspection
from tcm.dataops.model import ModelResponseRejected, valid_arguments
from tcm.dataops.workspace import Conflict


class State(TypedDict):
    data: dict


class ToolRejected(ValueError):
    pass


class Limits:
    model_calls = 8
    tool_calls = 24
    leaf_reads = 200
    active_seconds = 300
    context_bytes = 16000


def summary(s):
    inspections = s['inspections']
    ids = s['selected']['selected_ids'] if s['selected'] else []
    ambiguities = [{'source_id': sid, 'candidates': x['candidates']} for sid, x in inspections.items()
                   if x['status'] == 'ambiguous' and sid not in s['decisions']]
    unread = {sid: [f for f in s['goal']['document_fields'] if f not in x['fields']]
                for sid, x in inspections.items() if x['status'] not in ('unmatched', 'ambiguous', 'source_mismatch')
                and any(f not in x['fields'] for f in s['goal']['document_fields'])}
    uninspected = [sid for sid in ids if sid not in inspections]
    evidence = evidence_items(s)
    already_read = set(s.get('evidence_reads', []))
    limits = s.get('execution_limits', {'model_calls': Limits.model_calls, 'tool_calls': Limits.tool_calls,
                                       'leaf_reads': Limits.leaf_reads})
    audit = s['goal']['completion_policy'] == 'audit'
    observation = deepcopy(s.get('observation'))
    if observation and 'reads' in observation:
        observation['read_count'] = len(observation['reads'])
        observation['reads'] = observation['reads'][:10]
    return {'original_question': s['question'], 'goal': s['goal'], 'plan': s['plan'][-8:],
            'selected': s['selected'] is not None, 'record_ids': ids[:10], 'selected_count': len(ids),
            'uninspected': uninspected[:10], 'uninspected_count': len(uninspected),
            'ambiguities': ambiguities[:3], 'ambiguity_count': len(ambiguities),
            'unread_fields': dict(list(unread.items())[:10]), 'unread_record_count': len(unread),
            'mapping_states': dict(Counter(x['status'] for x in inspections.values())),
            'field_states': dict(Counter(c['status'] for x in inspections.values() for c in x['fields'].values())),
            'field_exceptions': [{'source_id': sid, 'field': field, 'status': cell['status']}
                for sid, x in inspections.items() for field, cell in x['fields'].items()
                if cell['status'] not in ('candidate_value', 'present')][:20],
            'artifact': bool(s.get('artifact')), 'verified': s.get('verified', False),
            'evidence_refs': list(evidence_items(s))[:80],
            'evidence_already_read': sorted(already_read)[:80],
            'unread_evidence_count': len(set(evidence) - already_read),
            'budget': {'decision_slots_remaining_including_current': max(0, limits['model_calls'] - s['model_calls']),
                       'tool_actions_remaining': max(0, limits['tool_calls'] - s['tool_calls']),
                       'source_reads_remaining': max(0, limits['leaf_reads'] - s['leaf_reads'])},
            'delivery': {'ready_to_build': s['selected'] is not None and not uninspected and not unread,
                         'ready_to_verify': bool(s.get('artifact')) and not s.get('verified', False),
                         'ready_to_finish': bool(s.get('artifact')) and s.get('verified', False),
                         'minimum_delivery_actions_remaining': int(not s.get('artifact')) + int(not s.get('verified')) + 1,
                         'audit_allows_reported_missing_or_ambiguous_values': audit,
                         'unread_fields_are_unfetched_not_null_values': True,
                         'scope_ambiguity_must_remain_unknown': True},
            'progress_watch': {'consecutive_no_progress': s.get('stalls', 0), 'stop_after': 2,
                               'inspections_needed': bool(uninspected), 'field_reads_needed': bool(unread)},
            'observation': observation, 'model_calls': s['model_calls'],
            'response_corrections': s.get('response_corrections', 0),
            'action_contract': {'tool_calls_per_response': 1, 'execute_rejected_calls': False},
            'tool_calls': s['tool_calls'], 'leaf_reads': s['leaf_reads']}


def projection(s):
    ctx = summary(s)
    return {k: ctx[k] for k in ('plan', 'model_calls', 'tool_calls', 'leaf_reads', 'uninspected', 'unread_fields')} | {
        'last_node': s.get('last_node'), 'driver': s['driver'], 'reason': s.get('reason', ''),
        'model_diagnostics': s.get('model_diagnostics'),
        'response_corrections': s.get('response_corrections', 0),
        'active_seconds': round(s['active_seconds'], 3), 'reused_cells': s.get('reused_cells', 0),
        'events': s['events'][-24:]}


def evidence_items(s):
    output = dict((s.get('result') or {}).get('evidence', {}))
    for sid, inspected in s['inspections'].items():
        for cell in inspected['fields'].values():
            for witness in cell['evidence']:
                item = {'source_id': sid, 'kind': 'pharmacopoeia', 'source': witness}
                key = 'E-' + hashlib.sha256(canonical(item).encode()).hexdigest()[:24]
                output[key] = item
    return output


def _parent_inspections(parent, goal, selected, refresh):
    output = {}
    if not parent:
        return output, 0
    reused = 0
    for row in parent['result']['rows']:
        sid = row['source_id']
        if sid not in selected or sid in refresh:
            continue
        fields = {}
        for field in goal['document_fields']:
            if field not in row['cells']:
                continue
            cell = row['cells'][field]
            fields[field] = {'value': cell['value'], 'status': cell['status'], 'evidence': [
                parent['result']['evidence'][ref]['source'] for ref in cell['evidence_ids']]}
        output[sid] = {'status': row['mapping_status'], 'candidates': row['candidates'],
                       'comparison': row['comparison'], 'fields': fields, 'identity_verified': False}
        reused += len(set(row['cells']) & set(goal['attributes'] + list(fields)))
    return output, reused


def difference(before, after):
    old = {(r['source_id'], f): c for r in before['rows'] for f, c in r['cells'].items()}
    new = {(r['source_id'], f): c for r in after['rows'] for f, c in r['cells'].items()}
    diff = [{'source_id': sid, 'field': field, 'before': old.get((sid, field)), 'after': new.get((sid, field))}
            for sid, field in sorted(old.keys() | new.keys()) if old.get((sid, field)) != new.get((sid, field))]
    old_mapping = {r['source_id']: r['mapping_status'] for r in before['rows']}
    for row in after['rows']:
        if row['source_id'] in old_mapping and old_mapping[row['source_id']] != row['mapping_status']:
            diff.append({'source_id': row['source_id'], 'field': 'mapping_status',
                         'before': old_mapping[row['source_id']], 'after': row['mapping_status']})
    return diff


class Engine:
    def __init__(self, store, owner, identifier, sources_factory, driver, *, guard=lambda: None,
                 on_state=lambda s: None, limits=None):
        self.store, self.owner, self.identifier = store, owner, identifier
        self.doc = store.get(owner, identifier)
        if self.doc['mode'] != 'dynamic_A':
            raise Conflict('Only DataOps graph runs may enter this engine')
        self.sources_factory, self.driver = sources_factory, driver
        self.max_response_corrections = getattr(driver, 'max_response_corrections', 0)
        if type(self.max_response_corrections) is not int or self.max_response_corrections not in (0, 1):
            raise ValueError('Response correction allowance must be explicitly 0 or 1')
        self.guard, self.on_state, self.limits = guard, on_state, limits or Limits()
        self.directory = store.root / identifier
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.thread_id = hashlib.sha256(canonical([owner, identifier, self.doc['revision']]).encode()).hexdigest()
        from pathlib import Path
        package = Path(__file__).parent
        self.implementation_revision = hashlib.sha256(canonical({name: fingerprint(package / name)
            for name in ('graph.py', 'model.py', 'contracts.py', 'data_access.py', 'baseline.py', 'artifacts.py')}).encode()).hexdigest()

    def _receipt(self, call_id, context):
        path = self.directory / 'dispatch.sqlite3'
        fingerprint = hashlib.sha256(canonical(context).encode()).hexdigest()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS dispatch(id INTEGER PRIMARY KEY, fingerprint TEXT, response TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS dispatch_diagnostics(id INTEGER PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint,response FROM dispatch WHERE id=?', (call_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise Conflict('Model request changed during replay')
                if row[1] is None:
                    return None, True
                return json.loads(row[1]), False
            db.execute('INSERT INTO dispatch VALUES(?,?,NULL)', (call_id, fingerprint))
        # Commit reservation BEFORE network dispatch; no automatic retry on exception.
        self.guard()
        try:
            response = self.driver.decide(context)
            encoded = canonical(response)
            if len(encoded.encode()) > 32000:
                raise ValueError('Oversized model action')
        except ModelResponseRejected as exc:
            response = {'_rejected_response': {'reason': exc.reason, 'diagnostics': exc.diagnostics}}
            encoded = canonical(response)
        except Exception as exc:
            # Exception messages may contain source text or credentials. Keep types/status only.
            known = {'TimeoutError', 'ReadTimeout', 'ConnectError', 'APIConnectionError', 'APITimeoutError',
                     'BadRequestError', 'AuthenticationError', 'RateLimitError', 'ValueError', 'TypeError', 'AttributeError'}
            kind = type(exc).__name__
            diagnostics = {'exception_type': kind if kind in known else 'adapter_exception'}
            status_code = getattr(exc, 'status_code', None)
            if type(status_code) is int and 400 <= status_code <= 599:
                diagnostics['http_status'] = status_code
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('INSERT OR REPLACE INTO dispatch_diagnostics VALUES(?,?)', (call_id, canonical(diagnostics)))
            return None, True
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('UPDATE dispatch SET response=? WHERE id=? AND response IS NULL', (encoded, call_id))
        return response, False

    def _wrap(self, name, fn):
        def run(state):
            self.guard()
            s = deepcopy(state['data']); started = time.monotonic()
            if name != 'wait' and s['active_seconds'] >= self.limits.active_seconds:
                s.update(status='partial', reason='active_time_budget', last_node=name)
                return {'data': s}
            from tcm.tracing import observation
            with observation('dataops.' + ('decision' if name == 'model' else name), metadata={'workspace_id': self.identifier,
                    'driver': self.driver.label, 'driver_steps_before': s['model_calls'],
                    'tool_calls_before': s['tool_calls']}) as trace:
                from tcm.dataops.diagnostics import record
                record('execution_node','started',node=name,workspace_id=self.identifier,
                       driver=self.driver.label,action=s.get('action'),decision_step=s['model_calls'],tool_calls=s['tool_calls'])
                try:result = fn(s)
                except BaseException as exc:
                    record('execution_node','failed',code='node_exception',node=name,exception_type=type(exc).__name__)
                    raise
                after = result if result is not None else s
                trace.update(output={'status': after['status'], 'pending': bool(after.get('pending')),
                                     'latest_event': after.get('events', [])[-1:]})
                record('execution_node','finished',node=name,workspace_id=self.identifier,action=after.get('action'),
                       status_result=after['status'],reason=after.get('reason'),latest_event=after.get('events',[])[-1:])
            s = result if result is not None else s
            s['active_seconds'] += time.monotonic() - started
            s['last_node'] = name
            if len(canonical(s).encode()) > 8_000_000:
                raise ValueError('Checkpoint exceeds task storage limit')
            return {'data': s}
        return run

    def decide(self, s):
        s.pop('correction_pending', None)
        s['action'] = None
        if s['model_calls'] >= self.limits.model_calls:
            s.update(status='partial', reason='model_budget'); return
        context = summary(s)
        if len(canonical(context).encode()) > self.limits.context_bytes:
            s.update(status='partial', reason='context_budget'); return
        action, unknown = self._receipt(s['model_calls'] + 1, context)
        s['model_calls'] += 1
        if unknown:
            with closing(sqlite3.connect(self.directory / 'dispatch.sqlite3')) as db:
                row = db.execute('SELECT document FROM dispatch_diagnostics WHERE id=?', (s['model_calls'],)).fetchone()
                if row:
                    s['model_diagnostics'] = json.loads(row[0])
            s.update(status='interrupted', reason='model_dispatch_unknown'); return
        if isinstance(action, dict) and set(action) == {'_rejected_response'}:
            rejection = action['_rejected_response']
            s['model_diagnostics'] = rejection['diagnostics']
            s['usage'].append(rejection['diagnostics'].get('usage', {}))
            s['events'].append({'tool': 'model_response', 'status': 'rejected', 'reason': rejection['reason']})
            if (s.get('response_corrections', 0) < self.max_response_corrections
                    and s['model_calls'] < self.limits.model_calls):
                s['response_corrections'] = s.get('response_corrections', 0) + 1
                s['observation'] = {'tool': 'model_response', 'status': 'rejected',
                    'reason': rejection['reason'], 'diagnostics': rejection['diagnostics'],
                    'required_tool_calls': 1, 'executed_tool_calls': 0}
                s['correction_pending'] = True
                return
            s.update(status='partial', reason='model_response_rejected'); return
        if not isinstance(action, dict) or set(action) - {'tool', 'arguments', 'usage'}:
            s.update(status='partial', reason='invalid_model_action'); return
        s['action'] = action
        s['usage'].append(action.get('usage', {}))

    def tools(self, s):
        if s['tool_calls'] >= self.limits.tool_calls:
            s.update(status='partial', reason='tool_budget'); return
        action = s['action']; tool = action.get('tool'); args = action.get('arguments', {})
        if not valid_arguments(tool, args):
            s.update(status='partial', reason='invalid_tool_arguments'); return
        s['tool_calls'] += 1
        step = {'id': 'step-' + str(s['tool_calls']), 'tool': tool, 'purpose': args['purpose'], 'status': 'executed'}
        s['plan'].append(step)
        before = canonical([s['selected'] is not None, s['inspections'], s.get('artifact'), s['verified'], s['decisions'], s.get('evidence_reads', [])])
        observation = {'tool': tool, 'status': 'ok'}
        try:
            with self.sources_factory() as sources:
                if sources.revision != s['source_revision']:
                    raise Conflict('Source revision changed; old cells must not be reused')
                def reads(count):
                    if s['leaf_reads'] + count > self.limits.leaf_reads:
                        raise ToolRejected('leaf_read_budget')
                    s['leaf_reads'] += count
                    self.guard()
                if tool == 'query_records':
                    if s['selected'] is None:
                        reads(1)
                        s['selected'] = sources.query(Goal.model_validate(s['goal']).query)
                        parent = self.store.get(self.owner, self.doc['parent_id']) if self.doc.get('parent_id') else None
                        if parent:
                            validate_result(parent['result'])
                        s['inspections'], s['reused_cells'] = _parent_inspections(
                            parent, s['goal'], s['selected']['selected_ids'], self.doc.get('refresh_source_ids', []))
                        if parent:
                            s['decisions'].update({sid: choice for sid, choice in parent['result'].get('decisions', {}).items()
                                                   if sid in s['inspections']})
                        invalid = set(self.doc.get('refresh_source_ids', [])) - set(s['selected']['selected_ids'])
                        if invalid:
                            raise ToolRejected('Refresh outside result scope')
                    observation.update(returned=s['selected']['returned'], total=s['selected']['total'],
                                       unknown_names=s['selected']['unknown_names'], reused_cells=s.get('reused_cells', 0))
                elif s['selected'] is None:
                    observation.update(status='blocked', missing=['query_records'])
                elif tool in ('inspect_sources', 'lookup_document_fields'):
                    requested = args.get('fields', s['goal']['document_fields'])
                    if (not isinstance(requested, list) or any(not isinstance(f, str) for f in requested) or
                            set(requested) - set(s['goal']['document_fields']) or len(requested) > 4):
                        raise ToolRejected('Fields exceed goal scope')
                    read_fields = []
                    records = s['selected']['records']
                    # Mechanical batching belongs to host, not repeated model planning.
                    for start in range(0, len(records), 10):
                        for record in records[start:start + 10]:
                            sid = record['source_record_id']; old = s['inspections'].get(sid)
                            if tool == 'inspect_sources' and old:
                                continue
                            if tool == 'lookup_document_fields' and not old:
                                observation.update(status='blocked', missing=['inspect_sources']); continue
                            if old and old['status'] in ('ambiguous', 'unmatched', 'source_mismatch'):
                                continue
                            fields = [] if tool == 'inspect_sources' else [f for f in requested if f not in old['fields']]
                            if tool == 'lookup_document_fields' and not fields:
                                continue
                            if not s['goal']['document_fields']:
                                current = unrequested_inspection()
                            else:
                                reads(1)
                                current = sources.inspect(record, fields, candidate_id=s['decisions'].get(sid) or None)
                            if old:
                                current['fields'] = {**old['fields'], **current['fields']}
                            s['inspections'][sid] = current
                            read_fields.append({'source_id': sid, 'fields': fields})
                    observation['reads'] = read_fields
                elif tool == 'compare_fields':
                    observation['mapping_states'] = summary(s)['mapping_states']
                    observation['field_states'] = summary(s)['field_states']
                elif tool == 'ask_user':
                    sid = args.get('source_id'); inspected = s['inspections'].get(sid)
                    if not inspected or inspected['status'] != 'ambiguous' or sid in s['decisions']:
                        raise ToolRejected('No unresolved candidate decision for this record')
                    s['pending'] = {'question_id': uuid4().hex, 'revision': self.doc['revision'], 'source_id': sid,
                        'candidates': inspected['candidates'], 'operations': ['choose', 'skip', 'cancel']}
                    s['status'] = 'waiting_input'
                elif tool == 'read_evidence':
                    refs = args.get('evidence_ids', [])
                    evidence = evidence_items(s)
                    if (not isinstance(refs, list) or not 1 <= len(refs) <= 3 or
                            any(not isinstance(ref, str) or ref not in evidence for ref in refs)):
                        raise ToolRejected('Unknown evidence handle')
                    observation['fragments'] = [{'evidence_id': ref,
                        'text': str(evidence[ref]['source'].get('text', evidence[ref]['source'].get('raw_value', '')))[:800],
                        'truncated': len(str(evidence[ref]['source'].get('text', evidence[ref]['source'].get('raw_value', '')))) > 800}
                        for ref in refs]
                    s['evidence_reads'] = sorted(set(s.get('evidence_reads', [])) | set(refs))
                elif tool == 'build_artifact':
                    ctx = summary(s)
                    if ctx['uninspected'] or ctx['unread_fields']:
                        observation.update(status='blocked', missing=ctx['unread_fields'], uninspected=ctx['uninspected'])
                    else:
                        # Audit policy may report unresolved candidates as unknown, never invent a mapping.
                        result = collect(sources, s['goal'], selected=s['selected'], inspections=s['inspections'])
                        result.update(mode='dynamic_A', model_calls=s['model_calls'],
                            execution_label='A / ' + s['driver'], trace=s['events'] + [observation],
                            decisions=s['decisions'], usage=s['usage'])
                        if self.doc.get('parent_id'):
                            parent = self.store.get(self.owner, self.doc['parent_id'])
                            result['diff'] = difference(parent['result'], result)
                        validate_result(result)
                        s['result'] = result
                        self.guard(); sources.assert_unchanged()
                        s['artifact'] = publish(self.directory, result, version=self.doc['revision'], reconcile=True)
                elif tool == 'verify_delivery':
                    if not s.get('artifact'):
                        observation.update(status='blocked', missing=['build_artifact'])
                    else:
                        validate_result(s['result'])
                        observation['verification'] = verify(self.directory / s['artifact']['filename'], s['result'])
                        s['verified'] = True
                elif tool == 'finish_task':
                    if not s.get('verified'):
                        observation.update(status='blocked', missing=['verify_delivery'])
                    else:
                        s['status'] = s['result']['status']
                sources.assert_unchanged()
        except ToolRejected as exc:
            observation.update(status='rejected', reason=str(exc)[:200])
        except TimeoutError:
            s['read_timeouts'] = s.get('read_timeouts', 0) + 1
            observation.update(status='read_timeout', reason='local_read_timeout')
            if s['read_timeouts'] > 1:
                s.update(status='partial', reason='read_timeout_budget')
        s['observation'] = observation
        s['events'].append(observation)
        if tool == 'finish_task' and s.get('verified'):
            s['result'].update(model_calls=s['model_calls'], tool_calls=s['tool_calls'], leaf_reads=s['leaf_reads'],
                               trace=s['events'], usage=s['usage'])
        after = canonical([s['selected'] is not None, s['inspections'], s.get('artifact'), s['verified'], s['decisions'], s.get('evidence_reads', [])])
        s['stalls'] = s['stalls'] + 1 if before == after and s['status'] == 'running' else 0
        if s['stalls'] >= 2:
            s.update(status='partial', reason='no_progress')

    def wait(self, s):
        # This node has NO model requests or file writes before interrupt.
        answer = interrupt(s['pending'])
        pending = s['pending']
        if (not isinstance(answer, dict) or answer.get('question_id') != pending['question_id'] or
                answer.get('revision') != pending['revision'] or answer.get('operation') not in pending['operations']):
            raise Conflict('Stale or invalid decision')
        sid = pending['source_id']; operation = answer['operation']
        if operation == 'cancel':
            s.update(status='cancelled', pending=None); return
        if operation == 'choose':
            choice = answer.get('candidate_id')
            if choice not in {c['entry_id'] for c in pending['candidates']}:
                raise Conflict('Candidate is outside this question')
            # Invalidate ALL fields depending on this mapping, including reused parent cells.
            s['decisions'][sid] = choice
            s['inspections'][sid] = {'status': 'candidate_selected_pending', 'fields': {},
                'candidates': [c for c in pending['candidates'] if c['entry_id'] == choice],
                'comparison': None, 'identity_verified': False}
        else:
            s['decisions'][sid] = ''
        s['answered'].append({'question_id': pending['question_id'], 'answer': answer})
        s.update(status='running', pending=None, observation={'tool': 'ask_user', 'status': operation})

    def graph(self, saver):
        graph = StateGraph(State)
        for name, fn in (('model', self.decide), ('tools', self.tools), ('wait', self.wait)):
            graph.add_node(name, self._wrap(name, fn))
        graph.add_edge(START, 'model')
        def after_model(st):
            if st['data']['status'] != 'running':
                return END
            return 'model' if st['data'].get('correction_pending') else 'tools'
        graph.add_conditional_edges('model', after_model)
        graph.add_conditional_edges('tools', lambda st: 'wait' if st['data']['status'] == 'waiting_input'
                                    else 'model' if st['data']['status'] == 'running' else END)
        graph.add_conditional_edges('wait', lambda st: 'model' if st['data']['status'] == 'running' else END)
        return graph.compile(checkpointer=saver)

    def run(self, answer=None):
        from tcm.tracing import observation
        with observation('dataops.execution', input={'question': self.doc['question'], 'goal': self.doc['goal']},
                metadata={'workspace_id': self.identifier, 'revision': self.doc['revision'],
                          'parent_id': self.doc.get('parent_id'), 'source_revision': self.doc['source_revision'],
                          'driver': self.driver.label, 'resume_segment': answer is not None,
                          **({'provider_requests': 0} if self.driver.label == 'synthetic_observation_policy' else {})}) as trace:
            result = self._run(answer)
            trace.update(output={'status': result['status'], 'artifact': result.get('artifact'),
                                 'progress': result.get('progress')},
                         level='WARNING' if result['status'] in ('partial','interrupted','failed') else 'DEFAULT',
                         status_message=result.get('error') or result['status'])
            from tcm.dataops.diagnostics import record
            record('delivery','finished',status_result=result['status'],artifact=result.get('artifact'),
                   business_acceptance='not_evaluated')
            return result

    def _run(self, answer=None):
        with (self.directory / 'execution.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Conflict('This graph is already executing') from None
            with closing(sqlite3.connect(self.directory / 'graph.sqlite3', check_same_thread=False)) as conn:
                saver = SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[], allowed_json_modules=[]))
                graph = self.graph(saver)
                config = {'configurable': {'thread_id': self.thread_id}, 'recursion_limit': 90}
                snapshot = graph.get_state(config)
                if snapshot.values:
                    state = snapshot.values['data']
                    if state['source_revision'] != self.doc['source_revision'] or state['driver'] != self.driver.label:
                        raise Conflict('Graph binding changed')
                    if state.get('implementation_revision') != self.implementation_revision:
                        raise Conflict('Execution code changed; start a new run instead of replaying')
                    if state.get('response_correction_limit', 0) != self.max_response_corrections:
                        raise Conflict('Response correction policy changed; start a new run')
                    if state.get('execution_limits') != self._execution_limits():
                        raise Conflict('Execution limits changed; start a new run')
                    if answer is not None and any(x['answer'] == answer for x in state['answered']):
                        answer = None
                    if answer is not None:
                        if state.get('pending') is None:
                            if any(x['answer'] == answer for x in state['answered']):
                                return self.store.get(self.owner, self.identifier)
                            raise Conflict('No pending decision')
                        # Validate before invoking Command; an invalid choice must not poison the checkpoint.
                        p = state['pending']
                        if (answer.get('question_id') != p['question_id'] or answer.get('revision') != p['revision'] or
                                answer.get('operation') not in p['operations'] or answer.get('operation') == 'choose' and
                                answer.get('candidate_id') not in {c['entry_id'] for c in p['candidates']}):
                            raise Conflict('Stale decision or invalid candidate')
                        command = Command(resume=answer)
                    elif state['status'] == 'waiting_input' or not snapshot.next:
                        return self._project(state)
                    else:
                        command = None
                else:
                    if answer is not None:
                        raise Conflict('No graph is waiting')
                    state = dict(question=self.doc['question'], goal=self.doc['goal'], source_revision=self.doc['source_revision'],
                        selected=None, inspections={}, decisions={}, answered=[], pending=None, plan=[], events=[],
                        model_calls=0, tool_calls=0, leaf_reads=0, active_seconds=0.0, stalls=0, usage=[],
                        status='running', result=None, artifact=None, verified=False, driver=self.driver.label)
                    state.update(response_corrections=0, response_correction_limit=self.max_response_corrections)
                    state['execution_limits'] = self._execution_limits()
                    state['implementation_revision'] = self.implementation_revision
                    command = {'data': state}
                for item in graph.stream(command, config, stream_mode='values', durability='sync'):
                    state = item['data']
                    self.guard()
                    self._project(state)
                    self.on_state(projection(state))
                return self._project(graph.get_state(config).values['data'])

    def _project(self, state):
        return self.store.project(self.owner, self.identifier, status=state['status'], pending=state.get('pending'),
            progress=projection(state), result=state.get('result'), artifact=state.get('artifact'), error=state.get('reason'))

    def _execution_limits(self):
        return {key: getattr(self.limits, key) for key in
                ('model_calls', 'tool_calls', 'leaf_reads', 'context_bytes', 'active_seconds')}
