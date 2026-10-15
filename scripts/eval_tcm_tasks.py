"""Execute draft task API checks; keep manual goals and unsupported work separate."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from urllib.parse import quote
from unittest.mock import patch

from scripts.eval_tcm_golden import database_preflight
from scripts.validate_tcm_task_candidates import validate

ROOT = Path(__file__).resolve().parents[1]
MANUAL_KEYS = {'evidence_scope', 'scientific_conclusion_verified', 'contract',
               'requested_capability', 'requested_capability_supported'}
SOURCE_KEYS = {
    'name_pinyin': 'Herb Name in Pinyin', 'latin_name': 'Herb Name in Latin',
    'english_name': 'Herb Name in English', 'family': 'Family', 'habitat': 'Habitat',
    'medicinal_part': 'Medicinal Part', 'properties': 'Property', 'flavors': 'Flavor',
    'meridians': 'Meridian Tropism', 'efficacy_class': 'Herb classification based on efficacy',
    'indications': 'Indications', 'reference_pmids': 'PubMed',
    'cross_references': 'Database Cross References',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ApiChecks:
    def __init__(self, client, version):
        self.client, self.version = client, version
        self.calls = []

    def request(self, inputs):
        op = inputs['operation']
        params = {'dataset_version': inputs.get('dataset_version', self.version)}
        if op == 'lookup':
            if 'source_record_id' in inputs:
                path = '/herb-records/' + quote(inputs['source_record_id'], safe='')
            else:
                path = '/herbs/' + quote(inputs['name'], safe='')
        elif op == 'filter':
            path = '/herbs'
            params.update(inputs['filters'])
            params.update(limit=inputs.get('limit', 20), offset=inputs.get('offset', 0))
        elif op == 'compare':
            path = '/herbs/compare'
            params['names'] = ','.join(inputs['names'])
        else:
            raise ValueError('Unsupported automatic operation: ' + str(op))
        response = self.client.get(path, params=params)
        body = response.json()
        self.calls.append({'path': path, 'params': params, 'http_status': response.status_code})
        return response.status_code, body

    def all_ids(self, inputs):
        ids, offset, count = [], 0, None
        for _ in range(100):
            code, body = self.request({**inputs, 'limit': 100, 'offset': offset})
            if code != 200 or body.get('status') != 'ok':
                raise ValueError('Full-result pagination failed')
            if count is not None and body['count'] != count:
                raise ValueError('Result count changed during pagination')
            count = body['count']
            ids.extend(row['source_record_id'] for row in body['records'])
            next_offset = body['next_offset']
            if next_offset is None:
                if len(ids) != count or len(set(ids)) != len(ids):
                    raise ValueError('Incomplete or duplicated full-result pagination')
                return ids
            if next_offset <= offset:
                raise ValueError('Non-advancing pagination')
            offset = next_offset
        raise ValueError('Full-result pagination exceeds 100-page budget')


def check_case(case, api):
    started = time.perf_counter()
    row = {'id': case['id'], 'category': case['category'], 'split': case['split'],
           'capability_status': case['capability_status'], 'execution_mode': case['execution_mode'],
           'human_review': 'pending', 'task_status': 'not_assessed', 'checks': [],
           'unverified_expected_keys': [], 'requests': [], 'model_calls': 0,
           'model_api_cost': 0, 'total_system_cost': None}
    if case['execution_mode'] != 'api':
        row.update(automated_status='not_run', reason='manual_required' if case['execution_mode'] == 'manual_ui' else 'unsupported',
                   task_status='manual_required' if case['execution_mode'] == 'manual_ui' else 'unsupported')
        row['unverified_expected_keys'] = sorted(case['expected'])
        return row
    call_start = len(api.calls)

    def equal(key, expected, actual):
        row['checks'].append({'key': key, 'expected': expected, 'actual': actual, 'passed': expected == actual})

    anchors = {(e['relative_path'], e['json_pointer']): e for e in case['source_evidence']}

    def field_anchor(source):
        path, pointer = source.get('relative_file_path'), source.get('json_pointer')
        exact = anchors.get((path, pointer))
        if exact:
            return exact
        # Missing-field tasks anchor the whole basic section. Resolve present
        # identity fields from that immutable value rather than inventing an anchor.
        for (filename, prefix), anchor in anchors.items():
            if filename != path or not isinstance(pointer, str) or not pointer.startswith(prefix + '/'):
                continue
            value = anchor['raw_value']
            try:
                for token in pointer[len(prefix)+1:].split('/'):
                    token = token.replace('~1', '/').replace('~0', '~')
                    value = value[int(token)] if isinstance(value, list) else value[token]
                return {**anchor, 'json_pointer': pointer, 'raw_value': value}
            except (ValueError, KeyError, IndexError, TypeError):
                continue
        return None

    def fields(record, wanted, label):
        selected = {}
        for name in wanted:
            actual = record['fields'][name]
            selected[name] = {key: actual.get(key) for key in wanted[name]}
            if actual.get('state') == 'present':
                source = actual.get('source') or {}
                anchor = field_anchor(source)
                equal(label + '.' + name + '.source_anchor_present', True, anchor is not None)
                if anchor:
                    equal(label + '.' + name + '.source_field_key', SOURCE_KEYS.get(name), source.get('raw_field_key'))
                    equal(label + '.' + name + '.source_hash', anchor['sha256'], source.get('file_sha256'))
                    equal(label + '.' + name + '.raw_value', anchor['raw_value'], source.get('raw_value'))
        return selected

    def projection(body, expected, inputs):
        records = body.get('records', [])
        record = records[0] if records else {}
        source = record.get('source', {})
        result = {key: body[key] for key in ('status', 'count', 'returned_count', 'limit', 'offset', 'next_offset', 'error') if key in body}
        result.update(records=records, record_count=len(records),
                      source_record_id=record.get('source_record_id'),
                      relative_file_path=source.get('relative_file_path'), file_sha256=source.get('file_sha256'),
                      page_source_record_ids=[r['source_record_id'] for r in records],
                      page_relative_paths=[r['source']['relative_file_path'] for r in records],
                      candidate_source_record_ids=[r['source_record_id'] for r in records],
                      candidate_paths=[r['source']['relative_file_path'] for r in records])
        for label in ('fields', 'present_identity'):
            if label in expected:
                result[label] = fields(record, expected[label], label)
        if 'display_values_equal' in expected:
            values = [record['fields'][name]['value'] for name in expected['fields']]
            result['display_values_equal'] = bool(values) and all(v == values[0] for v in values)
        for key in ('field_source', 'missing_field_source'):
            if key in expected:
                sources = [record['fields'][name].get('source') for name in expected['fields']]
                result[key] = None if all(s is None for s in sources) else sources
        if 'herbs' in expected:
            result['herbs'] = []
            for index, actual in enumerate(body['herbs']):
                wanted = expected['herbs'][index] if index < len(expected['herbs']) else {}
                item = {key: actual.get(key) for key in ('query', 'status')}
                if 'source_record_id' in wanted:
                    item['source_record_id'] = actual.get('record', {}).get('source_record_id')
                if 'fields' in wanted:
                    item['fields'] = fields(actual['record'], wanted['fields'], f'herbs.{index}.fields')
                result['herbs'].append(item)
        if 'all_matching_source_record_ids' in expected:
            result['all_matching_source_record_ids'] = api.all_ids(inputs)
        return result

    try:
        expected = case['expected']
        inputs = case['input']
        if inputs['operation'] == 'sequence':
            results = [api.request({'dataset_version': inputs['dataset_version'], **step}) for step in inputs['steps']]
            equal('step_http_statuses', [200] * len(results), [r[0] for r in results])
            equal('step_statuses', expected['step_statuses'], [r[1]['status'] for r in results])
            body = results[-1][1]
            wanted = {'source_record_id': expected['selected_source_record_id'],
                      'relative_file_path': expected['selected_relative_path'], 'fields': expected['fields']}
            actual = projection(body, wanted, inputs['steps'][-1])
            for key, value in wanted.items():
                equal(key, value, actual[key])
            known = {'step_statuses', 'selected_source_record_id', 'selected_relative_path', 'fields'}
            row['unverified_expected_keys'] = sorted(set(expected) - known)
        else:
            status, body = api.request(inputs)
            equal('http_status', expected.get('http_status', 200), status)
            wanted = expected.get('api_contract', expected)
            actual = projection(body, wanted, inputs)
            for key, value in wanted.items():
                if key in MANUAL_KEYS:
                    row['unverified_expected_keys'].append(key)
                elif key == 'http_status':
                    continue
                elif key == 'detail_location':
                    locations = [x.get('loc') for x in body.get('detail', [])]
                    equal(key, True, value in locations)
                elif key == 'required_warning':
                    equal(key, True, value in body.get('warnings', []))
                elif key in actual:
                    equal(key, value, actual[key])
                else:
                    row['unverified_expected_keys'].append(key)
            if 'api_contract' in expected:
                row['unverified_expected_keys'].extend(sorted(set(expected) - {'api_contract'}))
                row['degradation_contract'] = 'failed' if any(not c['passed'] for c in row['checks']) else 'passed'
        if not row['checks']:
            raise ValueError('No automatic assertions were executed')
        row['automated_status'] = ('failed' if any(not c['passed'] for c in row['checks']) else
                                   'partial' if row['unverified_expected_keys'] else 'passed')
    except Exception as exc:
        row.update(automated_status='failed', error=f'{type(exc).__name__}: {exc}')
    row['requests'] = api.calls[call_start:]
    row['latency_seconds'] = round(time.perf_counter() - started, 6)
    row['unverified_expected_keys'] = sorted(set(row['unverified_expected_keys']))
    row['task_status'] = 'unsupported' if case['capability_status'] == 'unsupported' else 'pending_human_review'
    # API assertions never establish UI interaction or scientific interpretation.
    row['manual_rubric'] = ['question_goal_and_answer_completeness', 'UI_source_visibility_and_interpretation', 'scientific_scope_and_no_unsupported_inference']
    return row


def evaluate(suite, database, client, source=None):
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'dataset_version': suite['dataset_version'],
              'review_status': suite['review_status'], 'human_signoff': False,
              'cases': [], 'task_completion_rate': None, 'task_outcomes': 'pending_human_review',
              'limitations': 'Draft expected answers; API checks do not prove human task completion, UI behavior, scientific validity or production capacity.'}
    try:
        validation = validate(suite, source)
        if validation['errors']:
            raise ValueError('; '.join(validation['errors']))
        report['candidate_validation'] = validation
        database_preflight(database, {'dataset_version': suite['dataset_version'], 'snapshot': suite['source_snapshot']})
        before = digest(database)
        ready = client.get('/ready')
        if ready.status_code != 200 or ready.json() != {'status': 'ready', 'dataset_version': suite['dataset_version'], 'record_count': suite['source_snapshot']['record_count']}:
            raise ValueError('API readiness/version/count mismatch')
    except Exception as exc:
        report.update(status='blocked', error=f'{type(exc).__name__}: {exc}',
                      summary={'total': len(suite['cases']), 'automated_executed': 0, 'not_run': len(suite['cases'])})
        return report
    api = ApiChecks(client, suite['dataset_version'])
    report['cases'] = [check_case(case, api) for case in suite['cases']]
    summary = {key: sum(c['automated_status'] == status for c in report['cases'])
               for key, status in (('automated_passed', 'passed'), ('automated_partial', 'partial'),
                                   ('automated_failed', 'failed'), ('not_run', 'not_run'))}
    summary.update(total=len(report['cases']), automated_executed=summary['automated_passed'] + summary['automated_partial'] + summary['automated_failed'],
                   manual_required=sum(c.get('reason') == 'manual_required' for c in report['cases']),
                   unsupported_not_run=sum(c.get('reason') == 'unsupported' for c in report['cases']),
                   degradation_contract_passed=sum(c.get('degradation_contract') == 'passed' for c in report['cases']),
                   actual_api_requests=len(api.calls), task_completion_rate=None)
    times = [c['latency_seconds'] for c in report['cases'] if 'latency_seconds' in c]
    report.update(summary=summary, status='failed' if summary['automated_failed'] else 'completed',
                  sqlite_unchanged=digest(database) == before,
                  api_check_latency={'sample_count': len(times), 'mean_seconds': statistics.mean(times) if times else None,
                                     'p50_seconds': statistics.median(times) if times else None,
                                     'p95_seconds': sorted(times)[min(len(times)-1, int(.95*len(times)))] if times else None,
                                     'scope': 'One sequential run of mixed API checks including pagination; not user latency or capacity.'})
    if not report['sqlite_unchanged']:
        report.update(status='blocked', error='Database changed during execution; results invalidated')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'eval/tcm_task_candidates_v1.json')
    parser.add_argument('--db', type=Path, default=ROOT / 'var/tcm/herbs.sqlite3')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--base-url', help='Existing TCM HTTP API; default uses actual FastAPI routes via ASGI TestClient')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/task_candidates/execution.json')
    args = parser.parse_args()
    if args.out.resolve() in (args.suite.resolve(), args.db.resolve()) or (args.source and args.out.resolve().is_relative_to(args.source.resolve())):
        parser.error('output must not overwrite inputs or enter source directory')
    raw = args.suite.read_bytes()
    suite = json.loads(raw)
    with ExitStack() as stack:
        if args.base_url:
            import httpx
            client = stack.enter_context(httpx.Client(base_url=args.base_url, timeout=15))
        else:
            from fastapi.testclient import TestClient
            from tcm import api
            stack.enter_context(patch.multiple(api, DB_PATH=str(args.db.resolve()), DATASET_VERSION=suite['dataset_version']))
            client = stack.enter_context(TestClient(api.app))
        report = evaluate(suite, args.db, client, args.source)
    report.update(suite_sha256=hashlib.sha256(raw).hexdigest(),
                  transport='http' if args.base_url else 'asgi_in_process',
                  code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  code_sha256={name: digest(ROOT / name) for name in ('scripts/eval_tcm_tasks.py', 'tcm/api.py', 'tcm/herbs.py')},
                  working_tree_changes=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()))
    if args.suite.read_bytes() != raw:
        report.update(status='blocked', error='Candidate suite changed during execution')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'summary': report['summary'], 'task_completion_rate': None}))
    return 2 if report['status'] == 'blocked' else 1 if report['status'] == 'failed' else 0


if __name__ == '__main__':
    sys.exit(main())
