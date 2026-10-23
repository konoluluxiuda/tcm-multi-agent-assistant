"""Label-free ranking diagnostics; these metrics do not measure prediction accuracy."""
from collections import Counter
from fractions import Fraction
from itertools import combinations
from statistics import mean, median


def ranked_ids(query, herbs, metric, top_k=10):
    if metric not in ('jaccard', 'common_count', 'size_only'):
        raise ValueError('Unknown metric')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError('Invalid top_k')
    rows = []
    for sid, targets in herbs.items():
        if not targets:
            continue
        if metric == 'size_only':
            rows.append(((-len(targets), sid), sid))
            continue
        shared = len(query & targets)
        if shared:
            score = Fraction(shared, len(query) + len(targets) - shared)
            key = (-score, -shared) if metric == 'jaccard' else (-shared, -score)
            rows.append(((*key, sid), sid))
    return [sid for _, sid in sorted(rows)[:top_k]]


def overlap_at_k(left, right, top_k):
    """Fixed denominator k, including short/empty lists; never labelled recall."""
    return len(set(left) & set(right)) / top_k


def distribution(values):
    values = list(values)
    return {'count': len(values), 'mean': mean(values) if values else None,
            'median': median(values) if values else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def summarize(rows, herbs, top_k):
    baseline = ranked_ids(set(), herbs, 'size_only', top_k)
    report = {'eligible_herb_target_counts': distribution(len(s) for s in herbs.values() if s),
              'size_only_target_counts': distribution(len(herbs[s]) for s in baseline),
              'methods': {}}
    for method in ('jaccard', 'common_count'):
        lists = [r[method] for r in rows]
        firsts = Counter(ids[0] for ids in lists if ids)
        appearances = Counter(sid for ids in lists for sid in ids)
        report['methods'][method] = {
            'queries': len(rows), 'empty_results': sum(not ids for ids in lists),
            'short_results': sum(len(ids) < top_k for ids in lists),
            'overlap_with_size_only_at_k': distribution(overlap_at_k(ids, baseline, top_k) for ids in lists),
            'pairwise_overlap_at_k': distribution(overlap_at_k(a, b, top_k) for a, b in combinations(lists, 2)),
            'selected_target_counts': distribution(len(herbs[s]) for ids in lists for s in ids),
            'distinct_top1_sources': len(firsts), 'distinct_topk_sources': len(appearances),
            'most_frequent_top1': firsts.most_common(5),
            'most_frequent_topk': appearances.most_common(5),
        }
    report['between_methods_overlap_at_k'] = distribution(
        overlap_at_k(r['jaccard'], r['common_count'], top_k) for r in rows)
    return report
