"""ETCM herb source-record schema, atomic snapshot import, and exact lookup."""
import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SCHEMA_VERSION = 1
NAMESPACE = 'etcm2-local'
FIELDS = {
    'Herb Name in Pinyin': 'name_pinyin',
    'Herb Name in Latin': 'latin_name',
    'Herb Name in English': 'english_name',
    'Family': 'family', 'Habitat': 'habitat',
    'Medicinal Part': 'medicinal_part', 'Property': 'properties',
    'Flavor': 'flavors', 'Meridian Tropism': 'meridians',
    'Herb classification based on efficacy': 'efficacy_class',
    'Indications': 'indications', 'PubMed': 'reference_pmids',
    'Database Cross References': 'cross_references',
}
LIST_FIELDS = {'PubMed', 'Database Cross References'}


class InvalidRecord(ValueError):
    pass


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def text_value(value):
    parser = PlainText()
    parser.feed(value)
    return ''.join(parser.parts).strip()


def search_key(value):
    return ' '.join(value.split()).casefold()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidRecord('duplicate_json_key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise InvalidRecord('invalid_json_constant: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def parse_herb(raw, relative_path, dataset_version):
    obj = strict_json(raw)
    if not isinstance(obj, dict) or type(obj.get('code')) is not int or obj['code'] != 1:
        raise InvalidRecord('api_non_success_or_invalid_root')
    sections = obj.get('data')
    if not isinstance(sections, list):
        raise InvalidRecord('data_not_list')
    basic = [(i, s) for i, s in enumerate(sections)
             if isinstance(s, dict) and s.get('id') == 'base_information']
    if len(basic) != 1 or not isinstance(basic[0][1].get('value'), list):
        raise InvalidRecord('expected_one_flat_base_information')
    section_index, section = basic[0]
    source_id = NAMESPACE + ':' + hashlib.sha256(relative_path.encode()).hexdigest()
    provenance = {
        'source_namespace': NAMESPACE, 'dataset_version': dataset_version,
        'relative_file_path': relative_path, 'file_sha256': hashlib.sha256(raw).hexdigest(),
        'section_id': 'base_information', 'normalization_rule_version': SCHEMA_VERSION,
        'review_status': 'unverified',
    }
    fields = {target: {'state': 'missing', 'value': None, 'source': None} for target in FIELDS.values()}
    seen = set()
    warnings = ['source_content_unverified', 'chinese_names_unmapped', 'relations_not_imported']
    for i, item in enumerate(section['value']):
        if not isinstance(item, dict) or not isinstance(item.get('key'), str) or 'value' not in item:
            raise InvalidRecord('malformed_basic_field')
        key, value = item['key'], item['value']
        if key in seen:
            raise InvalidRecord('duplicate_basic_key: ' + key)
        seen.add(key)
        if key not in FIELDS:
            warnings.append('unknown_field_retained: ' + key)
            continue
        state = 'missing' if value is None or value == '' or value == [] else 'present'
        normalized = value
        if state == 'present':
            if key in LIST_FIELDS:
                if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                    raise InvalidRecord('invalid_list_field: ' + key)
                normalized = list(dict.fromkeys(x.strip() for x in value if x.strip()))
                if key == 'PubMed' and any(not re.fullmatch(r'[1-9][0-9]*', x) for x in normalized):
                    raise InvalidRecord('invalid_pubmed_id')
            elif not isinstance(value, str):
                raise InvalidRecord('invalid_string_field: ' + key)
            else:
                normalized = text_value(value) if key in ('Herb Name in Latin', 'Herb Name in English') else value.strip()
            if not normalized:
                state = 'missing'
        fields[FIELDS[key]] = {
            'state': state, 'value': normalized if state == 'present' else None,
            'source': {**provenance, 'json_pointer': f'/data/{section_index}/value/{i}/value',
                       'raw_field_key': key, 'raw_value': value, 'raw_item': item},
        }
    if fields['name_pinyin']['state'] != 'present':
        raise InvalidRecord('missing_pinyin_name')
    normalize_filename = lambda v: re.sub(r'[\W_]+', '_', v.casefold()).strip('_')
    if normalize_filename(Path(relative_path).stem) != normalize_filename(fields['name_pinyin']['value']):
        warnings.append('filename_vs_primary_name')
    return {
        'schema_version': SCHEMA_VERSION, 'source_record_id': source_id,
        'herb_id': None, 'name_zh': None, 'aliases': None, 'fields': fields,
        'source': provenance, 'raw_basic_section': section,
        'complete_fetch_summary': obj.get('complete_fetch_summary'), 'warnings': warnings,
    }


def initialize(connection):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise ValueError('Unsupported database schema version')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY, dataset_version TEXT NOT NULL, source_root TEXT NOT NULL,
            imported_at TEXT NOT NULL, summary TEXT
        );
        CREATE TABLE IF NOT EXISTS records (
            source_record_id TEXT NOT NULL, dataset_version TEXT NOT NULL,
            relative_path TEXT NOT NULL, sha256 TEXT, state TEXT NOT NULL,
            document TEXT, error TEXT, batch_id INTEGER NOT NULL,
            PRIMARY KEY (source_record_id, dataset_version)
        );
        CREATE TABLE IF NOT EXISTS names (
            source_record_id TEXT NOT NULL, dataset_version TEXT NOT NULL, name_key TEXT NOT NULL,
            PRIMARY KEY (source_record_id, dataset_version, name_key)
        );
        CREATE INDEX IF NOT EXISTS names_lookup ON names(dataset_version, name_key);
    ''')
    connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')


def import_snapshot(source, database, dataset_version):
    """Full herb-directory snapshot; commit atomically, quarantine invalid records.

    Removed files become missing_from_snapshot for this version. Other versions
    remain available. Source files are never altered. No entity merging occurs.
    """
    root, db = Path(source).resolve(), Path(database).resolve()
    if not dataset_version.strip():
        raise ValueError('dataset_version must not be empty')
    files = sorted((root / 'etcm_herbs').glob('*.json'))
    if not files:
        raise ValueError('No herb JSON files found; refusing an empty snapshot')
    if db.is_relative_to(root):
        raise ValueError('Database must be outside the read-only source directory')
    if any(not f.resolve().is_relative_to(root) for f in files):
        raise ValueError('Source symlinks must not leave source directory')
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=30)) as con:
        initialize(con)
        with con:
            con.execute('BEGIN IMMEDIATE')
            prior_root = con.execute('SELECT source_root FROM batches WHERE dataset_version=? LIMIT 1', (dataset_version,)).fetchone()
            if prior_root and prior_root[0] != str(root):
                raise ValueError('Dataset version already bound to another source root')
            batch = con.execute('INSERT INTO batches(dataset_version,source_root,imported_at) VALUES (?,?,?)',
                                (dataset_version, str(root), datetime.now(timezone.utc).isoformat())).lastrowid
            counts = {'files': len(files), 'valid': 0, 'quarantined': 0, 'inserted': 0, 'updated': 0, 'unchanged': 0}
            pinyin_counts = Counter()
            con.execute('DELETE FROM names WHERE dataset_version=?', (dataset_version,))
            for path in files:
                relative = path.relative_to(root).as_posix()
                source_id = NAMESPACE + ':' + hashlib.sha256(relative.encode()).hexdigest()
                # I/O failure aborts the batch; malformed contents quarantine just this record.
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                old = con.execute('SELECT sha256,state FROM records WHERE source_record_id=? AND dataset_version=?',
                                  (source_id, dataset_version)).fetchone()
                try:
                    doc = parse_herb(raw, relative, dataset_version)
                    state, error = 'valid', None
                except (ValueError, UnicodeError, RecursionError) as exc:
                    doc, state, error = None, 'quarantined', str(exc)[:400]
                counts[state] += 1
                counts['inserted' if old is None else 'unchanged' if old == (digest, state) else 'updated'] += 1
                con.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?)',
                            (source_id, dataset_version, relative, digest, state,
                             json.dumps(doc, ensure_ascii=False) if doc else None, error, batch))
                if doc:
                    pinyin_counts[search_key(doc['fields']['name_pinyin']['value'])] += 1
                    keys = {search_key(doc['fields'][f]['value']) for f in ('name_pinyin', 'latin_name', 'english_name')
                            if doc['fields'][f]['state'] == 'present'}
                    con.executemany('INSERT INTO names VALUES (?,?,?)', [(source_id, dataset_version, k) for k in sorted(keys)])
            counts['missing_from_snapshot'] = con.execute(
                "UPDATE records SET state='missing_from_snapshot' WHERE dataset_version=? AND batch_id<>? AND state<>'missing_from_snapshot'",
                (dataset_version, batch)).rowcount
            counts['duplicate_pinyin_groups'] = sum(n > 1 for n in pinyin_counts.values())
            summary = {'batch_id': batch, 'dataset_version': dataset_version, **counts}
            con.execute('UPDATE batches SET summary=? WHERE id=?', (json.dumps(summary), batch))
        return summary


FILTERABLE_FIELDS = {
    'family', 'habitat', 'medicinal_part', 'properties', 'flavors',
    'meridians', 'efficacy_class', 'indications',
}


def _summarize(doc):
    """Keep flat display values plus field evidence and source limitations."""
    result = {
        'source_record_id': doc['source_record_id'],
        'source': doc['source'], 'warnings': doc['warnings'],
        'fields': doc['fields'],
    }
    for target in FIELDS.values():
        field = doc['fields'].get(target)
        result[target] = field['value'] if field and field['state'] == 'present' else None
    return result


def _resolve_dataset_version(con, dataset_version):
    if dataset_version is None:
        row = con.execute('SELECT dataset_version FROM batches ORDER BY id DESC LIMIT 1').fetchone()
        if row is None:
            raise ValueError('No imported dataset')
        dataset_version = row[0]
    exists = con.execute('SELECT 1 FROM batches WHERE dataset_version=?', (dataset_version,)).fetchone()
    if not exists:
        return dataset_version, False
    return dataset_version, True


def filter_herbs(database, *, filters, dataset_version=None, limit=20, offset=0):
    """Filter herbs by raw field values; casefold substring match, AND across filters.

    filters: dict of target field name -> search string. Only FILTERABLE_FIELDS are
    accepted (name fields use lookup, list fields are not filterable in this MVP).
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer between 1 and 100')
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a non-negative integer')
    if not isinstance(filters, dict) or not filters:
        raise ValueError('filters must be a non-empty dict')
    unknown = set(filters) - FILTERABLE_FIELDS
    if unknown:
        raise ValueError('unsupported_filter_field: ' + ', '.join(sorted(unknown)))
    if any(not isinstance(v, str) for v in filters.values()):
        raise ValueError('filter values must be strings')
    cleaned = {k: v.strip() for k, v in filters.items()}
    if any(not v for v in cleaned.values()):
        raise ValueError('filter values must not be empty')
    db = Path(database).resolve()
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as con:
        if con.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported database schema version')
        dataset_version, exists = _resolve_dataset_version(con, dataset_version)
        if not exists:
            return {'status': 'dataset_not_found', 'dataset_version': dataset_version, 'filters': cleaned, 'records': []}
        records = []
        total = 0
        rows = con.execute(
            "SELECT document FROM records WHERE dataset_version=? AND state='valid' ORDER BY relative_path, source_record_id",
            (dataset_version,),
        )
        for (document,) in rows:
            if document is None:
                continue
            doc = json.loads(document)
            if all(
                search_key(needle) in search_key(doc['fields'][target]['value'] or '')
                for target, needle in cleaned.items()
            ):
                if offset <= total < offset + limit:
                    records.append(_summarize(doc))
                total += 1
        return {
            'status': 'ok', 'dataset_version': dataset_version, 'filters': cleaned,
            'count': total, 'returned_count': len(records), 'limit': limit, 'offset': offset,
            'next_offset': offset + limit if offset + limit < total else None, 'records': records,
        }


def compare_herbs(database, *, names, dataset_version=None):
    """Resolve multiple herb names and align their reduced fields side by side."""
    if not isinstance(names, (list, tuple)) or not names:
        raise ValueError('names must be a non-empty list')
    if any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError('names must be non-empty strings')
    db = Path(database).resolve()
    herbs = []
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as con:
        if con.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported database schema version')
        dataset_version, exists = _resolve_dataset_version(con, dataset_version)
        if not exists:
            return {'status': 'dataset_not_found', 'dataset_version': dataset_version, 'herbs': []}
    for name in names:
        result = lookup(database, name=name, dataset_version=dataset_version)
        entry = {'query': name, 'status': result['status'], 'warnings': result.get('warnings', [])}
        if result['status'] == 'ok':
            entry['record'] = _summarize(result['records'][0])
        elif result['status'] == 'ambiguous_entity':
            entry['records'] = [_summarize(r) for r in result['records']]
        herbs.append(entry)
    return {'status': 'ok', 'dataset_version': dataset_version, 'herbs': herbs}


def lookup(database, *, name=None, source_record_id=None, dataset_version=None):
    if (name is None) == (source_record_id is None):
        raise ValueError('Provide exactly one of name or source_record_id')
    if not (name if name is not None else source_record_id).strip():
        raise ValueError('Query must not be empty')
    db = Path(database).resolve()
    # Read-only connection: a query cannot silently create an empty database.
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as con:
        if con.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported database schema version')
        if dataset_version is None:
            row = con.execute('SELECT dataset_version FROM batches ORDER BY id DESC LIMIT 1').fetchone()
            if row is None:
                raise ValueError('No imported dataset')
            dataset_version = row[0]
        exists = con.execute('SELECT 1 FROM batches WHERE dataset_version=?', (dataset_version,)).fetchone()
        if not exists:
            return {'status': 'dataset_not_found', 'dataset_version': dataset_version, 'records': []}
        if name is not None:
            rows = con.execute('''SELECT r.state,r.document,r.error FROM records r JOIN names n
                USING(source_record_id,dataset_version) WHERE n.dataset_version=? AND n.name_key=?
                ORDER BY r.relative_path''', (dataset_version, search_key(name))).fetchall()
        else:
            rows = con.execute('SELECT state,document,error FROM records WHERE dataset_version=? AND source_record_id=?',
                               (dataset_version, source_record_id)).fetchall()
        docs = [json.loads(row[1]) for row in rows if row[0] == 'valid']
        status = 'ok' if len(docs) == 1 else 'ambiguous_entity' if docs else 'not_found'
        if rows and not docs:
            status = rows[0][0]
        warnings = ['source_content_unverified', 'chinese_names_unmapped']
        if name is not None and re.search('[\u4e00-\u9fff]', name) and not docs:
            status = 'unsupported_name_mapping'
        return {'status': status, 'dataset_version': dataset_version, 'records': docs, 'warnings': warnings}
