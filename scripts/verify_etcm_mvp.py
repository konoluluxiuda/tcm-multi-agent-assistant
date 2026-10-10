"""Verify the imported local ETCM snapshot against audit hashes and source fields.

Run from repo root: python -m scripts.verify_etcm_mvp --source /path/to/ETCM2.0
No clinical-content correctness claims are made by these checks.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from tcm.herbs import lookup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=Path('var/tcm/herbs.sqlite3'))
    parser.add_argument('--manifest', type=Path, default=Path('audits/etcm2/scanned_sha256.json'))
    parser.add_argument('--out', type=Path, default=Path('audits/etcm2_mvp/query_checks.json'))
    parser.add_argument('--version', default='local-2026-10-10')
    args = parser.parse_args()
    checks = []
    def check(label, value):
        checks.append({'check': label, 'passed': bool(value)})

    cases = [('HuangQin', 'ok', 1), (' huangqin ', 'ok', 1),
             ('Scutellaria baicalensis Georgi', 'ok', 1), ('HuaShi', 'ambiguous_entity', 2),
             ('HuaYeEZhangChai', 'ok', 1), ('黄芩', 'unsupported_name_mapping', 0),
             ('__nonexistent_herb__', 'not_found', 0)]
    for name, expected, count in cases:
        result = lookup(args.db, name=name, dataset_version=args.version)
        check('lookup ' + name, result['status'] == expected and len(result['records']) == count)
    doc = lookup(args.db, name='HuangQin', dataset_version=args.version)['records'][0]
    check('lookup source ID', lookup(args.db, source_record_id=doc['source_record_id'], dataset_version=args.version)['status'] == 'ok')
    raw = (args.source / doc['source']['relative_file_path']).read_bytes()
    source = json.loads(raw)
    check('HuangQin current source SHA-256', hashlib.sha256(raw).hexdigest() == doc['source']['file_sha256'])
    for field in doc['fields'].values():
        if field['source'] is None:
            continue
        value = source
        for part in field['source']['json_pointer'].lstrip('/').split('/'):
            value = value[int(part)] if isinstance(value, list) else value[part]
        check('JSON Pointer ' + field['source']['raw_field_key'], value == field['source']['raw_value'])
    sparse = lookup(args.db, name='HuaYeEZhangChai', dataset_version=args.version)['records'][0]
    check('sparse record has explicit missing fields', sparse['fields']['properties']['state'] == 'missing')
    manifest = {x['path']: x['sha256'] for x in json.loads(args.manifest.read_text())}
    con = sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = con.execute('SELECT relative_path,sha256,state FROM records WHERE dataset_version=?', (args.version,)).fetchall()
        check('2076 valid source records', len(rows) == 2076 and all(r[2] == 'valid' for r in rows))
        check('all imported hashes match prior audit', all(manifest.get(path) == sha for path, sha, _ in rows))
        check('SQLite integrity', con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    finally:
        con.close()
    report = {'dataset_version': args.version, 'checks': checks, 'passed': all(x['passed'] for x in checks),
              'database_bytes': args.db.stat().st_size}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'passed': report['passed'], 'checks': len(checks), 'database_bytes': report['database_bytes']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
