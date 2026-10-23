"""Uniform, size-matched target-set null; diagnostic, not efficacy inference."""
import math
import random

from tcm.ranking_diagnostics import distribution, ranked_ids


def overlap_moments(population, disease_size, herb_size):
    """Mean/variance of overlap under sampling without replacement."""
    if (any(type(n) is not int or n < 0 for n in (population, disease_size, herb_size))
            or max(disease_size, herb_size) > population):
        raise ValueError('Invalid set sizes')
    if population == 0:
        return 0.0, 0.0
    mean = disease_size * herb_size / population
    variance = (disease_size * (herb_size / population) * (1 - herb_size / population)
                * (population - disease_size) / (population - 1)) if population > 1 else 0.0
    return mean, variance


def prepare_background(herbs):
    universe = sorted(set().union(*herbs.values()))
    bit = {uid: 1 << i for i, uid in enumerate(universe)}
    ids = sorted(sid for sid, targets in herbs.items() if targets)
    return {'universe': universe, 'bits': bit, 'ids': ids,
            'masks': [sum(bit[uid] for uid in herbs[sid]) for sid in ids]}


def nearest_rank_95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


def diagnose(query, herbs, *, iterations=256, seed=20261023, top_k=10, background=None):
    if type(iterations) is not int or not 16 <= iterations <= 1024:
        raise ValueError('iterations must be between 16 and 1024')
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError('Invalid top_k')
    bg = prepare_background(herbs) if background is None else background
    universe = bg['universe']; ids = bg['ids']; masks = bg['masks']; bit = bg['bits']
    inside = query & set(universe)
    observed_mask = sum(bit[uid] for uid in inside)
    observed = [(mask & observed_mask).bit_count() for mask in masks]
    methods = {m: ranked_ids(query, herbs, m, top_k) for m in ('jaccard', 'common_count')}
    selected = set().union(*methods.values())
    indices = {sid: i for i, sid in enumerate(ids) if sid in selected}
    simulated = {sid: [] for sid in selected}
    maxima = []
    rng = random.Random(seed)
    for _ in range(iterations):
        draw = rng.sample(universe, len(inside))
        mask = sum(bit[uid] for uid in draw)
        counts = [(mask & candidate).bit_count() for candidate in masks]
        maxima.append(max(counts, default=0))
        for sid, i in indices.items():
            simulated[sid].append(counts[i])
    details = {}
    for sid in sorted(selected):
        shared = observed[indices[sid]]
        expected, variance = overlap_moments(len(universe), len(inside), len(herbs[sid]))
        details[sid] = {
            'shared_count': shared, 'herb_target_count': len(herbs[sid]),
            'expected_shared_count': expected, 'null_variance': variance,
            'observed_minus_expected': shared - expected,
            'standardized_excess': (shared - expected) / math.sqrt(variance) if variance > 0 else None,
            'observed_to_expected': shared / expected if expected > 0 else None,
            'simulated_shared_counts': distribution(simulated[sid]),
            'simulated_ge_observed': sum(x >= shared for x in simulated[sid]),
        }
    maximum = max(observed, default=0)
    return {
        'query_target_count': len(query), 'background_size': len(universe),
        'query_background_count': len(inside), 'query_outside_background_count': len(query - set(universe)),
        'iterations': iterations, 'seed': seed,
        'status': 'evaluated' if inside else 'no_background_overlap',
        'methods': methods, 'selected_candidates': details,
        'observed_max_overlap': maximum,
        'null_max_overlap': distribution(maxima),
        'null_max_overlap_p95': nearest_rank_95(maxima),
        'null_max_ge_observed': sum(x >= maximum for x in maxima),
        'null_maxima': maxima,
    }
