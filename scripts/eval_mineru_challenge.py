"""Freeze corpus-proposed references, then measure a separately frozen challenge."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time

from tcm.mineru_challenge import score
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def prepare(questions, view, freeze, output):
    receipt_path = freeze.with_name('reference_freeze.json')
    if output.exists() or receipt_path.exists():
        raise FileExistsError('Use a fresh reference file')
    frozen = read(freeze)
    if digest(questions) != frozen['question_sha256']:
        raise ValueError('Questions differ from frozen snapshot')
    entries = read(view)['entries']
    refs = []
    for case in read(questions)['cases']:
        if case['group'] == 'out_of_scope':
            continue
        matches = [e for e in entries if e['name_zh_candidate'] == case['expected_name']]
        if not matches and case['group'] == 'processed_variant':
            refs.append({'case_id': case['id'], 'kind': 'variant_scope_guard',
                         'reason': 'No standalone exact-name entry; do not imply parent field resolves processed scope.'})
            continue
        if len(matches) != 1:
            raise ValueError('Cannot resolve unique reference: ' + case['id'])
        entry = matches[0]
        targets = [{'chunk_id': f"{entry['id']}:field:{i:03}", 'evidence': field['evidence'],
                    'scope': field['scope']} for i, field in enumerate(entry['fields'])
                   if field['label'] == case['expected_field'] and not field['scope'].startswith('appendix:')]
        if not targets:
            raise ValueError('No proposed field: ' + case['id'])
        refs.append({'case_id': case['id'], 'kind': 'corpus_proposed_evidence',
                     'name': entry['name_zh_candidate'], 'targets': targets,
                     'title_evidence': entry['title_evidence']})
    result = {'question_sha256': digest(questions), 'view_sha256': digest(view),
              'reference_status': 'corpus_proposed_not_independent_gold', 'human_signoff': False,
              'limits': ['Source spans are proposed from the same parsed corpus used by retrieval.',
                         'Title/field spotchecks do not certify complete OCR or clinical correctness.',
                         'Variant guards test non-substitution behavior, not medical dose equivalence.'],
              'references': refs}
    write(output, result)
    write(receipt_path, {'question_sha256': digest(questions), 'reference_sha256': digest(output),
                         'source_view_sha256': digest(view), 'retrieval_outputs_inspected': False,
                         'human_gold': False})
    return result


def evaluate(questions, references, freeze, index, output):
    if output.exists():
        raise FileExistsError('Use a fresh evaluation directory')
    q, refs, frozen = read(questions), read(references), read(freeze)
    if q['k'] != 5 or len({c['id'] for c in q['cases']}) != len(q['cases']):
        raise ValueError('Invalid frozen challenge recipe')
    receipt = read(freeze.with_name('reference_freeze.json'))
    if receipt['reference_sha256'] != digest(references):
        raise ValueError('References differ from frozen snapshot')
    if digest(questions) != frozen['question_sha256'] or refs['question_sha256'] != digest(questions):
        raise ValueError('Question fingerprint mismatch')
    if digest(ROOT / 'tcm/mineru_retrieval.py') != frozen['retriever_sha256']:
        raise ValueError('Retriever changed after question freeze; use a new experiment')
    guards = ROOT / 'tcm/mineru_guards.py'
    if frozen.get('guards_sha256') and digest(guards) != frozen['guards_sha256']:
        raise ValueError('Guards changed after regression freeze')
    retriever = ResearchRetriever(index)
    records, outputs = [], []
    by_id = {r['case_id']: r for r in refs['references']}
    try:
        if retriever.receipt['index_sha256'] != frozen['index_sha256'] or retriever.receipt['view_sha256'] != refs['view_sha256']:
            raise ValueError('Index/reference snapshot mismatch')
        for method in ('bm25', 'name_field'):
            for case in q['cases']:
                start = time.perf_counter()
                result = retriever.query(case['question'], method=method, k=5)
                elapsed = (time.perf_counter() - start) * 1000
                records.append({**score(case, by_id.get(case['id']), result), 'method': method,
                                'query_ms': elapsed})
                outputs.append({'case_id': case['id'], **result})
    finally:
        retriever.close()
    groups = {}
    for method in ('bm25', 'name_field'):
        groups[method] = {}
        for group in sorted({c['group'] for c in q['cases']}):
            rows = [r for r in records if r['method'] == method and r['group'] == group]
            positive = [r for r in rows if r['kind'] == 'corpus_proposed_evidence']
            behavior = [r for r in rows if 'behavior_passed' in r]
            groups[method][group] = {'total': len(rows), 'evidence_cases': len(positive),
                'target_hit_at_5_count': sum(r['target_evidence_hit_at_5'] for r in positive),
                'target_top1_count': sum(r['target_top1'] for r in positive),
                'mrr_at_5': statistics.mean(r['reciprocal_rank'] for r in positive) if positive else None,
                'behavior_cases': len(behavior), 'behavior_passed': sum(r['behavior_passed'] for r in behavior),
                'status_counts': dict(Counter(r['status'] for r in rows))}
    report = {'status': 'completed_with_observed_limitations', 'questions': len(q['cases']),
              'question_sha256': digest(questions), 'reference_sha256': digest(references),
              'index_sha256': frozen['index_sha256'], 'retriever_sha256': frozen['retriever_sha256'],
              'guards_sha256': digest(guards) if guards.exists() else None,
              'evaluation_code_sha256': digest(Path(__file__)),
              'scoring_code_sha256': digest(ROOT / 'tcm/mineru_challenge.py'),
              'human_gold': False, 'clinical_correctness_evaluated': False,
              'external_calls': 0, 'llm_calls': 0, 'embedding_calls': 0,
              'limits': refs['limits'], 'groups': groups, 'cases': records}
    output.mkdir(parents=True)
    write(output / 'evaluation.json', report)
    write(output / 'query_results.json', outputs)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'evaluate'))
    parser.add_argument('--questions', type=Path, default=ROOT / 'eval/mineru_retrieval_challenge_v1.json')
    parser.add_argument('--references', type=Path, default=ROOT / 'var/mineru_challenge_v1/reference.json')
    parser.add_argument('--freeze', type=Path, default=ROOT / 'audits/mineru_challenge_v1/freeze.json')
    parser.add_argument('--view', type=Path, default=ROOT / 'var/mineru_revisions_v2/research_view.json')
    parser.add_argument('--index', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_challenge_v1/results')
    args = parser.parse_args()
    if args.action == 'prepare':
        args.references.parent.mkdir(parents=True, exist_ok=True)
        result = prepare(args.questions, args.view, args.freeze, args.references)
        print(json.dumps({'references': len(result['references']), 'human_gold': False}))
    else:
        result = evaluate(args.questions, args.references, args.freeze, args.index, args.out)
        print(json.dumps(result['groups'], ensure_ascii=False))
