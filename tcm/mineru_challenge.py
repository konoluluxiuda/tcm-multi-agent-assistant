"""Challenge scoring separates proposed evidence retrieval from scope behavior."""


def score(case, reference, result):
    hits = result['hits']
    row = {'case_id': case['id'], 'group': case['group'], 'status': result['status'],
           'hit_ids': [h['chunk_id'] for h in hits],
           'returned_names': sorted({h['name'] for h in hits})}
    if case['group'] == 'out_of_scope':
        return {**row, 'kind': 'abstention', 'expected_status': case['expected_status'],
                'behavior_passed': not hits and result['status'] == case['expected_status']}
    if reference['kind'] == 'variant_scope_guard':
        # A parent-name match is not evidence that a processed variant was resolved.
        return {**row, 'kind': 'variant_scope_guard',
                'behavior_passed': (not hits or result['status'] == 'ambiguous_field'),
                'unqualified_parent_substitution': bool(hits) and result['status'] != 'ambiguous_field'}
    expected = reference['targets']
    valid = {r['chunk_id'] for r in expected}
    ranks = [i for i, hit in enumerate(hits, 1) if hit['chunk_id'] in valid and hit['name'] == reference['name']
             and any(hit['chunk_id'] == r['chunk_id'] and hit['evidence'] == r['evidence'] for r in expected)]
    rank = min(ranks) if ranks else None
    return {**row, 'kind': 'corpus_proposed_evidence', 'rank': rank,
            'target_evidence_hit_at_5': rank is not None,
            'target_top1': rank == 1,
            'reciprocal_rank': 1 / rank if rank else 0,
            'returned_all_target_fields': valid.issubset({h['chunk_id'] for h in hits}),
            'reference_field_occurrences': len(expected)}
