"""Explicitly create a source-derived candidate suite; never overwrite a baseline.

Independent stdlib oracle: no imports of tcm, database, models or API responses.
The frozen output requires human sign-off; this script does not provide it.
"""
import argparse
import hashlib
import html
import json
import re
from pathlib import Path

# Source mapping is deliberately explicit, independent of the production loader.
KEYS = {
    'name_pinyin': 'Herb Name in Pinyin', 'latin_name': 'Herb Name in Latin',
    'english_name': 'Herb Name in English', 'family': 'Family', 'habitat': 'Habitat',
    'medicinal_part': 'Medicinal Part', 'properties': 'Property', 'flavors': 'Flavor',
    'meridians': 'Meridian Tropism', 'efficacy_class': 'Herb classification based on efficacy',
    'indications': 'Indications', 'reference_pmids': 'PubMed',
    'cross_references': 'Database Cross References',
}


def fingerprint(entries):
    encoded = json.dumps(sorted(entries), ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def key(value):
    return ' '.join(value.split()).casefold()


def read_sources(root):
    records = {}
    for path in sorted((root / 'etcm_herbs').glob('*.json')):
        raw = path.read_bytes()
        obj = json.loads(raw)
        assert obj['code'] == 1, path
        section_index, section = next((i, s) for i, s in enumerate(obj['data']) if s.get('id') == 'base_information')
        items = {item['key']: (i, item) for i, item in enumerate(section['value'])}
        assert len(items) == len(section['value']), path
        fields = {}
        for target, source_key in KEYS.items():
            found = items.get(source_key)
            value = found[1].get('value') if found else None
            state = 'missing' if value is None or value == '' or value == [] else 'present'
            if state == 'present':
                if isinstance(value, str):
                    value = html.unescape(re.sub(r'<[^>]*>', '', value)).strip() if target in ('latin_name', 'english_name') else value.strip()
                elif isinstance(value, list):
                    value = list(dict.fromkeys(s.strip() for s in value if s.strip()))
                if not value:
                    state = 'missing'
            fields[target] = {'state': state, 'value': value if state == 'present' else None,
                              'json_pointer': f'/data/{section_index}/value/{found[0]}/value' if found else None,
                              'raw_field_key': source_key if found else None,
                              'raw_value': found[1]['value'] if found else None}
        relative = path.relative_to(root).as_posix()
        records[relative] = {'sha256': hashlib.sha256(raw).hexdigest(), 'fields': fields}
    if not records:
        raise ValueError('No source herbs found')
    return records


def build(records, version):
    name_index = {}
    for path, record in records.items():
        for field in ('name_pinyin', 'latin_name', 'english_name'):
            value = record['fields'][field]['value']
            if value:
                name_index.setdefault(key(value), set()).add(path)
    cases = []
    selected = set()

    def paths_for(name):
        return sorted(name_index.get(key(name), []))

    def add_lookup(case_id, name=None, path=None, status=None, dataset_version=None):
        paths = [path] if path else paths_for(name)
        status = status or ('ok' if len(paths) == 1 else 'ambiguous_entity' if paths else 'not_found')
        if dataset_version and dataset_version != version:
            status, paths = 'dataset_not_found', []
        args = {'name': name} if path is None else {'source_record_id': 'etcm2-local:' + hashlib.sha256(path.encode()).hexdigest()}
        if dataset_version:
            args['dataset_version'] = dataset_version
        cases.append({'id': case_id, 'operation': 'lookup', 'input': args,
                      'expected': {'status': status, 'paths': paths}})
        selected.update(paths)

    add_lookup('01-detail-huangqin', 'HuangQin')
    add_lookup('02-name-case-and-space', '  HUANGQIN  ')
    add_lookup('03-latin-name-html-normalized', 'Scutellaria baicalensis Georgi')
    add_lookup('04-detail-gancao', 'GanCao')
    add_lookup('05-detail-danggui', 'DangGui')
    add_lookup('06-detail-renshen', 'RenShen')
    add_lookup('07-sparse-basic-fields', 'HuaYeEZhangChai')
    add_lookup('08-ambiguous-huashi', 'HuaShi')
    add_lookup('09-specific-huashi-source', path='etcm_herbs/HuaShi .json')
    add_lookup('10-chinese-name-unmapped', '黄芩', status='unsupported_name_mapping')
    add_lookup('11-unknown-name', '__golden_missing_herb_20261012__')
    add_lookup('12-unknown-version', 'HuangQin', dataset_version='__golden_absent_version__')
    add_lookup('13-missing-pubmed-field', 'BaiShao')

    def add_filter(case_id, filters, limit=20, offset=0):
        matches = sorted(path for path, record in records.items() if all(
            key(needle) in key(record['fields'][field]['value'] or '') for field, needle in filters.items()))
        paths = matches[offset:offset + limit]
        cases.append({'id': case_id, 'operation': 'filter',
                      'input': {'filters': filters, 'limit': limit, 'offset': offset},
                      'expected': {'status': 'ok', 'count': len(matches), 'returned_count': len(paths),
                                   'limit': limit, 'offset': offset,
                                   'next_offset': offset + limit if offset + limit < len(matches) else None,
                                   'paths': paths}})
        selected.update(paths)

    add_filter('14-family-first-page', {'family': 'Leguminosae'})
    add_filter('15-family-second-page', {'family': 'Leguminosae'}, offset=20)
    add_filter('16-family-last-page', {'family': 'Leguminosae'}, offset=120)
    add_filter('17-page-beyond-total', {'family': 'Leguminosae'}, offset=127)
    add_filter('18-family-and-flavor', {'family': 'Leguminosae', 'flavors': 'bitter'})
    add_filter('19-property-and-flavor', {'properties': 'Cold', 'flavors': 'Bitter'}, limit=5)
    add_filter('20-no-filter-results', {'family': '__golden_absent_family__'})
    for case_id, args, message, http_status in [
        ('21-empty-filters', {'filters': {}}, 'filters must be a non-empty dict', 400),
        ('22-unsupported-filter', {'filters': {'name_pinyin': 'HuangQin'}}, 'unsupported_filter_field:', 400),
        ('23-negative-offset', {'filters': {'family': 'Leguminosae'}, 'offset': -1}, 'offset must be', 422),
        ('24-excessive-limit', {'filters': {'family': 'Leguminosae'}, 'limit': 101}, 'limit must be', 422),
    ]:
        cases.append({'id': case_id, 'operation': 'filter', 'input': args,
                      'expected': {'exception': 'ValueError', 'message_contains': message, 'http_status': http_status}})

    def add_compare(case_id, names, dataset_version=None):
        entries = []
        for name in names:
            paths = paths_for(name)
            entries.append({'query': name, 'status': 'ok' if len(paths) == 1 else 'ambiguous_entity' if paths else 'not_found', 'paths': paths})
            selected.update(paths)
        args = {'names': names}
        expected = {'status': 'ok', 'herbs': entries}
        if dataset_version:
            args['dataset_version'] = dataset_version
            expected = {'status': 'dataset_not_found', 'herbs': []}
        cases.append({'id': case_id, 'operation': 'compare', 'input': args, 'expected': expected})

    add_compare('25-compare-traditional-fields', ['HuangQin', 'GanCao', 'DangGui'])
    add_compare('26-compare-ambiguity-and-missing', ['HuangQin', 'HuaShi', '__golden_missing_herb_20261012__'])
    add_compare('27-compare-sparse-and-missing-reference', ['HuaYeEZhangChai', 'BaiShao'])
    add_compare('28-compare-unknown-version', ['HuangQin'], '__golden_absent_version__')
    cases.append({'id': '29-empty-compare', 'operation': 'compare', 'input': {'names': []},
                  'expected': {'exception': 'ValueError', 'message_contains': 'names must be a non-empty list', 'http_status': 400}})
    cases.append({'id': '30-dataset-readiness', 'operation': 'ready', 'input': {},
                  'expected': {'status': 'ready', 'record_count': len(records)}})
    assert len(cases) == 30
    # Explicitly inspected source facts: fail generation if the reference snapshot changes.
    assert records['etcm_herbs/HuangQin.json']['fields']['properties']['value'] == 'Cold'
    assert records['etcm_herbs/GanCao.json']['fields']['flavors']['value'] == 'Sweet'
    assert records['etcm_herbs/DangGui.json']['fields']['family']['value'] == 'Apiaceae'
    assert records['etcm_herbs/BaiShao.json']['fields']['reference_pmids']['state'] == 'missing'
    assert records['etcm_herbs/HuaYeEZhangChai.json']['fields']['properties']['state'] == 'missing'
    assert paths_for('HuaShi') == ['etcm_herbs/HuaShi .json', 'etcm_herbs/HuaShi_.json']
    assert cases[13]['expected']['count'] == 127
    return {'suite_version': 1, 'dataset_version': version,
            'review': {'status': 'source_reconciled_pending_human_signoff',
                       'basis': 'Expected outputs frozen from raw ETCM JSON via independent stdlib oracle; representative fields inspected directly by coding agent.',
                       'human_reviewer': None,
                       'limitations': 'Engineering correctness only; not clinical validation. No model-generated ground truth.'},
            'snapshot': {'record_count': len(records), 'fingerprint_algorithm': 'sha256 of sorted [relative_path, sha256] pairs serialized as UTF-8 JSON with ensure_ascii=False and compact separators',
                         'sha256': fingerprint([[p, r['sha256']] for p, r in records.items()])},
            'records': {p: records[p] for p in sorted(selected)}, 'cases': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--version', default='local-2026-10-10')
    args = parser.parse_args()
    root = args.source.resolve()
    if args.out.resolve().is_relative_to(root):
        parser.error('Output must be outside the read-only source root')
    if args.out.exists():
        parser.error('Refusing to overwrite frozen suite; choose a new version/path')
    suite = build(read_sources(root), args.version)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(suite, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'cases': len(suite['cases']), 'record_expectations': len(suite['records']),
                      'review_status': suite['review']['status']}))


if __name__ == '__main__':
    main()
