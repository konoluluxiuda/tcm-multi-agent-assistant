"""Bounded local ingredient witnesses. Filename matches never establish chemical identity."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from tcm.etcm_enrichment import connect

MAX_BYTES = 32 * 1024 * 1024
FIELDS = {'Component Name IN English': 'name', 'Molecular Formula': 'formula',
          'Molecular Weight': 'weight', 'CAS Number': 'cas', 'PubChem': 'pubchem', '2D Structure': 'structure'}


def text_key(value):
    return unicodedata.normalize('NFKC', value).strip().casefold() if isinstance(value, str) else None


def read_source(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Source file is outside the configured snapshot or missing')
    with path.open('rb') as handle:
        content = handle.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError('Source exceeds the bounded detail reader')
    return json.loads(content), hashlib.sha256(content).hexdigest()


def basic_fields(document):
    fields = {}
    def walk(node, pointer):
        for i, value in enumerate(node.get('value', []) if isinstance(node.get('value'), list) else []):
            if isinstance(value, dict) and value.get('key') in FIELDS:
                key = FIELDS[value['key']]
                fields.setdefault(key, []).append({'value': value.get('value'), 'json_pointer': pointer + f'/value/{i}/value'})
        for i, child in enumerate(node.get('children', [])):
            if isinstance(child, dict):
                walk(child, pointer + f'/children/{i}')
    for i, section in enumerate(document.get('data', [])):
        if isinstance(section, dict) and section.get('id') == 'base_information':
            walk(section, f'/data/{i}')
    return fields


def single(fields, key):
    values = fields.get(key, [])
    return values[0]['value'] if len(values) == 1 else None


def compare_fields(row, fields):
    names = row.get('Ingredient Name')
    expected_name = names[0] if isinstance(names, list) and len(names) == 1 else None
    name = single(fields, 'name')
    formula = single(fields, 'formula')
    weight = single(fields, 'weight')
    statuses = {}
    for field, left, right in [('name', expected_name, name), ('formula', row.get('Molecular Formula'), formula)]:
        statuses[field] = ('unknown' if not isinstance(left, str) or not left.strip() or not isinstance(right, str) or not right.strip()
                           else 'same' if (text_key(left) == text_key(right) if field == 'name' else left.strip() == right.strip())
                           else 'different')
    try:
        left, right = Decimal(str(row.get('Molecular Weight'))), Decimal(str(weight))
        statuses['weight'] = ('unknown' if not left.is_finite() or not right.is_finite() else
                              'same' if left == right else 'different')
    except InvalidOperation:
        statuses['weight'] = 'unknown'
    status = ('source_fields_inconsistent' if 'different' in statuses.values() else
              'source_fields_consistent_not_verified' if set(statuses.values()) == {'same'} else 'insufficient_identity_fields')
    return status, statuses


def inspect_candidate(root, relative, row):
    document, sha = read_source(root, relative)
    if document.get('code') != 1:
        raise ValueError('Ingredient source is not a successful record')
    fields = basic_fields(document)
    status, checks = compare_fields(row, fields)
    result = {'relative_path': relative, 'file_sha256': sha, 'status': status,
              'field_checks': checks, 'identity_verified': False,
              'comparison_fields': {k: fields.get(k, []) for k in ('name', 'formula', 'weight')},
              'scope': 'source_declared_fields_not_structure_or_stereochemistry_verification'}
    if status != 'source_fields_consistent_not_verified':
        return result
    # Identifiers belong to this checked file, not an externally verified merged entity.
    result['identifiers'] = {k: fields.get(k, []) for k in ('cas', 'pubchem', 'structure')}
    target_tables = []
    for si, section in enumerate(document.get('data', [])):
        if not isinstance(section, dict) or section.get('id') != 'related_table':
            continue
        for ti, table in enumerate(section.get('value', [])):
            if not isinstance(table, dict) or table.get('type') != 'target':
                continue
            rows = table.get('value', [])
            if not isinstance(rows, list):
                continue
            target_tables.append({'count': len(rows), 'json_pointer': f'/data/{si}/value/{ti}/value',
                'capture_status': [s for s in document.get('complete_fetch_summary', [])
                                   if isinstance(s, dict) and s.get('section') == 'related_table' and s.get('type') == 'target'],
                'rows': [{'raw_row': raw, 'json_pointer': f'/data/{si}/value/{ti}/value/{ri}'}
                         for ri, raw in enumerate(rows[:10])], 'truncated': len(rows) > 10})
    result['target_tables'] = target_tables
    result['target_links_status'] = 'source_table_present_not_verified' if target_tables else 'not_recorded'
    return result


def details(database, root, record, version, edge_id):
    root = Path(root).resolve()
    if not re.fullmatch(r'source-row:[0-9a-f]{64}', edge_id):
        raise ValueError('Invalid source row')
    # Verify the original herb snapshot too, so another root cannot be substituted silently.
    _, sha = read_source(root, record['source']['relative_file_path'])
    if sha != record['source']['file_sha256']:
        raise ValueError('Herb source snapshot changed')
    with closing(connect(database)) as con:
        con.execute('BEGIN')
        herb = con.execute('SELECT file_sha256 FROM herbs WHERE source_record_id=? AND dataset_version=?',
                           (record['source_record_id'], version)).fetchone()
        edge = con.execute("SELECT * FROM edges WHERE edge_id=? AND herb_id=? AND kind='ingredient'",
                           (edge_id, record['source_record_id'])).fetchone()
        if herb is None or herb[0] != sha or edge is None:
            raise ValueError('Ingredient row does not belong to selected source')
        raw = json.loads(edge['raw_row'])
        count = con.execute('SELECT COUNT(*) FROM file_candidates WHERE node_id=?', (edge['node_id'],)).fetchone()[0]
        paths = [r[0] for r in con.execute('SELECT relative_path FROM file_candidates WHERE node_id=? ORDER BY relative_path LIMIT 3', (edge['node_id'],))]
        candidates = []
        for path in paths:
            try:
                candidates.append(inspect_candidate(root, path, raw))
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                candidates.append({'relative_path': path, 'status': 'source_unavailable_or_oversized', 'identity_verified': False})
    return {'status': 'source_candidates', 'source_record_id': record['source_record_id'],
            'dataset_version': version, 'herb_source': record['source'], 'edge_id': edge_id,
            'herb_row': raw, 'herb_json_pointer': edge['source_pointer'],
            'candidate_count': count, 'candidates_truncated': count > 3,
            'candidates': candidates, 'llm_calls': 0, 'network_calls': 0,
            'message': '仅核对来源名称、分子式与分子量；未核验化学结构、立体异构或外部数据库。'
                       '标识属于各自候选文件。未列出靶点关系不等于不存在，不将药材靶点复制给成分。'}
