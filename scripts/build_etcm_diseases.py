"""Audit local disease JSON and publish a fresh, versioned read-only gene-set catalog."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from time import perf_counter

from tcm.disease_catalog import KIND, dumps, inspect_document, key, read_document, identifier
from tcm.etcm_enrichment import connect as herb_connect


def stat_token(path):
    stat = path.lstat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def background(path):
    with closing(herb_connect(path)) as con:
        con.execute('BEGIN')
        meta = {r[0]: json.loads(r[1]) for r in con.execute('SELECT key,value FROM metadata')}
        ids = {r[0] for r in con.execute("SELECT DISTINCT n.identifier FROM edges e JOIN nodes n ON n.node_id=e.node_id WHERE e.kind='target' AND n.kind='target' AND n.identity_status LIKE 'source_declared_uniprot_not_externally_verified%'") if identifier(r[0])}
    return ids, {'dataset_version': meta['dataset_version'], 'source_fingerprint': meta['source_fingerprint'],
                 'target_set_sha256': hashlib.sha256(dumps(sorted(ids)).encode()).hexdigest(), 'target_count': len(ids)}


def build(source, enrichment, output, version):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source) or Path(enrichment).resolve().is_relative_to(output):
        raise ValueError('Output must be separate from source data and existing catalogs')
    if not isinstance(version, str) or not version.strip():
        raise ValueError('A dataset version is required')
    directory = source / 'etcm_diseases'
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('Missing regular disease directory')
    paths = sorted(directory.glob('*.json'))
    if not paths:
        raise ValueError('No disease JSON files')
    tokens = {p.name: stat_token(p) for p in paths}
    bg, binding = background(enrichment)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    pending, final = output/'catalog.building.sqlite3', output/'diseases.sqlite3'
    started = perf_counter();file_status = Counter();capture_status = Counter();species_status = Counter()
    row_keys = Counter();hash_counts = Counter();all_ids = set();rows_count = indexed = malformed = duplicate_rows = no_ids = 0
    manifest = [];set_hashes = Counter();no_tables = zero_targets = conflicts = 0
    with closing(sqlite3.connect(pending)) as con:
        con.executescript('''
          PRAGMA user_version=1;
          PRAGMA journal_mode=DELETE;
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE files(relative_path TEXT PRIMARY KEY,file_sha256 TEXT,bytes INTEGER,status TEXT NOT NULL,record_id TEXT);
          CREATE TABLE diseases(record_id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL,file_sha256 TEXT UNIQUE NOT NULL,document TEXT NOT NULL);
          CREATE TABLE target_rows(record_id TEXT NOT NULL REFERENCES diseases(record_id),ordinal INTEGER NOT NULL,
             uniprot TEXT,gene TEXT,json_pointer TEXT NOT NULL,raw_row TEXT NOT NULL,row_status TEXT NOT NULL,species_fields TEXT NOT NULL,
             PRIMARY KEY(record_id,ordinal));
        ''')
        meta = {'kind': KIND, 'build_complete': False, 'dataset_version': version, 'herb_background': binding,
                'scope': 'source_consistency_not_external_or_biological_validation'}
        con.executemany('INSERT INTO metadata VALUES (?,?)', [(k, dumps(v)) for k, v in meta.items()]);con.commit()
        with (output/'files.jsonl').open('x', encoding='utf-8') as report:
            for index, path in enumerate(paths, 1):
                relative = path.relative_to(source).as_posix();sha = None;size = None;sid = None;profile = None
                try:
                    document, sha, size = read_document(path, source)
                    hash_counts[sha] += 1
                    profile, rows = inspect_document(document, relative)
                    status = profile['identity_status']
                except (OSError, ValueError, TypeError, KeyError, RecursionError):
                    status = 'invalid_unreadable_or_oversized'
                if status == 'source_identity_consistent_not_externally_verified':
                    sid = 'etcm-disease:' + sha
                    if con.execute('SELECT 1 FROM diseases WHERE record_id=?', (sid,)).fetchone():
                        status = 'duplicate_content'
                    else:
                        ids = {r['uniprot'] for r in rows if r['uniprot']};all_ids.update(ids)
                        profile['source'] = {'relative_path': relative, 'file_sha256': sha, 'name': profile['name']}
                        profile['background_overlap'] = {'target_count': len(ids & bg), 'outside_background_count': len(ids - bg),
                                                         'herb_background_target_count': len(bg)}
                        profile['target_set_sha256'] = hashlib.sha256(dumps(sorted(ids)).encode()).hexdigest()
                        if ids:set_hashes[profile['target_set_sha256']] += 1
                        con.execute('INSERT INTO diseases VALUES (?,?,?,?,?)', (sid, profile['name'], key(profile['name']), sha, dumps(profile)))
                        con.executemany('INSERT INTO target_rows VALUES (?,?,?,?,?,?,?,?)',
                            [(sid, r['ordinal'], r['uniprot'], r['gene'], r['json_pointer'], dumps(r['raw_row']), r['row_status'], dumps(r['species_fields'])) for r in rows])
                        indexed += 1;rows_count += len(rows);malformed += profile['unidentified_rows'];duplicate_rows += profile['duplicate_identifier_rows']
                        no_tables += profile['target_tables'] == 0;zero_targets += not ids;conflicts += bool(profile['conflicting_gene_identifiers'])
                        no_ids += sum(r['gene'] is None for r in rows)
                        capture_status[profile['capture_state']] += 1;species_status[profile['species_status']] += 1
                        row_keys.update(profile['target_row_keys'])
                file_status[status] += 1
                item = {'relative_path': relative, 'file_sha256': sha, 'bytes': size, 'status': status, 'record_id': sid}
                con.execute('INSERT INTO files VALUES (?,?,?,?,?)', tuple(item.values()))
                manifest.append(item)
                if profile:item = {**item, 'name': profile['name'], 'capture_state': profile['capture_state'], 'target_rows': profile['target_row_count'], 'target_count': profile['target_count']}
                report.write(dumps(item) + '\n')
                if index % 100 == 0:
                    con.commit()
                    print(dumps({'files_processed': index, 'total_files': len(paths), 'indexed_sources': indexed}), flush=True)
        # A changed input snapshot must not publish a partially stale index.
        if {p.name: stat_token(p) for p in directory.glob('*.json')} != tokens:
            raise ValueError('Source snapshot changed during build')
        if background(enrichment)[1] != binding:
            raise ValueError('Herb background changed during build')
        con.executescript('CREATE INDEX diseases_by_name ON diseases(name_key); CREATE INDEX rows_by_uniprot ON target_rows(uniprot,record_id);')
        fingerprint = hashlib.sha256(dumps(manifest).encode()).hexdigest()
        duplicate_names = con.execute('SELECT COUNT(*) FROM (SELECT name_key FROM diseases GROUP BY name_key HAVING COUNT(*)>1)').fetchone()[0]
        report = {'status': 'built', 'kind': KIND, 'dataset_version': version, 'source_fingerprint': fingerprint,
                  'files': len(paths), 'source_bytes': sum(x['bytes'] or 0 for x in manifest), 'indexed_sources': indexed,
                  'file_status_counts': dict(file_status), 'distinct_file_hashes': len(hash_counts),
                  'duplicate_file_copies': sum(n-1 for n in hash_counts.values()), 'ambiguous_name_groups': duplicate_names,
                  'target_rows': rows_count, 'distinct_uniprot': len(all_ids), 'unidentified_rows': malformed,
                  'duplicate_identifier_rows': duplicate_rows, 'rows_without_single_gene_symbol': no_ids,
                  'sources_without_target_table': no_tables, 'sources_without_usable_targets': zero_targets,
                  'sources_with_conflicting_gene_symbols': conflicts, 'capture_state_counts': dict(capture_status),
                  'species_status_counts': dict(species_status), 'target_row_key_source_counts': dict(row_keys),
                  'identical_nonempty_target_set_groups': sum(n>1 for n in set_hashes.values()),
                  'sources_in_identical_nonempty_target_sets': sum(n for n in set_hashes.values() if n>1),
                  'background': binding, 'disease_identifiers_in_herb_background': len(all_ids & bg),
                  'disease_identifiers_outside_herb_background': len(all_ids-bg),
                  'duration_seconds': round(perf_counter()-started, 3), 'model_calls': 0, 'network_calls': 0,
                  'clinical_or_species_validation': False, 'chat_enabled': False, 'ranking_implemented': False,
                  'original_files_modified': False}
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Catalog integrity check failed')
        meta.update(build_complete=True, source_fingerprint=fingerprint, summary=report)
        con.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', [(k, dumps(v)) for k, v in meta.items()]);con.commit()
    (output/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    # Fresh directory plus exclusive link: never replace a previously published catalog.
    os.link(pending, final);pending.unlink()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--enrichment', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    print(dumps(build(args.source, args.enrichment, args.out, args.version)), flush=True)


if __name__ == '__main__':
    main()
