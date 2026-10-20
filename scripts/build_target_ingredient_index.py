"""Index the audited reverse rows without revisiting or modifying the original target files."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from tcm.compound_identity import compound_key


def build(directory, output):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a fresh target index path')
    report_bytes = (directory/'report.json').read_bytes()
    report = json.loads(report_bytes)
    if report.get('status') != 'completed':
        raise ValueError('Source audit has not completed')
    approved = {c['file_sha256']: c for c in report['contents']
                if c['status'] == 'source_identity_consistent_not_externally_verified'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb'):
        pass
    con = sqlite3.connect(output)
    try:
        con.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
                          'CREATE TABLE targets(uniprot TEXT PRIMARY KEY,gene TEXT,gene_key TEXT,name TEXT,'
                          'source_file TEXT,source_file_sha256 TEXT,row_count INTEGER);'
                          'CREATE INDEX gene_lookup ON targets(gene_key,uniprot);'
                          'CREATE TABLE ingredient_rows(ordinal INTEGER PRIMARY KEY,uniprot TEXT,source_sha TEXT,'
                          'source_pointer TEXT,document TEXT,match_name TEXT,match_formula TEXT,match_weight TEXT,'
                          'UNIQUE(source_sha,source_pointer));'
                          'CREATE INDEX target_rows ON ingredient_rows(uniprot,ordinal);'
                          'CREATE INDEX compound_rows ON ingredient_rows(match_name,match_formula,match_weight,uniprot,ordinal);')
        con.execute('INSERT INTO metadata VALUES (?,?)', ('build_complete', 'false')); con.commit()
        sha = hashlib.sha256(); counts = Counter(); matchable = 0
        with (directory/'target_ingredients.jsonl').open('rb') as handle:
            for ordinal, line in enumerate(handle):
                sha.update(line); row = json.loads(line)
                subject = row['target']; identifier = subject['UniProt Accession Number']
                accepted = approved.get(row['source_file_sha256'])
                if (accepted is None or accepted['identity'] != subject or
                        accepted['canonical_source'] != row['source_file'] or
                        row['raw_row'].get('Ingredient Name') != [row['ingredient_name']]):
                    raise ValueError('Export row differs from approved audit identity')
                existing = con.execute('SELECT gene,name,source_file_sha256 FROM targets WHERE uniprot=?', (identifier,)).fetchone()
                expected = (subject['Gene Symbol'], subject['Target Name'], row['source_file_sha256'])
                if existing is not None and existing != expected:
                    raise ValueError('Ambiguous source identity for the same UniProt')
                con.execute('INSERT OR IGNORE INTO targets VALUES (?,?,?,?,?,?,0)',
                            (identifier, subject['Gene Symbol'], subject['Gene Symbol'].upper(), subject['Target Name'],
                             row['source_file'], row['source_file_sha256']))
                match = compound_key(row['raw_row'])
                matchable += match is not None
                con.execute('INSERT INTO ingredient_rows VALUES (?,?,?,?,?,?,?,?)',
                            (ordinal, identifier, row['source_file_sha256'], row['json_pointer'], json.dumps(row, ensure_ascii=False),
                             *(match if match else (None, None, None))))
                counts[identifier] += 1
        if sum(counts.values()) != report['exported_target_ingredient_rows']:
            raise ValueError('Export row count differs from audit')
        for identifier, count in counts.items():
            con.execute('UPDATE targets SET row_count=? WHERE uniprot=?', (count, identifier))
        meta = {'kind': 'etcm_target_ingredients_v1', 'build_complete': True,
                'dataset_version': report['dataset_version'], 'herb_source_fingerprint': report['herb_source_fingerprint'],
                'targets': len(counts), 'rows': sum(counts.values()), 'reference_target_groups': report['current_herb_uniprot_groups'],
                'compound_lookup_version': 1, 'compound_matchable_rows': matchable,
                'report_sha256': hashlib.sha256(report_bytes).hexdigest(), 'export_sha256': sha.hexdigest()}
        con.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in meta.items()])
        con.commit()
        return meta
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.audit, args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
