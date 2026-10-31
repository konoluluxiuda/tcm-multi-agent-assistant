"""Offline strategy comparison and a pre-execution source oracle.

This module measures structured business contracts. Scripted F/P/A controllers
are explicitly NOT evidence of LLM planning quality or free-text understanding.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from tcm.dataops.artifacts import verify
from tcm.dataops.baseline import validate_result
from tcm.dataops.contracts import Goal, Query, LABELS
from tcm.dataops.data_access import canonical, fingerprint
from tcm.dataops.graph import Engine
from tcm.dataops.model import SyntheticDriver
from tcm.dataops.workspace import Workspaces, Conflict


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class FixedPlanDriver:
    """Fixed host stages with normal cache/HITL/error handling, not a weak baseline."""
    label = 'offline_F_fixed_host'

    def __init__(self, goal, *, once_plan=False):
        self.label = 'offline_P_once_plan_stub' if once_plan else self.label
        self.plan = ('query_records', 'inspect_sources', 'lookup_document_fields',
                     'build_artifact', 'verify_delivery', 'finish_task')
        self.planning_stub_steps = int(once_plan)

    def decide(self, context):
        # Normal ambiguity handling is shared host behavior, not model replanning.
        if context['ambiguities']:
            return {'tool': 'ask_user', 'arguments': {'purpose': '固定流程的候选异常处理',
                    'source_id': context['ambiguities'][0]['source_id']}}
        needed = (not context['selected'], bool(context['uninspected']), bool(context['unread_fields']),
                  not context['artifact'], not context['verified'], True)
        tool = next(tool for tool, needed in zip(self.plan, needed) if needed)
        return {'tool': tool, 'arguments': {'purpose': '执行冻结阶段，已有效的阶段直接复用'}}


class MeasuredDriver:
    def __init__(self, strategy, goal):
        self.inner = SyntheticDriver() if strategy == 'A' else FixedPlanDriver(goal, once_plan=strategy == 'P')
        self.label = self.inner.label
        self.context_bytes = []

    def decide(self, context):
        self.context_bytes.append(len(canonical(context).encode()))
        return self.inner.decide(context)


class Probe:
    """Observe actual adapter reads, rather than trusting model-written traces."""
    def __init__(self, factory):
        self.factory, self.reads = factory, []

    def __call__(self):
        probe = self
        class Scope:
            def __enter__(inner):
                inner.source = probe.factory().__enter__()
                inspect = inner.source.inspect
                query = inner.source.query
                def observed(record, fields, **kwargs):
                    probe.reads.append({'kind': 'inspect', 'source_id': record['source_record_id'], 'fields': list(fields)})
                    return inspect(record, fields, **kwargs)
                def queried(spec):
                    probe.reads.append({'kind': 'query'})
                    return query(spec)
                inner.source.inspect, inner.source.query = observed, queried
                return inner.source
            def __exit__(inner, *args):
                return inner.source.__exit__(*args)
        return Scope()


class SourceOracle:
    """Read raw JSON + pre-index view; never call the tested query/inspect/collect."""
    keys = {'name_pinyin': 'Herb Name in Pinyin', 'latin_name': 'Herb Name in Latin',
            'medicinal_part': 'Medicinal Part', 'properties': 'Property'}
    mapping = {'Alpha': ['entry-0'], 'Beta': ['entry-1'], 'Gamma': ['entry-2', 'entry-3'], 'Delta': []}

    def __init__(self, fixture):
        self.fixture = Path(fixture); self.records = {}
        for path in sorted((self.fixture / 'raw/etcm_herbs').glob('*.json')):
            relative = 'etcm_herbs/' + path.name
            sid = 'etcm2-local:' + hashlib.sha256(relative.encode()).hexdigest()
            items = json.loads(path.read_text())['data'][0]['value']
            raw = {item['key']: (item['value'], pos) for pos, item in enumerate(items)}
            name = raw['Herb Name in Pinyin'][0]
            self.records[name] = dict(source_id=sid, raw=raw, relative=relative, sha256=fingerprint(path))
        view = json.loads((self.fixture / 'view/research_view.json').read_text())
        self.entries = {e['id']: e for e in view['entries']}
        # The original index is a separate source witness, read without its retrieval adapter.
        with sqlite3.connect((self.fixture / 'index/research.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            self.chunks = {r['chunk_id']: dict(r) for r in db.execute('SELECT * FROM chunks')}

    def rows(self, case, *, parent=False):
        goal = Goal.model_validate(case['parent_goal'] if parent else case['goal'])
        names = case.get('parent_names', case['expected_names']) if parent else case['expected_names']
        # Parent selection may be larger than the revised selection.
        if parent:
            names = [n for n in goal.query.names if n in self.records]
        rows = {}
        for name in names:
            record = self.records[name]; ids = self.mapping[name]
            choice = (case.get('answer') or {}).get('candidate_id')
            entry_id = ids[0] if len(ids) == 1 else choice if choice in ids else None
            mapping = ('unmatched' if not ids else 'ambiguous' if entry_id is None else
                       'medicinal_part_difference' if name == 'Beta' else 'fields_consistent_not_verified')
            cells = {}
            for field in goal.attributes:
                value, pos = record['raw'].get(self.keys.get(field), (None, None))
                cells[field] = {'value': value, 'status': 'present' if value is not None else 'missing',
                                'raw_key': self.keys.get(field), 'pointer': '/data/0/value/'+str(pos)+'/value'}
            for field in goal.document_fields:
                parts = [x for x in self.entries[entry_id]['fields'] if x['label'] == LABELS[field]
                         and x['scope'] in ('monograph', 'prepared_slices')] if entry_id else []
                value = parts[0]['raw_text'] if len(parts) == 1 else None
                status = (mapping if entry_id is None else 'missing' if not parts else 'ambiguous_field'
                    if len(parts) > 1 else 'candidate_scope_value' if parts[0]['scope'] == 'prepared_slices' else 'candidate_value')
                cells[field] = {'value': value, 'status': status, 'parts': parts, 'entry_id': entry_id}
            rows[record['source_id']] = {'name': name, 'mapping_status': mapping, 'cells': cells,
                                        'relative': record['relative'], 'sha256': record['sha256']}
        return rows

    def reference(self, case):
        if case.get('expected_rejection') or case.get('source_unavailable') or case.get('wait_only'):
            return {'expected_status': 'rejected' if case.get('expected_rejection') else case['expected_status']}
        if case['category'] == 'query':
            records = sorted(self.records.values(), key=lambda r: r['source_id'])
            if 'expected_sorted_slice' in case:
                a, b = case['expected_sorted_slice']; ids = [r['source_id'] for r in records[a:b]]
            else:
                ids = sorted(self.records[n]['source_id'] for n in case['expected_names'])
            return {'expected_status': 'complete', 'record_ids': ids, 'groups': case.get('expected_groups')}
        rows = self.rows(case)
        ref = {'expected_status': case['expected_status'], 'rows': rows}
        if case.get('parent_goal'):
            before = self.rows(case, parent=True)
            old = {(sid, f): (c['value'], c['status']) for sid, r in before.items() for f, c in r['cells'].items()}
            new = {(sid, f): (c['value'], c['status']) for sid, r in rows.items() for f, c in r['cells'].items()}
            ref['diff_keys'] = [list(k) for k in sorted(old.keys() | new.keys()) if k not in old or k not in new or old[k] != new[k]]
        return ref

    def score(self, case, reference, doc, store):
        errors = []
        if doc['status'] != reference['expected_status']:
            errors.append('unexpected_task_status')
        if 'rows' not in reference:
            return errors
        result = doc.get('result')
        if not result:
            return errors + ['missing_delivery']
        wanted = reference['rows']; actual = {r['source_id']: r for r in result['rows']}
        if set(actual) != set(wanted) or len(actual) != len(result['rows']):
            errors.append('wrong_record_set')
        for sid, expected in wanted.items():
            row = actual.get(sid)
            if not row:
                continue
            if row['mapping_status'] != expected['mapping_status'] or row['identity_verified']:
                errors.append('wrong_or_promoted_mapping')
            if set(row['cells']) != set(expected['cells']):
                errors.append('wrong_columns')
            for field, cell in expected['cells'].items():
                observed = row['cells'].get(field, {})
                if (observed.get('value'), observed.get('status')) != (cell['value'], cell['status']):
                    errors.append('wrong_field_value_or_status:'+field)
                witnesses = [result['evidence'].get(ref) for ref in observed.get('evidence_ids', [])]
                if any(not x or x['source_id'] != sid for x in witnesses):
                    errors.append('invalid_provenance_owner'); continue
                if 'parts' in cell:
                    expected_parts = {(cell['entry_id'], p['label'], p['scope'], p['raw_text']) for p in cell['parts']}
                    seen = set()
                    for witness in witnesses:
                        source = witness['source']; chunk = self.chunks.get(source.get('chunk_id'))
                        if not chunk or any(source.get(k) != chunk[k] for k in ('entry_id', 'label', 'scope')) or source.get('text') != chunk['raw_text']:
                            errors.append('invented_document_position'); continue
                        if source.get('payload') != json.loads(chunk['payload']):
                            errors.append('altered_page_or_document_witness')
                        seen.add((source['entry_id'], source['label'], source['scope'], source['text']))
                    if seen != expected_parts or len(witnesses) != len(cell['parts']):
                        errors.append('missing_or_wrong_document_evidence')
                elif cell['value'] is not None:
                    if len(witnesses) != 1:
                        errors.append('missing_structured_evidence'); continue
                    source = witnesses[0]['source']
                    if (source.get('file_sha256') != expected['sha256'] or source.get('relative_file_path') != expected['relative'] or
                        source.get('raw_field_key') != cell['raw_key'] or source.get('json_pointer') != cell['pointer'] or
                        source.get('raw_value') != cell['value']):
                        errors.append('wrong_structured_position')
        try:
            validate_result(result)
            path, _ = store.artifact('benchmark-owner', doc['id'])
            verify(path, result)
        except (ValueError, KeyError, OSError):
            errors.append('invalid_workbook_or_reference')
        if 'diff_keys' in reference:
            observed = sorted([d['source_id'], d['field']] for d in result.get('diff', []))
            if observed != reference['diff_keys']:
                errors.append('wrong_version_diff')
        return errors


def run_case(case, reference, oracle, strategy, factory, directory, *, driver_factory=None):
    started = time.perf_counter()
    driver = driver_factory(strategy, case) if driver_factory else MeasuredDriver(strategy, case.get('goal'))
    probe = Probe(factory); store = Workspaces(directory); doc = None; parent_hash = None
    errors = []; rejected_stale = False
    try:
        if case.get('expected_rejection'):
            try:
                Query.model_validate(case['query']) if 'query' in case else Goal.model_validate(case['goal'])
            except ValueError:
                return {'status': 'passed', 'outcome': 'rejected', 'external_model_calls': 0, 'route': 'schema_guard'}
            return {'status': 'failed', 'outcome': 'not_rejected', 'errors': ['scope_guard_missing']}
        if case['category'] == 'query':
            with probe() as sources:
                data = sources.query(Query.model_validate(case['query']))
            if data['selected_ids'] != reference['record_ids']:
                errors.append('wrong_query_record_set')
            if reference['groups'] is not None:
                if {x['value']: x['count'] for x in data['groups']} != reference['groups']:
                    errors.append('wrong_group_counts')
            return {'status': 'failed' if errors else 'passed', 'outcome': 'query_complete', 'errors': errors,
                    'route': 'shared_direct_query', 'leaf_reads': 1, 'external_model_calls': 0,
                    'elapsed_seconds': time.perf_counter()-started}
        with factory() as sources:
            revision = sources.revision
        def create(goal, key, **kw):
            doc, _ = store.reserve_agent('benchmark-owner', key, Goal.model_validate(goal).model_dump(), revision,
                question=case['question'], **kw)
            return doc
        def execute(doc, answer=None):
            return Engine(store, 'benchmark-owner', doc['id'], probe, driver).run(answer)
        def continue_task(doc):
            if doc['status'] != 'waiting_input' or case.get('wait_only'):
                return doc
            p = doc['pending']; configured = case.get('answer', {'operation': 'skip'})
            answer = {'question_id':p['question_id'], 'revision':doc['revision'], 'candidate_id':None, **configured}
            nonlocal rejected_stale
            if case.get('stale_answer'):
                try:
                    execute(doc, {**answer, 'question_id': 'f'*32})
                except Conflict:
                    rejected_stale = True
            # Engine reopens its SQLite checkpointer; driver is stateless across this boundary.
            return execute(doc, answer)
        parent = None
        if case.get('parent_goal'):
            parent = continue_task(execute(create(case['parent_goal'], 'parent-task')))
            if not parent.get('artifact') or not parent.get('result'):
                progress = parent.get('progress', {})
                return {'status': 'failed', 'outcome': 'blocked_parent', 'errors': ['parent_missing_delivery'],
                    'parent_status': parent['status'], 'parent_error': parent.get('error'),
                    'workspace_id': parent['id'], 'child_created': False,
                    'model_diagnostics': progress.get('model_diagnostics'),
                    'host_policy_steps_including_parent': len(driver.context_bytes),
                    'external_model_calls': 0, 'elapsed_seconds': time.perf_counter()-started}
            path, _ = store.artifact('benchmark-owner', parent['id']); parent_hash = fingerprint(path)
        cut = len(probe.reads)
        extras = {} if parent is None else dict(parent_id=parent['id'], expected_revision=parent['revision'],
            refresh_source_ids=[oracle.records[n]['source_id'] for n in case.get('refresh_names', [])])
        doc = create(case['goal'], 'current-task', **extras)
        if case.get('source_unavailable'):
            def unavailable():
                raise FileNotFoundError('injected source unavailability')
            try:
                Engine(store, 'benchmark-owner', doc['id'], unavailable, driver).run()
            except FileNotFoundError:
                return {'status': 'passed', 'outcome': 'failed', 'reason': 'source_unavailable',
                        'external_model_calls': 0, 'route': 'source_failure_guard'}
            return {'status': 'failed', 'outcome': 'not_failed', 'errors': ['unavailable_source_not_reported']}
        doc = continue_task(execute(doc))
        errors.extend(oracle.score(case, reference, doc, store))
        reads = probe.reads[cut:]
        if 'expected_new_reads' in case:
            expected_fields = set(case['expected_new_reads'])
            actual_fields = {f for r in reads if r['kind']=='inspect' for f in r['fields']}
            if actual_fields != expected_fields or any(r['kind']=='inspect' and not r['fields'] for r in reads):
                errors.append('old_fields_requeried_or_new_fields_not_read')
        if case.get('refresh_names'):
            expected_ids = {oracle.records[n]['source_id'] for n in case['refresh_names']}
            if {r['source_id'] for r in reads if r['kind']=='inspect'} != expected_ids:
                errors.append('wrong_refresh_scope')
        if parent:
            path, _ = store.artifact('benchmark-owner', parent['id'])
            if fingerprint(path) != parent_hash:
                errors.append('parent_artifact_changed')
        if case.get('stale_answer') and not rejected_stale:
            errors.append('stale_answer_not_rejected')
        progress = doc.get('progress', {})
        return {'status': 'failed' if errors else 'passed', 'outcome': doc['status'], 'errors': errors,
                'stop_reason': doc.get('error'), 'artifact_present': bool(doc.get('artifact')),
                'verification_action_completed': any(e['tool']=='verify_delivery' and e['status']=='ok'
                                                     for e in progress.get('events', [])),
                'finish_action_completed': any(e['tool']=='finish_task' and e['status']=='ok'
                                              for e in progress.get('events', [])),
                'model_diagnostics': progress.get('model_diagnostics'),
                'workspace_id': doc['id'], 'route': 'shared_graph_tools_and_validator',
                'tool_calls': progress.get('tool_calls'), 'leaf_reads': progress.get('leaf_reads'),
                'logical_adapter_reads': reads, 'policy_context_max_bytes': max(driver.context_bytes, default=0),
                'host_policy_steps_including_parent': len(driver.context_bytes),
                'planning_stub_steps': 1 if strategy=='P' else 0, 'external_model_calls': 0,
                'provider_usage': None, 'cost': None, 'elapsed_seconds': time.perf_counter()-started}
    except Exception as exc:
        return {'status': 'failed', 'outcome': 'exception', 'error_type': type(exc).__name__,
                'errors': errors, 'external_model_calls': 0, 'elapsed_seconds': time.perf_counter()-started}
