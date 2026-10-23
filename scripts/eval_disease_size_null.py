"""Run a frozen-cohort size-matched null locally; never changes chat rankings."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
from time import monotonic

from scripts.eval_disease_ranking import read_inputs, write_json
from tcm.ranking_diagnostics import distribution
from tcm.target_similarity import file_token, SnapshotChanged
from tcm.target_size_null import diagnose, prepare_background

MAX_COMPARISONS = 100_000_000


def load_baseline(directory):
    protocol_bytes = (Path(directory) / 'protocol.json').read_bytes()
    summary_bytes = (Path(directory) / 'summary.json').read_bytes()
    protocol, summary = json.loads(protocol_bytes), json.loads(summary_bytes)
    sha = hashlib.sha256(protocol_bytes).hexdigest()
    if (protocol['version'] != 'disease_ranking_diagnostics_v1' or summary['status'] != 'completed'
            or summary['protocol_sha256'] != sha or summary['sources'] != protocol['sources']
            or summary['sample_size'] != len(protocol['cohort']) or summary['top_k'] != protocol['top_k']):
        raise ValueError('Baseline protocol and summary differ')
    if (type(protocol['requested_sample_size']) is not int or not 2 <= protocol['requested_sample_size'] <= 512
            or type(protocol['top_k']) is not int or not 1 <= protocol['top_k'] <= 20):
        raise ValueError('Invalid baseline limits')
    return protocol, sha, hashlib.sha256(summary_bytes).hexdigest()


def summarize(rows):
    evaluated = [r for r in rows if r['status'] == 'evaluated']
    result = {
        'evaluated_queries': len(evaluated), 'queries_without_background_overlap': len(rows) - len(evaluated),
        'observed_max_overlap': distribution(r['observed_max_overlap'] for r in evaluated),
        'null_max_overlap_mean': distribution(r['null_max_overlap']['mean'] for r in evaluated),
        'queries_above_null_max_p95': sum(r['observed_max_overlap'] > r['null_max_overlap_p95'] for r in evaluated),
        'queries_above_all_simulated_maxima': sum(r['null_max_ge_observed'] == 0 for r in evaluated),
        'methods': {},
    }
    for method in ('jaccard', 'common_count'):
        candidates = [r['selected_candidates'][sid] for r in evaluated for sid in r['methods'][method]]
        result['methods'][method] = {
            'selected_positions': len(candidates),
            **{field: distribution(c[field] for c in candidates if c[field] is not None) for field in (
                'shared_count', 'expected_shared_count', 'observed_minus_expected', 'standardized_excess', 'observed_to_expected')},
            'positions_above_expected_overlap': sum(c['observed_minus_expected'] > 0 for c in candidates),
        }
    return result


def evaluate(base, enrichment, diseases, baseline, out, *, iterations=256, seed=20261023):
    if type(iterations) is not int or not 16 <= iterations <= 1024:
        raise ValueError('iterations must be between 16 and 1024')
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('seed must be an unsigned 64-bit integer')
    prior, prior_sha, prior_summary_sha = load_baseline(baseline)
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    paths = (base, enrichment, diseases)
    before = [file_token(p) for p in paths]
    herbs, queries, cohort, sources, _ = read_inputs(*paths, prior['requested_sample_size'])
    if sources != prior['sources'] or cohort != prior['cohort']:
        raise SnapshotChanged('Baseline cohort or sources differ')
    bg = prepare_background(herbs)
    comparisons = len(cohort) * iterations * len(bg['ids'])
    if comparisons > MAX_COMPARISONS:
        raise ValueError('Simulation exceeds 100 million candidate comparisons')
    implementation = {p: hashlib.sha256((Path(__file__).resolve().parents[1] / p).read_bytes()).hexdigest()
                      for p in ('scripts/eval_disease_size_null.py', 'tcm/target_size_null.py',
                                'scripts/eval_disease_ranking.py', 'tcm/ranking_diagnostics.py')}
    protocol = {
        'version': 'disease_size_null_v1', 'baseline_protocol_sha256': prior_sha,
        'baseline_summary_sha256': prior_summary_sha, 'cohort': cohort, 'sources': sources,
        'top_k': prior['top_k'], 'iterations_per_query': iterations, 'seed': seed,
        'per_query_seed': 'int(SHA256(str(seed)+colon+record_id),16)',
        'rng': 'random.Random.sample_sorted_universe_without_replacement', 'python': platform.python_version(),
        'null': 'fix_all_herb_sets_draw_uniform_disease_background_subset_of_same_size',
        'outside_background': 'excluded_from_draw_preserved_in_observed_ranking_and_counts',
        'selection_control': 'recompute_maximum_shared_count_across_entire_covered_herb_pool_each_draw',
        'p95': 'nearest_rank_ceil_0.95_times_iterations',
        'scope': 'exploratory_existing_development_cohort_no_independent_labels',
        'limitations': ['does_not_preserve_disease_target_frequencies', 'no_multiplicity_adjusted_significance_claims',
                        'pair_diagnostics_are_post_selection', 'no_efficacy_or_disease_specificity_validation'],
        'candidate_comparisons': comparisons, 'implementation_sha256': implementation,
    }
    write_json(out / 'protocol.json', protocol)
    rows = []
    for item in cohort:
        sid = item['record_id']
        query_seed = int(hashlib.sha256(f'{seed}:{sid}'.encode()).hexdigest(), 16)
        row = diagnose(queries[sid], herbs, iterations=iterations, seed=query_seed,
                       top_k=prior['top_k'], background=bg)
        rows.append({'record_id': sid, 'target_set_sha256': item['target_set_sha256'], **row})
    if before != [file_token(p) for p in paths] or load_baseline(baseline)[1:] != (prior_sha, prior_summary_sha):
        raise SnapshotChanged('Inputs changed during null evaluation')
    report = {
        'status': 'completed', 'scope': protocol['scope'],
        'protocol_sha256': hashlib.sha256((out / 'protocol.json').read_bytes()).hexdigest(),
        'baseline_protocol_sha256': prior_sha, 'sources': sources, 'sample_size': len(rows),
        'iterations_per_query': iterations, 'seed': seed, 'candidate_comparisons': comparisons,
        'diagnostics': summarize(rows), 'input_stat_tokens_unchanged': True,
        'predictive_validity': 'not_evaluated_no_independent_labels',
        'duration_seconds': round(monotonic() - started, 3),
    }
    write_json(out / 'queries.json', rows)
    write_json(out / 'summary.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'enrichment', 'diseases', 'baseline', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=256)
    parser.add_argument('--seed', type=int, default=20261023)
    args = parser.parse_args()
    result = evaluate(args.database, args.enrichment, args.diseases, args.baseline, args.out,
                      iterations=args.iterations, seed=args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
