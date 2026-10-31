"""Score storage boundaries against frozen original-page/block assignments."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.build_mineru_pharmacopoeia import read, write
from scripts.build_mineru_revisions import verified_corpus
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_revisions import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def source_text(segments, blocks):
    values = []
    for segment in segments:
        text = blocks[segment['block_id']]['raw']['text']
        start, end = segment['start'], segment['end']
        if not 0 <= start < end <= len(text): raise ValueError('Invalid reference offsets')
        values.append(text[start:end])
    return '\n'.join(values)


def evidence_matches(evidence, segments, blocks):
    if len(evidence) != len(segments): return False
    return all(all(actual.get(k) == expected[k] for k in ('block_id', 'start', 'end'))
               and actual.get('source') == blocks[expected['block_id']]['source']
               and actual.get('bbox_0_1000') == blocks[expected['block_id']]['raw']['bbox']
               for actual, expected in zip(evidence, segments))


def assess_cases(db, blocks, cases):
    results = []
    for case in cases:
        for key, witness in case['source_witnesses'].items():
            block = blocks[key]
            if (fingerprint(block) != witness['block_sha256'] or block['source'] != witness['source']
                    or block['raw']['bbox'] != witness['bbox_0_1000']):
                raise ValueError('Frozen original block witness changed')
        expected = source_text(case['storage_segments'], blocks)
        if hashlib.sha256(expected.encode()).hexdigest() != case['expected_storage_sha256']:
            raise ValueError('Reference text checksum mismatch')
        rows = db.execute('SELECT raw_text,payload FROM chunks WHERE entry_id=? AND label=? AND scope=?',
                          (case['entry_id'], '贮藏', case['scope'])).fetchall()
        value_ok = len(rows) == 1 and rows[0][0] == expected
        evidence_ok = len(rows) == 1 and evidence_matches(json.loads(rows[0][1])['evidence'], case['storage_segments'], blocks)
        annotation_ok = True
        if case['annotation_block_ids']:
            notes = db.execute('SELECT raw_text,payload FROM chunks WHERE entry_id=? AND label=? AND scope=?',
                               (case['entry_id'], '条目注释', case['scope'])).fetchall()
            segments = [{'block_id': k, 'start': 0, 'end': len(blocks[k]['raw']['text'])}
                        for k in case['annotation_block_ids']]
            annotation_ok = (len(notes) == 1 and notes[0][0] == source_text(segments, blocks)
                             and evidence_matches(json.loads(notes[0][1])['evidence'], segments, blocks))
        results.append({'case_id': case['case_id'], 'name': case['name'], 'kind': case['kind'],
                        'storage_value_exact': value_ok, 'storage_evidence_exact': evidence_ok,
                        'annotations_preserved_separately': annotation_ok,
                        'status': 'passed' if value_ok and evidence_ok and annotation_ok else 'failed'})
    return results


def evaluate(base, index, references):
    _, _, blocks = verified_corpus(base)
    ref = read(references)
    if digest(base/'blocks.json') != ref['source_blocks_sha256']:
        raise ValueError('Reference targets different original source blocks')
    books = {'high': ROOT/'data/《中国药典》2025年版 一部.pdf',
             'full': ROOT/'data/《中国药典》2025年版 一部 全本.pdf'}
    expected = {w['source']['source_document']: w['source']['source_sha256']
                for c in ref['cases'] for w in c['source_witnesses'].values()}
    for book, sha in expected.items():
        if digest(books[book]) != sha: raise ValueError('Original PDF changed')
    manifest = read(index/'manifest.json')
    if digest(index/'research.sqlite3') != manifest['index_sha256']:
        raise ValueError('Candidate index checksum mismatch')
    with closing(sqlite3.connect((index/'research.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        cases = assess_cases(db, blocks, ref['cases'])
    return {'status': 'passed' if all(c['status']=='passed' for c in cases) else 'failed',
            'reference_sha256': digest(references), 'index_sha256': manifest['index_sha256'],
            'total': len(cases), 'passed': sum(c['status']=='passed' for c in cases), 'cases': cases,
            'reference_kind': ref['kind'], 'human_signoff': False, 'holdout': False,
            'external_model_calls': 0, 'task_completion_or_general_accuracy_claim': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, default=ROOT/'var/mineru_pharmacopoeia_v4_checked')
    p.add_argument('--index', type=Path, required=True)
    p.add_argument('--references', type=Path, default=ROOT/'eval/pharmacopoeia_storage_boundary_v1.json')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists(): p.error('Preserve previous evaluations; use a new output')
    result = evaluate(args.base, args.index, args.references)
    args.out.parent.mkdir(parents=True, exist_ok=True); write(args.out, result)
    print(json.dumps({k: result[k] for k in ('status','total','passed','external_model_calls')}))
    return 0 if result['status']=='passed' else 1


if __name__ == '__main__': raise SystemExit(main())
