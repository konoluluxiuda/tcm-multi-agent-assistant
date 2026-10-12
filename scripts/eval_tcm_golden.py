"""Evaluate frozen source-grounded TCM cases against repository or HTTP API.

No regeneration, source writes, ingestion, model calls or paid APIs.
Exit 0: pass; 1: case failure; 2: suite/snapshot precondition failed.
"""
import argparse
import hashlib
import json
import sqlite3
import time
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tcm.herbs import compare_herbs, dataset_status, filter_herbs, lookup


class CaseMismatch(AssertionError):
    pass


def fingerprint(entries):
    return hashlib.sha256(json.dumps(sorted(entries), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def equal(label, actual, expected):
    if actual != expected:
        raise CaseMismatch(f'{label}: expected {expected!r}, got {actual!r}')


def source_preflight(root, suite):
    paths = sorted((root / 'etcm_herbs').glob('*.json'))
    equal('source file count', len(paths), suite['snapshot']['record_count'])
    entries = [[p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()] for p in paths]
    equal('source snapshot fingerprint', fingerprint(entries), suite['snapshot']['sha256'])
    for path, record in suite['records'].items():
        obj = json.loads((root / path).read_bytes())
        for field in record['fields'].values():
            if field['json_pointer'] is None:
                continue
            value = obj
            for part in field['json_pointer'].lstrip('/').split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                value = value[int(part)] if isinstance(value, list) else value[part]
            equal('frozen raw value at ' + path + field['json_pointer'], value, field['raw_value'])


def database_preflight(database, suite):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('BEGIN')
        equal('database schema', con.execute('PRAGMA user_version').fetchone()[0], 1)
        rows = con.execute('SELECT relative_path,sha256,state FROM records WHERE dataset_version=?', (suite['dataset_version'],)).fetchall()
        equal('database record count', len(rows), suite['snapshot']['record_count'])
        equal('valid record count', sum(r[2] == 'valid' for r in rows), suite['snapshot']['record_count'])
        equal('database snapshot fingerprint', fingerprint([[r[0], r[1]] for r in rows]), suite['snapshot']['sha256'])
        equal('SQLite integrity', con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


def record_paths(records):
    return [r['source']['relative_file_path'] for r in records]


def verify_record(record, suite):
    path = record['source']['relative_file_path']
    expected = suite['records'][path]
    equal(path + ' SHA-256', record['source']['file_sha256'], expected['sha256'])
    equal(path + ' dataset version', record['source']['dataset_version'], suite['dataset_version'])
    equal(path + ' source ID', record['source_record_id'], 'etcm2-local:' + hashlib.sha256(path.encode()).hexdigest())
    equal(path + ' source review status', record['source']['review_status'], 'unverified')
    for warning in ('source_content_unverified', 'chinese_names_unmapped'):
        if warning not in record.get('warnings', []):
            raise CaseMismatch(path + ' missing warning: ' + warning)
    for name, frozen in expected['fields'].items():
        actual = record['fields'][name]
        equal(path + ':' + name + ':state', actual['state'], frozen['state'])
        equal(path + ':' + name + ':value', actual['value'], frozen['value'])
        if frozen['json_pointer'] is None:
            equal(path + ':' + name + ':source', actual['source'], None)
        else:
            source = actual['source']
            for k in ('json_pointer', 'raw_field_key', 'raw_value'):
                equal(path + ':' + name + ':' + k, source[k], frozen[k])
            for k, value in [('relative_file_path', path), ('file_sha256', expected['sha256']),
                             ('dataset_version', suite['dataset_version']), ('review_status', 'unverified'),
                             ('section_id', 'base_information'), ('normalization_rule_version', 1)]:
                equal(path + ':' + name + ':' + k, source[k], value)
    if 'name_zh' in record:
        equal(path + ':name_zh', record['name_zh'], None)
        equal(path + ':herb_id', record['herb_id'], None)
        equal(path + ':aliases', record['aliases'], None)


def verify_result(case, result, suite):
    expected = case['expected']
    equal('status', result['status'], expected['status'])
    equal('version', result['dataset_version'], case['input'].get('dataset_version', suite['dataset_version']))
    operation = case['operation']
    records = []
    if operation in ('lookup', 'filter'):
        records = result['records']
        equal('record paths and order', record_paths(records), expected['paths'])
        if operation == 'filter' and expected['status'] == 'ok':
            for name in ('count', 'returned_count', 'limit', 'offset', 'next_offset'):
                equal(name, result[name], expected[name])
    elif operation == 'compare':
        equal('compare entries', len(result['herbs']), len(expected['herbs']))
        for actual, frozen in zip(result['herbs'], expected['herbs']):
            equal('compare query', actual['query'], frozen['query'])
            equal('compare status', actual['status'], frozen['status'])
            resolved = [actual['record']] if 'record' in actual else actual.get('records', [])
            equal('compare record paths', record_paths(resolved), frozen['paths'])
            records.extend(resolved)
            if 'source_content_unverified' not in actual.get('warnings', []):
                raise CaseMismatch('Comparison entry lost source warnings')
    else:
        equal('ready record_count', result['record_count'], expected['record_count'])
    for record in records:
        verify_record(record, suite)
    return len(records)


def call_repository(case, database, version):
    functions = {'lookup': lookup, 'filter': filter_herbs, 'compare': compare_herbs, 'ready': dataset_status}
    args = {'dataset_version': version, **case['input']}
    return functions[case['operation']](database, **args)


def call_api(case, base_url, version):
    args = dict(case['input'])
    params = {'dataset_version': args.pop('dataset_version', version)}
    operation = case['operation']
    if operation == 'lookup':
        if 'name' in args:
            path = '/herbs/' + urllib.parse.quote(args['name'], safe='')
        else:
            path = '/herb-records/' + urllib.parse.quote(args['source_record_id'], safe='')
    elif operation == 'filter':
        path = '/herbs'
        params.update(args.pop('filters'))
        params.update(args)
    elif operation == 'compare':
        path = '/herbs/compare'
        params['names'] = ','.join(args['names'])
    else:
        # /ready uses the service's configured version; response version is checked.
        path, params = '/ready', {}
    url = base_url.rstrip('/') + path + '?' + urllib.parse.urlencode(params)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as response:
        return response.code, json.load(response)


def evaluate(suite, *, database, base_url=None, source=None):
    report = {'suite_version': suite['suite_version'], 'dataset_version': suite['dataset_version'],
              'review_status': suite['review']['status'], 'human_signoff': bool(suite['review'].get('human_reviewer')),
              'backend': 'http' if base_url else 'repository',
              'created_at': datetime.now(timezone.utc).isoformat(), 'preconditions': [], 'cases': []}
    started = time.perf_counter()
    try:
        equal('unique case IDs', len({c['id'] for c in suite['cases']}), len(suite['cases']))
        if source is not None:
            source_preflight(source, suite)
            report['preconditions'].append({'check': 'raw source hashes and frozen JSON Pointers', 'passed': True})
        database_preflight(database, suite)
        report['preconditions'].append({'check': 'database snapshot, valid records and integrity', 'passed': True})
    except (AssertionError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        report.update(status='blocked', passed=False, error=str(exc),
                      summary={'total':len(suite['cases']), 'passed':0, 'failed':0, 'not_run':len(suite['cases'])},
                      duration_seconds=round(time.perf_counter() - started, 3))
        return report
    for case in suite['cases']:
        case_start = time.perf_counter()
        item = {'id': case['id'], 'operation': case['operation'], 'passed': False}
        expected = case['expected']
        try:
            if base_url:
                http_status, result = call_api(case, base_url, suite['dataset_version'])
                equal('HTTP status', http_status, expected.get('http_status', 200))
                if 'exception' not in expected:
                    item['verified_records'] = verify_result(case, result, suite)
                elif http_status == 400:
                    equal('validation status', result.get('status'), 'error')
                    # Repository exception text and HTTP validation wording differ.
                    if 'http_message_contains' in expected and expected['http_message_contains'] not in result.get('error', ''):
                        raise CaseMismatch('Unexpected HTTP validation message')
                elif not isinstance(result.get('detail'), list):
                    raise CaseMismatch('Missing HTTP validation details')
            else:
                try:
                    result = call_repository(case, database, suite['dataset_version'])
                except ValueError as exc:
                    equal('exception type', type(exc).__name__, expected.get('exception'))
                    if expected.get('message_contains', '') not in str(exc):
                        raise CaseMismatch('Unexpected validation message: ' + str(exc))
                else:
                    if 'exception' in expected:
                        raise CaseMismatch('Expected validation exception was not raised')
                    item['verified_records'] = verify_result(case, result, suite)
            item['passed'] = True
        except (AssertionError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            item['error'] = str(exc)[:1200]
        item['duration_seconds'] = round(time.perf_counter() - case_start, 4)
        report['cases'].append(item)
    passed = sum(c['passed'] for c in report['cases'])
    report.update(status='passed' if passed == len(suite['cases']) else 'failed', passed=passed == len(suite['cases']),
                  summary={'total':len(suite['cases']), 'passed':passed, 'failed':len(suite['cases'])-passed, 'not_run':0},
                  duration_seconds=round(time.perf_counter() - started, 3))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=Path('eval/tcm_golden_v1.json'))
    parser.add_argument('--db', type=Path, default=Path('var/tcm/herbs.sqlite3'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--base-url', help='Optional live HTTP service; local --db remains required for snapshot checks')
    parser.add_argument('--out', type=Path, default=Path('audits/tcm_golden/report_repository.json'))
    args = parser.parse_args()
    protected = [args.suite.resolve(), args.db.resolve()]
    out = args.out.resolve()
    if out in protected or (args.source and out.is_relative_to(args.source.resolve())):
        parser.error('Output must not overwrite the frozen suite, database or source data')
    try:
        raw = args.suite.read_bytes()
        suite = json.loads(raw)
        if suite.get('suite_version') != 1 or not isinstance(suite.get('cases'), list) or not suite['cases']:
            raise ValueError('Invalid or empty frozen suite')
        report = evaluate(suite, database=args.db, base_url=args.base_url, source=args.source.resolve() if args.source else None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'status': 'invalid_suite', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    report['suite_sha256'] = hashlib.sha256(raw).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k:report[k] for k in ('status','summary','review_status','duration_seconds')}, ensure_ascii=False))
    return 0 if report['passed'] else 2 if report['status'] == 'blocked' else 1


if __name__ == '__main__':
    raise SystemExit(main())
