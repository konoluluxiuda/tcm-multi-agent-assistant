"""Build an isolated SQLite FTS5 research index from a verified revision view."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from scripts.build_mineru_pharmacopoeia import read, write
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import PARAMETERS, VERSION, terms

ROOT = Path(__file__).resolve().parents[1]


def build(revisions, output):
    start = time.perf_counter()
    if output.exists():
        raise FileExistsError('Use a fresh research index directory')
    receipt = read(revisions / 'summary.json')
    view_path = revisions / 'research_view.json'
    if digest(view_path) != receipt['research_view_sha256']:
        raise ValueError('Research view changed since revision build')
    if digest(revisions / 'revision_ledger.json') != receipt['ledger_sha256']:
        raise ValueError('Revision ledger changed since build')
    view = read(view_path)
    if view['human_signoff'] is not False or view['production_eligible'] is not False:
        raise ValueError('This index expects unapproved research candidates')
    output.mkdir(parents=True)
    path = output / 'research.sqlite3'
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE entries(entry_id TEXT PRIMARY KEY,name TEXT NOT NULL);
        CREATE TABLE chunks(chunk_id TEXT UNIQUE NOT NULL,entry_id TEXT NOT NULL,
          field_index INTEGER NOT NULL,name TEXT NOT NULL,label TEXT NOT NULL,scope TEXT NOT NULL,
          text TEXT NOT NULL,raw_text TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX chunks_entry_field ON chunks(entry_id,label);
        CREATE VIRTUAL TABLE fts USING fts5(name_tokens,field_tokens,body_tokens);
    ''')
    lengths = []
    for entry in view['entries']:
        if entry['production_eligible'] is not False or entry['human_signoff'] is not False:
            raise ValueError('Unexpected approval state')
        connection.execute('INSERT INTO entries VALUES(?,?)', (entry['id'], entry['name_zh_candidate']))
        for index, field in enumerate(entry['fields']):
            if not field['text'].strip():
                continue  # Pure-asset fields remain in the source view, not searchable body text.
            chunk_id = f"{entry['id']}:field:{index:03}"
            payload = {'evidence': field['evidence'], 'field_index': index,
                       'field_overlay_ids': field['overlay_ids'], 'entry_overlay_ids': entry['applied_overlay_ids'],
                       'raw_name': entry['raw_name_zh_candidate'], 'review_status': entry['review_status'],
                       'raw_flags': entry['raw_flags'], 'production_eligible': False,
                       'title_evidence': entry['title_evidence']}
            cursor = connection.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',
                (chunk_id, entry['id'], index, entry['name_zh_candidate'], field['label'], field['scope'],
                 field['text'], field['raw_text'], json.dumps(payload, ensure_ascii=False)))
            connection.execute('INSERT INTO fts(rowid,name_tokens,field_tokens,body_tokens) VALUES(?,?,?,?)',
                (cursor.lastrowid, ' '.join(terms(entry['name_zh_candidate'])),
                 ' '.join(terms(field['label'])), ' '.join(terms(field['text']))))
            lengths.append(len(field['text']))
    metadata = {'version': VERSION, 'parameters': PARAMETERS, 'view_sha256': digest(view_path)}
    connection.executemany('INSERT INTO metadata VALUES(?,?)',
                           [(k, json.dumps(v, ensure_ascii=False)) for k, v in metadata.items()])
    connection.commit()
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    connection.close()
    manifest = {**metadata, 'index_sha256': digest(path), 'entries': len(view['entries']), 'chunks': len(lengths),
                'index_bytes': path.stat().st_size, 'max_field_chars': max(lengths),
                'research_only': True, 'production_ingested': False, 'human_signoff': False,
                'build_seconds': round(time.perf_counter() - start, 4),
                'code_sha256': {'retriever': digest(ROOT / 'tcm/mineru_retrieval.py'),
                                'builder': digest(Path(__file__))}}
    write(output / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revisions', type=Path, default=ROOT / 'var/mineru_revisions_v2')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    args = parser.parse_args()
    print(json.dumps(build(args.revisions, args.out), ensure_ascii=False))
