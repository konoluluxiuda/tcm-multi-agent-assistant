"""Frozen source-assisted paraphrase diagnostics; not independent clinical gold."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
import time

from tcm.cross_source_report import text_search
from tcm.mineru_retrieval import ResearchRetriever

ROOT = Path(__file__).resolve().parents[1]


def assess(case, result, canonical):
    hits = result.get('hits', [])[:6]
    ids = {h['chunk_id'] for h in hits}
    expected = set(case['expected_chunk_ids'])
    provenance = all(h['chunk_id'] in canonical and all(
        h.get(k) == canonical[h['chunk_id']].get(k)
        for k in ('name', 'text', 'raw_text', 'evidence')) for h in hits)
    wrong_entity = any(h.get('name') != case['herb'] for h in hits)
    missing = sorted(expected - ids)
    priority = not case['primary_chunk_id'] or bool(hits) and hits[0]['chunk_id'] == case['primary_chunk_id']
    if not provenance or wrong_entity:
        failure = 'provenance_or_entity'
    elif case['expect_abstention']:
        failure = ('unexpected_answer' if hits else None) if result.get('status') in ('no_match', 'field_not_resolved', 'candidates') else 'invalid_abstention_status'
    elif missing:
        failure = 'no_candidates' if not hits else 'missing_required_fields'
    elif not priority:
        failure = 'wrong_priority'
    else:
        failure = None
    return {'passed': failure is None, 'failure': failure, 'missing_ids': missing,
            'covered_targets': len(expected & ids), 'expected_targets': len(expected),
            'provenance_consistent': provenance, 'wrong_entity': wrong_entity,
            'priority_correct': bool(priority), 'hit_ids': [h['chunk_id'] for h in hits]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        raise FileExistsError('Use a fresh output; preserve previous failures')
    question_path = ROOT / 'eval/retrieval_robustness_dev_v1.json'
    suite = json.loads(question_path.read_text())
    os.environ.update(PHARMACOPOEIA_INDEX=str(ROOT/'var/mineru_retrieval_v2'),
        LOCAL_DENSE_MODEL=str(ROOT/'var/evidence_assistant_v1/local_bge_small_zh'),
        LOCAL_DENSE_CACHE=str(ROOT/'var/evidence_assistant_v1/local_dense_cache'),
        LOCAL_QDRANT_GENERATION=str(ROOT/'var/qdrant_research_v1/generation'))
    import socket
    def deny(*args, **kwargs):
        raise RuntimeError('Evaluation network disabled')
    for key in ('connect', 'connect_ex', 'sendto'):
        setattr(socket.socket, key, deny)
    socket.getaddrinfo = deny
    r = ResearchRetriever(os.environ['PHARMACOPOEIA_INDEX'])
    rows = []
    try:
        if suite['source_view_sha256'] != r.receipt['view_sha256']:
            raise ValueError('Reference source version mismatch')
        canonical = {row['chunk_id']: r.hit(row) for row in r.connection.execute('SELECT * FROM chunks')}
        for case in suite['cases']:
            if any(i not in canonical or canonical[i]['name'] != case['herb'] for i in case['expected_chunk_ids']):
                raise ValueError('Reference target absent')
            for method in ('bm25', 'hybrid', 'hybrid_rerank'):
                os.environ['LOCAL_RERANKER_MODEL'] = str(args.model.resolve()) if method == 'hybrid_rerank' else ''
                start = time.monotonic()
                result = r.query(case['question'], method='name_bm25', k=6) if method == 'bm25' else text_search(case['herb'], case['question'])
                elapsed = (time.monotonic()-start)*1000
                row = dict(id=case['id'], group=case['group'], method=method,
                           status=result['status'], ms=round(elapsed, 2),
                           retrieval=result.get('retrieval'), **assess(case, result, canonical))
                rows.append(row)
                print(json.dumps({k: row[k] for k in ('id','method','failure')}, ensure_ascii=False), flush=True)
    finally:
        r.close()
    summary = {}
    for method in ('bm25', 'hybrid', 'hybrid_rerank'):
        selected = [x for x in rows if x['method'] == method]
        summary[method] = {'total': len(selected), 'passed': sum(x['passed'] for x in selected),
            'failures': dict(Counter(x['failure'] for x in selected if x['failure'])),
            'covered_targets': sum(x['covered_targets'] for x in selected),
            'expected_targets': sum(x['expected_targets'] for x in selected),
            'mean_ms': round(statistics.mean(x['ms'] for x in selected), 2),
            'groups': {g: {'total': sum(x['group']==g for x in selected),
                          'passed': sum(x['group']==g and x['passed'] for x in selected)}
                       for g in sorted({x['group'] for x in selected})},
            'reranker_used': sum(isinstance(x['retrieval'],dict) and isinstance(x['retrieval'].get('reranker'),dict) and x['retrieval']['reranker'].get('status')=='used' for x in selected)}
    report = {'evaluation_type':suite['split'], 'external_model_calls':0,
              'question_sha256':hashlib.sha256(question_path.read_bytes()).hexdigest(),
              'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'source_view_sha256':suite['source_view_sha256'], 'summary':summary,'cases':rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
