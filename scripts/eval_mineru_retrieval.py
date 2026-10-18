"""Measure frozen lexical baselines; source-assisted AI references are not human gold."""
import argparse
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import time

from scripts.build_mineru_pharmacopoeia import read, write
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import PARAMETERS, ResearchRetriever

ROOT = Path(__file__).resolve().parents[1]


def anchor_matches(hit, reference):
    x0, y0, x1, y1 = reference['coarse_region_0_1000']
    for evidence in hit['evidence']:
        source = evidence['source'];box = evidence['bbox_0_1000']
        if (source['source_document'] == reference['source_document']
                and source['source_pdf_page'] == reference['source_pdf_page']
                and source['source_sha256'] == reference['source_sha256']
                and x0 <= (box[0] + box[2]) / 2 <= x1
                and y0 <= (box[1] + box[3]) / 2 <= y1):
            return True
    return False


def evaluate(index, questions, references, view_path, output):
    if output.exists():
        raise FileExistsError('Use a fresh evaluation output directory')
    q, refs, view = read(questions), read(references), read(view_path)
    if q.get('k') != 5:
        raise ValueError('This frozen evaluation recipe expects k=5')
    if refs['question_sha256'] != digest(questions):
        raise ValueError('Questions changed after reference preparation')
    ref_by_id = {r['case_id']: r for r in refs['references']}
    source = {f"{e['id']}:field:{i:03}": (e, f) for e in view['entries'] for i, f in enumerate(e['fields'])}
    started = time.perf_counter();retriever = ResearchRetriever(index)
    startup_ms = (time.perf_counter() - started) * 1000
    if digest(view_path) != retriever.receipt['view_sha256']:
        retriever.close();raise ValueError('Reference source view differs from index')
    records, raw_outputs, provenance_failures = [], [], []
    try:
        for method in ('bm25', 'name_field'):
            for case in q['cases']:
                result = retriever.query(case['question'], method=method, k=q['k'])
                timings = []
                for _ in range(3):
                    start = time.perf_counter();retriever.query(case['question'], method=method, k=q['k'])
                    timings.append((time.perf_counter() - start) * 1000)
                valid = True
                for hit in result['hits']:
                    entry, field = source[hit['chunk_id']]
                    check = (hit['entry_id'] == entry['id'] and hit['name'] == entry['name_zh_candidate']
                             and hit['text'] == field['text'] and hit['raw_text'] == field['raw_text']
                             and hit['evidence'] == field['evidence']
                             and hit['production_eligible'] is False)
                    valid &= check
                    if not check:provenance_failures.append({'case_id': case['id'], 'chunk_id': hit['chunk_id']})
                row = {'case_id': case['id'], 'group': case['group'], 'method': method,
                       'status': result['status'], 'hit_ids': [h['chunk_id'] for h in result['hits']],
                       'query_median_ms': statistics.median(timings), 'query_samples_ms': timings,
                       'provenance_consistent_with_view': valid}
                if case['group'] == 'abstention':
                    row['correct_abstention'] = not result['hits'] and result['status'] == case['expected_status']
                    row['expected_status'] = case['expected_status']
                else:
                    ref = ref_by_id[case['id']]
                    expected = f"{ref['entry_id']}:field:{ref['field_index']:03}"
                    rank = next((i for i, h in enumerate(result['hits'], 1) if h['chunk_id'] == expected), None)
                    anchor = any(h['chunk_id'] == expected and anchor_matches(h, ref) for h in result['hits'])
                    row.update(expected_chunk_id=expected, rank=rank, evidence_hit_at_k=rank is not None,
                               reference_anchor_hit_at_k=anchor,
                               reference_field_occurrences=ref['field_occurrences'],
                               top1_reference_hit=(rank == 1 and anchor),
                               unambiguous_top1_lookup=(rank == 1 and anchor and ref['field_occurrences'] == 1
                                                        and result['status'] == 'candidates'))
                records.append(row);raw_outputs.append({'case_id': case['id'], **result})
    finally:
        retriever.close()
    groups = {}
    for method in ('bm25', 'name_field'):
        groups[method] = {}
        for group in ('new_question_pilot', 'abstention', 'known_failure_regression'):
            rows = [r for r in records if r['method'] == method and r['group'] == group]
            if group == 'abstention':
                value = {'total': len(rows), 'correct_abstentions': sum(r['correct_abstention'] for r in rows)}
            else:
                value = {'total': len(rows), 'evidence_hit_at_5': sum(r['evidence_hit_at_k'] for r in rows) / len(rows),
                         'mrr_at_5': sum(1 / r['rank'] if r['rank'] else 0 for r in rows) / len(rows),
                         'reference_anchor_hit_at_5': sum(r['reference_anchor_hit_at_k'] for r in rows) / len(rows),
                         'top1_reference_hits': sum(r['top1_reference_hit'] for r in rows),
                         'unambiguous_top1_lookups': sum(r['unambiguous_top1_lookup'] for r in rows),
                         'ambiguous_fields': sum(r['status'] == 'ambiguous_field' for r in rows)}
            samples = sorted(t for r in rows for t in r['query_samples_ms'])
            value.update(mean_query_ms=statistics.mean(samples), p95_query_ms=samples[int(.95 * (len(samples) - 1))])
            groups[method][group] = value
    report = {'kind': 'mineru_lexical_retrieval_baseline', 'status': 'completed_with_observed_limitations',
              'parameters': PARAMETERS, 'questions_sha256': digest(questions), 'reference_sha256': digest(references),
              'index_sha256': retriever.receipt['index_sha256'], 'view_sha256': digest(view_path),
              'evaluation_code_sha256': digest(Path(__file__)),
              'index_startup_including_hash_ms': startup_ms, 'results': groups,
              'provenance_failures': provenance_failures, 'human_gold': False, 'clinical_correctness_evaluated': False,
              'external_calls': 0, 'llm_calls': 0, 'embedding_calls': 0, 'new_ocr_pages': 0,
              'latency_scope': 'warm in-process queries; 3 timed repetitions after initial query; excludes CLI startup and JSON printing',
              'runtime': {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version, 'os': platform.system()},
              'limitations': refs['limits'], 'cases': records}
    output.mkdir(parents=True)
    write(output / 'evaluation.json', report);write(output / 'query_results.json', raw_outputs)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    parser.add_argument('--questions', type=Path, default=ROOT / 'eval/mineru_retrieval_questions_v1.json')
    parser.add_argument('--references', type=Path, default=ROOT / 'eval/mineru_retrieval_reference_v2.json')
    parser.add_argument('--view', type=Path, default=ROOT / 'var/mineru_revisions_v2/research_view.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_retrieval_eval_v2')
    args = parser.parse_args()
    report = evaluate(args.index, args.questions, args.references, args.view, args.out)
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False))
