"""Prepare exhaustive field-location references, then measure local lexical recall.

Relevance means every nonblank, non-appendix field chunk of the specified source
entry/labels. It does NOT mean every clinically relevant passage or a safe answer.
Preparation reads the view directly, never the tested retriever's output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import time

from scripts.build_mineru_pharmacopoeia import read, write
from tcm.mineru_guards import field_body, reference_target
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import METHODS, PARAMETERS, ResearchRetriever

ROOT = Path(__file__).resolve().parents[1]
CODE = ('scripts/eval_scoped_recall.py', 'tcm/mineru_retrieval.py',
        'tcm/mineru_guards.py', 'tcm/mineru_pharmacopoeia.py',
        'scripts/build_mineru_pharmacopoeia.py')
POLICY = 'all_nonblank_nonappendix_chunks_for_exact_entry_and_labels_v1'


def references(suite, view):
    if type(suite.get('k')) is not int or not 1 <= suite['k'] <= PARAMETERS['max_k']:
        raise ValueError('Invalid k')
    cases = suite.get('cases', [])
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Cases must be nonempty and have unique IDs')
    by_name = {}
    for entry in view['entries']:
        by_name.setdefault(entry['name_zh_candidate'], []).append(entry)
    result = []
    for case in cases:
        if not isinstance(case['question'], str) or not 0 < len(case['question']) <= 500:
            raise ValueError('Invalid question')
        if case['group'] == 'boundary':
            if not case.get('allowed_statuses'):
                raise ValueError('Boundary case requires explicit allowed statuses')
            result.append({'case_id': case['id'], 'relevant_ids': [], 'locations': []})
            continue
        entries = by_name.get(case['name'], [])
        if len(entries) != 1:
            raise ValueError('Reference name must identify exactly one source entry')
        labels = case['labels']
        if not labels or len(set(labels)) != len(labels):
            raise ValueError('Specify distinct expected labels')
        entry = entries[0]
        locations = []
        for i, field in enumerate(entry['fields']):
            if (field['label'] not in labels or field['scope'].startswith('appendix:')
                    or not field['text'].strip()):
                continue
            locations.append({
                'chunk_id': f"{entry['id']}:field:{i:03}", 'label': field['label'],
                'scope': field['scope'], 'evidence': field['evidence'],
                'text_sha256': hashlib.sha256(field['text'].encode()).hexdigest(),
                'reference_or_empty_body': bool(reference_target(field['text']) or not field_body(field['text'])),
            })
        if set(labels) != {row['label'] for row in locations}:
            raise ValueError('Expected label missing from searchable source; use a boundary case')
        result.append({'case_id': case['id'], 'entry_id': entry['id'],
                       'relevant_ids': [row['chunk_id'] for row in locations],
                       'locations': locations})
    return result


def prepare(questions, view_path, index, output):
    if output.exists():
        raise FileExistsError('Use a fresh preparation directory')
    suite, view = read(questions), read(view_path)
    refs = references(suite, view)
    retriever = ResearchRetriever(index)
    try:
        if retriever.receipt['view_sha256'] != digest(view_path):
            raise ValueError('Index/view mismatch')
        index_sha = retriever.receipt['index_sha256']
    finally:
        retriever.close()
    output.mkdir(parents=True)
    shutil.copyfile(questions, output / 'questions.json')
    write(output / 'references.json', {'policy': POLICY, 'references': refs})
    code_hashes = {}
    for name in CODE:
        target = output / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        code_hashes[name] = digest(target)
    manifest = {'kind': 'scoped_field_location_recall_v1', 'policy': POLICY,
                'questions_sha256': digest(output / 'questions.json'),
                'references_sha256': digest(output / 'references.json'),
                'view_sha256': digest(view_path), 'index_sha256': index_sha,
                'code_sha256': code_hashes, 'human_gold': False,
                'split': 'author_designed_source_assisted_diagnostic_not_independent',
                'cases': len(refs), 'clinical_correctness_evaluated': False}
    write(output / 'manifest.json', manifest)
    return manifest


def score_ids(hit_ids, relevant_ids, k):
    if len(hit_ids) != len(set(hit_ids)):
        raise ValueError('Duplicate retrieved IDs must not inflate recall')
    relevant = set(relevant_ids)
    if not relevant:
        raise ValueError('Recall requires a nonempty explicit denominator')
    selected = hit_ids[:k]
    found = relevant.intersection(selected)
    rank = next((i for i, item in enumerate(selected, 1) if item in relevant), None)
    return {'relevant_total': len(relevant), 'relevant_retrieved': len(found),
            'hit_at_k': bool(found), 'recall_at_k': len(found) / len(relevant),
            'reciprocal_rank_at_k': 1 / rank if rank else 0,
            'missing_ids': sorted(relevant - found)}


def evaluate(prepared, view_path, index, output, methods=METHODS):
    if output.exists():
        raise FileExistsError('Use a fresh result directory')
    if not methods or len(set(methods)) != len(methods) or any(m not in METHODS for m in methods):
        raise ValueError('Select distinct supported methods')
    manifest = read(prepared / 'manifest.json')
    for name, key in [('questions.json', 'questions_sha256'), ('references.json', 'references_sha256')]:
        if digest(prepared / name) != manifest[key]:
            raise ValueError('Prepared inputs changed')
    if manifest['policy'] != POLICY or digest(view_path) != manifest['view_sha256']:
        raise ValueError('Reference policy/view changed')
    for name in CODE:
        if digest(ROOT / name) != manifest['code_sha256'][name]:
            raise ValueError('Evaluation code changed; prepare a new version')
    suite, view = read(prepared / 'questions.json'), read(view_path)
    refs = read(prepared / 'references.json')['references']
    # Re-enumeration protects against an incomplete or hand-picked denominator.
    if refs != references(suite, view):
        raise ValueError('References do not exhaust the declared source scope')
    by_id = {r['case_id']: r for r in refs}
    source = {f"{e['id']}:field:{i:03}": (e, f)
              for e in view['entries'] for i, f in enumerate(e['fields'])}
    retriever = ResearchRetriever(index)
    rows, raw = [], []
    try:
        if (retriever.receipt['index_sha256'] != manifest['index_sha256']
                or retriever.receipt['view_sha256'] != manifest['view_sha256']):
            raise ValueError('Frozen index changed')
        for method in methods:
            for case in suite['cases']:
                start = time.perf_counter()
                result = retriever.query(case['question'], method=method, k=suite['k'])
                elapsed = (time.perf_counter() - start) * 1000
                hits = result['hits']
                valid = True
                for hit in hits + result.get('unresolved_evidence', []):
                    entry, field = source[hit['chunk_id']]
                    valid &= (hit['entry_id'] == entry['id']
                              and hit['name'] == entry['name_zh_candidate']
                              and all(hit[key] == field[key] for key in
                                      ('label', 'scope', 'text', 'raw_text', 'evidence')))
                row = {'case_id': case['id'], 'group': case['group'], 'method': method,
                       'status': result['status'], 'query_ms': elapsed,
                       'provenance_consistent_with_view': bool(valid),
                       'hit_ids': [h['chunk_id'] for h in hits],
                       'unresolved_ids': [h['chunk_id'] for h in result.get('unresolved_evidence', [])]}
                if case['group'] == 'boundary':
                    row['correct_boundary'] = (result['status'] in case['allowed_statuses']
                                               and not hits and not row['unresolved_ids'])
                else:
                    ref = by_id[case['id']]
                    row.update(score_ids(row['hit_ids'], ref['relevant_ids'], suite['k']))
                    interpretation = result['query_interpretation']
                    row.update(
                        entity_selection_correct=(
                            {v['indexed_name'] for v in interpretation['name_forms']} == {case['name']})
                            if method != 'bm25' else None,
                        field_selection_correct=(
                            {v['field'] for v in interpretation['field_matches']} == set(case['labels']))
                            if method != 'bm25' else None,
                        wrong_entity_hits=sum(h['entry_id'] != ref['entry_id'] for h in hits),
                        other_field_hits=sum(h['label'] not in case['labels'] for h in hits),
                        appendix_hits=sum(h['scope'].startswith('appendix:') for h in hits),
                        source_reference_or_empty_count=sum(v['reference_or_empty_body'] for v in ref['locations']),
                    )
                rows.append(row)
                raw.append({'case_id': case['id'], **result})
    finally:
        retriever.close()
    groups = {}
    for method in methods:
        positives = [r for r in rows if r['method'] == method and r['group'] != 'boundary']
        boundaries = [r for r in rows if r['method'] == method and r['group'] == 'boundary']
        groups[method] = {
            'retrieval_cases': len(positives), 'boundary_cases': len(boundaries),
            'hit_at_k': statistics.mean(r['hit_at_k'] for r in positives) if positives else None,
            'macro_recall_at_k': statistics.mean(r['recall_at_k'] for r in positives) if positives else None,
            'mrr_at_k': statistics.mean(r['reciprocal_rank_at_k'] for r in positives) if positives else None,
            'entity_selection_correct': sum(r['entity_selection_correct'] is True for r in positives) if method != 'bm25' else None,
            'field_selection_correct': sum(r['field_selection_correct'] is True for r in positives) if method != 'bm25' else None,
            'correct_boundaries': sum(r['correct_boundary'] for r in boundaries),
            'wrong_entity_hits': sum(r['wrong_entity_hits'] for r in positives),
            'other_field_hits': sum(r['other_field_hits'] for r in positives),
            'provenance_failures': sum(not r['provenance_consistent_with_view'] for r in rows if r['method'] == method),
            'mean_query_ms': statistics.mean(r['query_ms'] for r in rows if r['method'] == method),
        }
    report = {'status': 'completed_diagnostic_not_acceptance', 'manifest': manifest,
              'k': suite['k'], 'methods': list(methods), 'results': groups, 'cases': rows,
              'llm_calls': 0, 'embedding_calls': 0,
              'limits': ['Source-derived field-location relevance, not semantic or clinical relevance.',
                         'Repeated scopes are retained; locating all is not resolving preparation identity.',
                         'Unresolved references remain in the denominator but are not successful evidence hits.',
                         'AI-authored diagnostic; not independent gold or end-to-end chat task success.',
                         'Single sequential in-process query timing; no P95, concurrency or model cost claim.']}
    if digest(view_path) != manifest['view_sha256'] or digest(index / 'research.sqlite3') != manifest['index_sha256']:
        raise ValueError('Source changed during evaluation')
    output.mkdir(parents=True)
    write(output / 'evaluation.json', report)
    write(output / 'query_results.json', raw)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run'))
    parser.add_argument('--questions', type=Path, default=ROOT / 'eval/scoped_recall_v1.json')
    parser.add_argument('--view', type=Path, default=ROOT / 'var/mineru_revisions_v2/research_view.json')
    parser.add_argument('--index', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    parser.add_argument('--prepared', type=Path, default=ROOT / 'var/scoped_recall_v1/prepared_01')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/scoped_recall_v1/run_01')
    parser.add_argument('--methods', nargs='+', choices=METHODS, default=list(METHODS))
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(args.questions, args.view, args.index, args.prepared)
    else:
        result = evaluate(args.prepared, args.view, args.index, args.out, args.methods)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
