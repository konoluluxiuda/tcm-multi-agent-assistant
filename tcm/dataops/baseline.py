"""F baseline: source read -> field checks -> artifact -> exact verification, zero LLM calls."""
from collections import Counter
import hashlib

from tcm.dataops.contracts import Goal, LABELS
from tcm.dataops.data_access import canonical
from tcm.dataops.artifacts import publish


def collect(sources, goal, *, selected=None, inspections=None):
    goal = Goal.model_validate(goal)
    selected = sources.query(goal.query) if selected is None else selected
    rows, issues, evidence, trace = [], [], {}, []
    trace.append({'tool': 'query_records', 'returned': selected['returned'], 'total': selected['total']})

    def add_evidence(sid, kind, source):
        item = {'source_id': sid, 'kind': kind, 'source': source}
        key = 'E-' + hashlib.sha256(canonical(item).encode()).hexdigest()[:24]
        evidence[key] = item
        return key

    for record in selected['records']:
        sid = record['source_record_id']
        inspected = sources.inspect(record, goal.document_fields) if inspections is None else inspections[sid]
        trace.append({'tool': 'inspect_sources', 'source_id': sid, 'status': inspected['status']})
        row = {'source_id': sid, 'name': record['fields']['name_pinyin']['value'],
               'mapping_status': inspected['status'], 'candidates': inspected['candidates'],
               'comparison': inspected['comparison'], 'identity_verified': False, 'cells': {}}
        for field in goal.attributes:
            cell = record['fields'][field]
            refs = [add_evidence(sid, 'etcm', cell['source'])] if cell.get('source') else []
            row['cells'][field] = {'value': cell['value'], 'status': cell['state'], 'evidence_ids': refs}
        for field in goal.document_fields:
            cell = inspected['fields'].get(field, {'status': inspected['status'], 'value': None, 'evidence': []})
            refs = [add_evidence(sid, 'pharmacopoeia', item) for item in cell['evidence']]
            row['cells'][field] = {'value': cell['value'], 'status': cell['status'], 'evidence_ids': refs}
        if inspected['status'] != 'fields_consistent_not_verified':
            issues.append({'source_id': sid, 'field': '身份/药用部位', 'kind': inspected['status'],
                           'message': (inspected['comparison'] or {}).get('label', '未确认对应；保留候选和缺失，不自动选择。')})
        for field, cell in row['cells'].items():
            if cell['value'] is None:
                issues.append({'source_id': sid, 'field': field, 'kind': cell['status'], 'message': '本次来源未能补齐此字段。'})
            elif cell['status'] == 'candidate_scope_value':
                issues.append({'source_id': sid, 'field': field, 'kind': 'scope_requires_review',
                               'message': '原文位于饮片段，范围待核对；未将其视为原药材已确认属性。'})
        rows.append(row)
    for sid in selected['unknown_source_ids']:
        issues.append({'source_id': sid, 'field': '来源ID', 'kind': 'not_found', 'message': '目录没有这条来源，未静默忽略。'})
    for resolution in selected['name_resolution']:
        if resolution['status'] != 'name_candidate':
            issues.append({'source_id': resolution['name'], 'field': '对象名称', 'kind': resolution['status'],
                           'message': '名称没有对应来源。' if resolution['status'] == 'not_found' else '名称对应多个来源；分别保留，未合并。'})
    missing = sum(cell['value'] is None for row in rows for cell in row['cells'].values())
    # Audit completion allows explicitly reported data gaps, but an empty or unknown selection is partial.
    complete = bool(rows) and not selected['unknown_source_ids'] and not selected['unknown_names'] and (
        goal.completion_policy == 'audit' or missing == 0 and all(
            r['mapping_status'] == 'fields_consistent_not_verified' and
            all(c['status'] in ('present', 'candidate_value') for c in r['cells'].values()) for r in rows))
    result = {'mode': 'fixed_local_F', 'status': 'complete' if complete else 'partial',
              'goal': goal.model_dump(), 'source_revision': sources.revision, 'rows': rows, 'issues': issues,
              'evidence': evidence, 'coverage': {'selected': len(rows), 'matched_total': selected['total'],
                  'unknown_source_ids': selected['unknown_source_ids'], 'missing_cells': missing,
                  'unknown_names': selected['unknown_names'],
                  'mapping_states': dict(Counter(r['mapping_status'] for r in rows)),
                  'selection_is_limited': selected['next_offset'] is not None},
              'query': {k: v for k, v in selected.items() if k != 'records'},
              'trace': trace, 'model_calls': 0}
    validate_result(result)
    sources.assert_unchanged()
    return result


def validate_result(result):
    fields = result['goal']['attributes'] + result['goal']['document_fields']
    seen = set()
    for row in result['rows']:
        if row['source_id'] in seen or set(row['cells']) != set(fields):
            raise ValueError('Duplicate row or missing output column')
        seen.add(row['source_id'])
        for field, cell in row['cells'].items():
            if cell['value'] is not None and not cell['evidence_ids']:
                raise ValueError('Nonempty cell without provenance')
            if any(e not in result['evidence'] or result['evidence'][e]['source_id'] != row['source_id'] for e in cell['evidence_ids']):
                raise ValueError('Invalid or cross-record provenance')
            if cell['value'] is not None:
                witnesses = [result['evidence'][e] for e in cell['evidence_ids']]
                if field in result['goal']['document_fields']:
                    if (len(witnesses) != 1 or any(w['kind'] != 'pharmacopoeia' or w['source']['label'] != LABELS[field]
                            or w['source']['scope'] not in ('monograph', 'prepared_slices') for w in witnesses) or
                            '\n'.join(w['source']['text'] for w in witnesses) != cell['value']):
                        raise ValueError('Document field is not supported by its exact witnesses')
                    expected_status = 'candidate_scope_value' if witnesses[0]['source']['scope'] == 'prepared_slices' else 'candidate_value'
                    if cell['status'] != expected_status:
                        raise ValueError('Document scope was silently promoted')
                else:
                    from tcm.herbs import FIELDS, text_value
                    if len(witnesses) != 1 or witnesses[0]['kind'] != 'etcm':
                        raise ValueError('Invalid structured field evidence')
                    source = witnesses[0]['source']
                    raw = source['raw_value']
                    original = text_value(raw) if field in ('latin_name', 'english_name') else raw.strip()
                    if FIELDS.get(source['raw_field_key']) != field or original != cell['value']:
                        raise ValueError('Structured field differs from original source')
    return {'status': 'passed', 'records': len(seen)}


def execute(store, owner, key, sources, goal):
    goal = Goal.model_validate(goal)
    doc, created = store.reserve(owner, key, goal.model_dump(), sources.revision)
    if not created:
        return doc, False
    try:
        result = collect(sources, goal)
        artifact = publish(store.root / doc['id'], result)
        sources.assert_unchanged()
        return store.finish(owner, doc['id'], result=result, artifact=artifact), True
    except Exception:
        store.finish(owner, doc['id'], error='local_execution_failed')
        raise
