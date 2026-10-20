"""Add bounded source-field comparisons to the derived catalog, without merging entities."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from tcm.etcm_enrichment import connect
from tcm.etcm_identity import compare, VERSION


def build(database, entries_path, blocks_path, output, names=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Use a fresh comparison report path')
    names = list(dict.fromkeys(names if names is not None else ['白术', '黄芪', '甘草']))
    if not names or len(names) > 20:
        raise ValueError('Select between 1 and 20 explicit herb names')
    entries_bytes = Path(entries_path).read_bytes()
    blocks_bytes = Path(blocks_path).read_bytes()
    entries_sha = hashlib.sha256(entries_bytes).hexdigest()
    blocks_sha = hashlib.sha256(blocks_bytes).hexdigest()
    with closing(connect(database)) as con:
        meta = {r['key']: json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}
        if entries_sha != meta.get('pharma_entries_sha256') or blocks_sha != meta.get('pharma_blocks_sha256'):
            raise ValueError('Pharmacopoeia snapshot differs from the candidate source')
        entries = {e['id']: e for e in json.loads(entries_bytes)['entries']}
        block_data = json.loads(blocks_bytes)
        if isinstance(block_data, dict):
            block_data = block_data['blocks']
        blocks = {b['id']: b for b in block_data}
        results = []
        for name in names:
            rows = con.execute('SELECT h.document,c.evidence FROM name_candidates c JOIN herbs h '
                               'ON h.source_record_id=c.herb_id WHERE c.name_zh=? '
                               'ORDER BY h.source_record_id,c.entry_id', (name,)).fetchall()
            if not rows:
                raise ValueError('No candidate for selected name: ' + name)
            if len(rows) > 20:
                raise ValueError('Too many candidates for selected name: ' + name)
            for row in rows:
                candidate = json.loads(row['evidence'])
                result = compare(json.loads(row['document']), candidate, entries[candidate['entry_id']], blocks)
                result['pharma_entries_sha256'] = entries_sha
                result['pharma_blocks_sha256'] = blocks_sha
                results.append(result)
    # Only this already validated derived catalog is writable; the base DB and source JSON stay read-only.
    uri = Path(database).resolve().as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as con, con:
        con.execute('CREATE TABLE IF NOT EXISTS identity_comparisons ('
                    'herb_id TEXT NOT NULL, entry_id TEXT NOT NULL, evidence TEXT NOT NULL,'
                    'PRIMARY KEY(herb_id,entry_id))')
        con.executemany('INSERT OR REPLACE INTO identity_comparisons VALUES (?,?,?)',
                        [(r['source_record_id'], r['entry_id'], json.dumps(r, ensure_ascii=False)) for r in results])
    report = {'status': 'completed', 'method': VERSION, 'names': names, 'count': len(results),
              'summary': dict(Counter(r['status'] for r in results)), 'model_calls': 0,
              'identity_verified': False, 'results': results}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {k: v for k, v in report.items() if k != 'results'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--entries', type=Path, required=True)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--name', action='append', help='Explicit candidate name, at most 20; defaults to 白术、黄芪、甘草')
    args = parser.parse_args()
    print(json.dumps(build(args.database, args.entries, args.blocks, args.out, args.name), ensure_ascii=False))


if __name__ == '__main__':
    main()
