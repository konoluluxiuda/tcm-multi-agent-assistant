"""Audit every local target file and export only source-consistent reverse ingredient rows."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re

from tcm.etcm_enrichment import connect

MAX_DOCUMENT_BYTES = 96 * 1024 * 1024


def key(value):
    return ' '.join(value.replace('_', ' ').casefold().split()) if isinstance(value, str) else ''


def digest(path):
    before = path.stat()
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during hashing')
    return result.hexdigest(), after.st_size


def identity(document):
    fields = defaultdict(list)
    for si, section in enumerate(document.get('data', [])):
        if not isinstance(section, dict) or section.get('id') != 'base_information':
            continue
        for vi, item in enumerate(section.get('value', [])):
            if isinstance(item, dict) and item.get('key') in ('Target Name', 'Gene Symbol', 'UniProt Accession Number'):
                fields[item['key']].append({'value': item.get('value'), 'json_pointer': f'/data/{si}/value/{vi}/value'})
    values = {}
    for label in ('Target Name', 'Gene Symbol', 'UniProt Accession Number'):
        witnesses = fields[label]
        value = witnesses[0]['value'] if len(witnesses) == 1 else None
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else None
        values[label] = value.strip() if isinstance(value, str) else None
    complete = (document.get('code') == 1 and all(values.values()) and
                re.fullmatch(r'[A-Z0-9]{6}(?:[A-Z0-9]{4})?', values['UniProt Accession Number'] or '') is not None)
    return values, dict(fields), bool(complete)


def reference_counts(values, reference):
    expected = (key(values['Target Name']), key(values['Gene Symbol']))
    return {'reference_matching_rows': reference.get(expected, 0),
            'reference_conflicting_rows': sum(count for (_, gene), count in reference.items() if gene != expected[1]),
            'reference_name_variant_rows': sum(count for (name, gene), count in reference.items()
                                               if gene == expected[1] and name != expected[0])}


def ingredient_rows(document):
    rows = []
    malformed = 0
    for si, section in enumerate(document.get('data', [])):
        if not isinstance(section, dict) or section.get('id') != 'related_table':
            continue
        for ti, table in enumerate(section.get('value', [])):
            if not isinstance(table, dict) or table.get('type') != 'ingredient':
                continue
            capture = [s for s in document.get('complete_fetch_summary', [])
                       if isinstance(s, dict) and s.get('section') == 'related_table' and s.get('type') == 'ingredient']
            for ri, row in enumerate(table.get('value', [])):
                name = row.get('Ingredient Name') if isinstance(row, dict) else None
                if not isinstance(name, list) or len(name) != 1 or not isinstance(name[0], str) or not name[0].strip():
                    malformed += 1
                    continue
                rows.append({'ingredient_name': name[0], 'raw_row': row,
                             'json_pointer': f'/data/{si}/value/{ti}/value/{ri}', 'capture_status': capture})
    return rows, malformed


def audit(source, database, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('Output must be outside the original snapshot')
    if output.exists():
        raise FileExistsError('Use a fresh audit output directory')
    directory = source/'etcm_targets'
    if not directory.is_dir():
        raise ValueError('Missing target directory')
    with closing(connect(database)) as con:
        con.execute('BEGIN')
        meta = {r['key']: json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}
        references = defaultdict(Counter)
        for identifier, row in con.execute("SELECT n.identifier,e.raw_row FROM edges e JOIN nodes n ON n.node_id=e.node_id "
                                           "WHERE e.kind='target' AND n.identifier IS NOT NULL"):
            raw = json.loads(row)
            name, gene = raw.get('Target Name'), raw.get('Gene Symbol')
            if isinstance(name, list) and len(name) == 1 and isinstance(gene, list) and len(gene) == 1:
                references[identifier][(key(name[0]), key(gene[0]))] += 1
    files = []
    by_hash = defaultdict(list)
    paths = sorted(directory.glob('*.json'))
    for i, path in enumerate(paths, 1):
        relative = path.relative_to(source).as_posix()
        if not path.resolve().is_relative_to(source):
            files.append({'relative_path': relative, 'status': 'outside_snapshot'})
            continue
        try:
            sha, size = digest(path)
            item = {'relative_path': relative, 'file_sha256': sha, 'bytes': size}
            by_hash[sha].append(item)
        except (OSError, ValueError):
            item = {'relative_path': relative, 'status': 'unreadable_or_changing'}
        files.append(item)
        if i % 25 == 0:
            print(json.dumps({'files_hashed': i, 'total_files': len(paths), 'phase': 'hashing'}), flush=True)
    contents, exported = [], []
    for sha, copies in by_hash.items():
        content = {'file_sha256': sha, 'copy_count': len(copies), 'status': 'invalid_or_oversized'}
        try:
            path = (source/copies[0]['relative_path']).resolve()
            if not path.is_relative_to(source):
                raise ValueError('Source path changed')
            with path.open('rb') as handle:
                data = handle.read(MAX_DOCUMENT_BYTES + 1)
            if len(data) > MAX_DOCUMENT_BYTES or hashlib.sha256(data).hexdigest() != sha:
                raise ValueError('Source changed or document exceeds bounded parser')
            document = json.loads(data)
            values, witnesses, complete = identity(document)
            content.update(identity=values, identity_evidence=witnesses)
            matching = [r for r in copies if key(Path(r['relative_path']).stem) == key(values['Target Name'])]
            reference = references.get(values['UniProt Accession Number'], Counter())
            checks = reference_counts(values, reference)
            content.update(filename_matching_copies=len(matching), **checks)
            status = ('identity_incomplete' if not complete else 'filename_unresolved' if not matching else
                      'reference_conflict' if checks['reference_conflicting_rows'] else
                      'reference_name_variation' if checks['reference_name_variant_rows'] else
                      'unreferenced_identity' if not checks['reference_matching_rows'] else
                      'source_identity_consistent_not_externally_verified')
            content['status'] = status
            rows, malformed = ingredient_rows(document)
            content.update(ingredient_rows=len(rows), malformed_ingredient_rows=malformed)
            for item in copies:
                item.update(internal_identity=values, duplicate_content=len(copies) > 1,
                            filename_matches_internal_name=item in matching,
                            status=status if item in matching else 'filename_internal_identity_mismatch')
            if status == 'source_identity_consistent_not_externally_verified':
                canonical = sorted(matching, key=lambda r: r['relative_path'])[0]
                content['canonical_source'] = canonical['relative_path']
                for row in rows:
                    exported.append({**row, 'target': values, 'target_identity_evidence': witnesses,
                                     'source_file': canonical['relative_path'], 'source_file_sha256': sha,
                                     'identity_verified_externally': False, 'ingredient_identity_verified': False,
                                     'relation_scope': 'target_file_reported_ingredient_not_experimental_binding'})
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            content['status'] = 'invalid_or_oversized'
            for item in copies:
                item['status'] = 'invalid_or_oversized'
        contents.append(content)
    summary = {'status': 'completed', 'dataset_version': meta['dataset_version'],
               'herb_source_fingerprint': meta['source_fingerprint'], 'total_files': len(files),
               'distinct_file_hashes': len(by_hash), 'duplicate_file_copies': sum(len(v)-1 for v in by_hash.values()),
               'file_status_counts': dict(Counter(f['status'] for f in files)),
               'content_status_counts': dict(Counter(c['status'] for c in contents)),
               'eligible_target_contents': sum(c['status'] == 'source_identity_consistent_not_externally_verified' for c in contents),
               'exported_target_ingredient_rows': len(exported), 'current_herb_uniprot_groups': len(references),
               'model_calls': 0, 'network_calls': 0, 'original_files_modified': False,
               'scope': 'source_consistency_audit_not_external_validation', 'contents': contents}
    output.mkdir(parents=True)
    (output/'files.json').write_text(json.dumps(files, ensure_ascii=False, indent=2)+'\n')
    with (output/'target_ingredients.jsonl').open('x') as handle:
        for row in exported:
            handle.write(json.dumps(row, ensure_ascii=False)+'\n')
    (output/'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    return {k: v for k, v in summary.items() if k != 'contents'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.source, args.database, args.out), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
