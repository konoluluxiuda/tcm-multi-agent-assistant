"""Read-only, provenance-bearing disease target sets; no therapeutic inference."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

KIND = 'etcm_disease_genes_v1'
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def key(value):
    return ' '.join(unicodedata.normalize('NFKC', value).replace('_', ' ').casefold().split()) if isinstance(value, str) else ''


def single(value):
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    return value.strip() if isinstance(value, str) and value.strip() and value.strip().casefold() not in ('null', 'none', 'n/a') else None


def identifier(value):
    value = single(value)
    # Matches the existing source catalog's format gate, not external UniProt validation.
    return value if value and re.fullmatch(r'[A-Z0-9]{6}(?:[A-Z0-9]{4})?', value) else None


def read_document(path, root):
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Source link is not a regular snapshot file')
    before = path.stat()
    if before.st_size > MAX_DOCUMENT_BYTES:
        raise ValueError('Document exceeds parser budget')
    with path.open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    after = path.stat()
    token = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if token(before) != token(after) or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('Source changed or exceeds parser budget')
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError('Expected an object')
    return document, hashlib.sha256(raw).hexdigest(), len(raw)


def inspect_document(document, relative_path):
    """Preserve target rows and witnesses even when identifiers are unusable."""
    sections = document.get('data')
    if not isinstance(sections, list):
        raise ValueError('Invalid data sections')
    fields, witnesses, rows, tables = {}, {}, [], 0
    for si, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError('Invalid section')
        if section.get('id') not in ('base_information', 'related_table'):
            continue
        if not isinstance(section.get('value'), list):
            raise ValueError('Invalid section values')
        for vi, item in enumerate(section['value']):
            pointer = f'/data/{si}/value/{vi}'
            if not isinstance(item, dict):
                raise ValueError('Invalid field or table')
            if section['id'] == 'base_information':
                label = item.get('key')
                if isinstance(label, str):
                    fields.setdefault(label, []).append(item.get('value'))
                    witnesses.setdefault(label, []).append(pointer + '/value')
            elif item.get('type') == 'target':
                tables += 1
                if not isinstance(item.get('value'), list):
                    raise ValueError('Invalid target rows')
                for ri, row in enumerate(item['value']):
                    uid = identifier(row.get('UniProt Accession Number')) if isinstance(row, dict) else None
                    gene = single(row.get('Gene Symbol')) if isinstance(row, dict) else None
                    species = {k: v for k, v in row.items() if re.search(r'species|organism|taxon|taxonomy|tax.?id', k, re.I)} if isinstance(row, dict) else {}
                    rows.append({'ordinal': len(rows), 'uniprot': uid, 'gene': gene,
                                 'json_pointer': pointer + f'/value/{ri}', 'raw_row': row,
                                 'row_status': 'source_identifier_format_valid' if uid else 'missing_or_ambiguous_identifier',
                                 'species_fields': species})
    name_values = fields.get('Disease Name', [])
    name = single(name_values[0]) if len(name_values) == 1 else None
    status = 'source_identity_consistent_not_externally_verified'
    if type(document.get('code')) is not int or document['code'] != 1 or not name:
        status = 'identity_incomplete'
    elif key(Path(relative_path).stem) != key(name):
        status = 'filename_internal_identity_mismatch'
    elif document.get('name') is not None and key(document['name']) != key(name):
        status = 'page_name_conflict'
    elif document.get('page_title') is not None and key(document['page_title']) != key('Disease ' + name + ' Details page'):
        status = 'page_title_conflict'
    summaries = document.get('complete_fetch_summary', [])
    capture = [s for s in summaries if isinstance(s, dict) and s.get('section') == 'related_table' and s.get('type') == 'target'] if isinstance(summaries, list) else []
    counts_match = (len(capture) == 1 and type(capture[0].get('actual')) is int
                    and type(capture[0].get('expected')) is int
                    and capture[0]['actual'] == capture[0]['expected'] == len(rows))
    capture_state = ('ambiguous_target_tables' if tables > 1 else 'not_recorded' if not capture else 'ambiguous_summary' if len(capture) != 1 else
                     'count_mismatch' if not counts_match else
                     'source_reported_complete' if capture[0].get('status') == 'ok' and tables == 1 else
                     'source_reported_' + str(capture[0].get('status', 'unknown')))
    ids = {r['uniprot'] for r in rows if r['uniprot']}
    genes = {}
    for row in rows:
        if row['uniprot'] and row['gene']:
            genes.setdefault(row['uniprot'], set()).add(row['gene'])
    species_fields = {k: v for k, v in fields.items() if re.search(r'species|organism|taxon|taxonomy|tax.?id', k, re.I)}
    species_rows = sum(bool(r['species_fields']) for r in rows)
    profile = {'name': name, 'identity_status': status, 'identity_fields': fields,
               'identity_pointers': witnesses, 'capture_status': capture, 'capture_state': capture_state,
               'target_tables': tables, 'target_row_count': len(rows), 'target_count': len(ids),
               'unidentified_rows': sum(r['uniprot'] is None for r in rows),
               'duplicate_identifier_rows': sum(r['uniprot'] is not None for r in rows) - len(ids),
               'conflicting_gene_identifiers': sorted(k for k, v in genes.items() if len(v) > 1),
               'species_fields': species_fields, 'rows_with_species_fields': species_rows,
               'species_status': 'source_fields_unverified' if species_fields or species_rows else 'not_recorded',
               'external_disease_id_status': 'not_mapped', 'target_row_keys': sorted({k for r in rows if isinstance(r['raw_row'], dict) for k in r['raw_row']}),
               'association_scope': 'source_reported_disease_target_not_independent_validation'}
    return profile, rows


def connect(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON')
        meta = {r['key']: json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}
        if meta.get('kind') != KIND or meta.get('build_complete') is not True or con.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Incomplete or incompatible disease catalog')
    except Exception:
        con.close()
        raise
    return con, meta


def lookup(path, token, *, page=1):
    if not isinstance(token, str) or not token.strip() or len(token) > 300 or type(page) is not int or not 1 <= page <= 10000:
        raise ValueError('Use an exact disease name or source ID and a bounded page')
    con, meta = connect(path)
    with closing(con):
        con.execute('BEGIN')
        column = 'record_id' if token.startswith('etcm-disease:') else 'name_key'
        matches = con.execute(f'SELECT record_id,document FROM diseases WHERE {column}=? ORDER BY record_id LIMIT 21',
                              (token if column == 'record_id' else key(token),)).fetchall()
        result = {'dataset_version': meta['dataset_version'], 'source_fingerprint': meta['source_fingerprint'],
                  'resolution_status': 'not_found' if not matches else 'ambiguous_source' if len(matches) > 1 else 'source_record',
                  'options': [dict(record_id=r['record_id'], **json.loads(r['document'])['source']) for r in matches[:20]],
                  'options_truncated': len(matches) > 20}
        if len(matches) != 1:
            return result
        profile = json.loads(matches[0]['document']);sid = matches[0]['record_id']
        ids = [r[0] for r in con.execute('SELECT DISTINCT uniprot FROM target_rows WHERE record_id=? AND uniprot IS NOT NULL ORDER BY uniprot', (sid,))]
        rows = con.execute('SELECT ordinal,uniprot,gene,json_pointer,raw_row,row_status,species_fields FROM target_rows WHERE record_id=? ORDER BY ordinal LIMIT 20 OFFSET ?', (sid, (page - 1) * 20)).fetchall()
        result.update(record_id=sid, profile=profile, target_ids=ids, page=page,
                      rows=[{**dict(r), 'raw_row': json.loads(r['raw_row']), 'species_fields': json.loads(r['species_fields'])} for r in rows],
                      has_next=page * 20 < profile['target_row_count'])
        return result


def verify_witness(source_root, profile, row):
    """Read a selected original file and reject changed content or a wrong pointer."""
    source = profile['source']
    document, sha, _ = read_document(Path(source_root) / source['relative_path'], source_root)
    if sha != source['file_sha256']:
        raise ValueError('Source content changed')
    value = document
    for token in row['json_pointer'].split('/')[1:]:
        value = value[int(token)] if isinstance(value, list) else value[token]
    if value != row['raw_row']:
        raise ValueError('Source witness differs')
    return True
