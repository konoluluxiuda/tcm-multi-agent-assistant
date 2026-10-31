"""Raw-anchor retrieval scoring, deliberately separate from the search API."""
import hashlib
import statistics

from tcm.mineru_pharmacopoeia import segment_text
from tcm.mineru_revisions import fingerprint


def anchor_in_hit(anchor, hit, blocks):
    if anchor['kind'] == 'etcm_json':
        return (hit.get('kind') == 'etcm_json' and
                all(hit.get(k) == anchor[k] for k in
                    ('source_record_id','relative_file_path','file_sha256','json_pointer','label')) and
                fingerprint(hit.get('raw_value')) == anchor['value_sha256'])
    if (hit.get('kind') != 'pdf_field' or
            any(hit.get(k) != anchor[k] for k in ('entry_id','label','scope'))):
        return False
    raw = blocks[anchor['block_id']]
    quote = segment_text(anchor, blocks)
    if (fingerprint(raw) != anchor['block_sha256'] or
            hashlib.sha256(quote.encode()).hexdigest() != anchor['text_sha256']):
        raise ValueError('Frozen original scoring evidence changed')
    if quote not in hit['text']:
        return False
    return any(e['block_id'] == anchor['block_id'] and
               e.get('source', {}).get('source_sha256') == anchor['source']['source_sha256'] and
               e.get('start',0) <= anchor['start'] and e.get('end',0) >= anchor['end'] and
               e.get('text_source') == anchor.get('text_source') and
               e.get('item_index') == anchor.get('item_index') for e in hit['evidence'])


def score(case, result, blocks):
    """A hit is a witnessed span, not an answer-level semantic correctness label."""
    anchors = case['relevant_anchors']
    docs = [a for a in anchors if a['kind'] == 'pdf_block']
    hits = result['hits']
    structured = result.get('structured_hits', [])
    doc_ranks = [i for i,h in enumerate(hits,1) if any(anchor_in_hit(a,h,blocks) for a in docs)]
    covered = [a['anchor_id'] for a in anchors if any(anchor_in_hit(a,h,blocks) for h in hits+structured)]
    # Contamination can occur inside an otherwise relevant field; do not require
    # that the wrong appended span share the scored field's declared label.
    excluded = []
    for a in case.get('excluded_anchors', []):
        if a['kind'] != 'pdf_block': continue
        quote = segment_text(a,blocks)
        if any(quote in h['text'] for h in hits): excluded.append(a['anchor_id'])
    expected_entries = {e['entry_id'] for e in case.get('expected_entities', [])}
    expected_scope = result.get('request', {}).get('scope')
    violations = [h['chunk_id'] for h in hits if
                  (expected_entries and h['entry_id'] not in expected_entries) or
                  h['scope'].startswith('appendix:') or (expected_scope and h['scope'] != expected_scope)]
    positive = case['expected_status'] == 'evidence_found'
    status_ok = result['status'] == case['expected_status']
    complete = len(covered) == len(anchors)
    passed = status_ok and not violations and not excluded and (not positive or complete)
    if not positive and (hits or structured): passed = False
    return {'case_id':case['case_id'], 'expected_status':case['expected_status'], 'status':result['status'],
            'positive':positive, 'status_correct':status_ok,
            'document_hit_at_5':bool(doc_ranks and doc_ranks[0] <= 5) if positive else None,
            'document_hit_at_10':bool(doc_ranks and doc_ranks[0] <= 10) if positive else None,
            'document_rr_at_10':1/doc_ranks[0] if positive and doc_ranks and doc_ranks[0] <= 10 else (0 if positive else None),
            'covered_anchor_ids':covered, 'required_anchor_count':len(anchors),
            'all_required_anchors_covered':complete if positive else None,
            'object_scope_violations':violations, 'excluded_span_leaks':excluded,
            'passed':passed, 'latency_ms':result['latency_ms'], 'error_type':result.get('error_type'),
            'scoring_limit':'raw_span_location_and_literal_coverage_not_generated_answer_support',
            'recall':None}


def summarize(rows):
    positives = [r for r in rows if r['positive']]
    def mean(key): return round(statistics.mean(float(r[key]) for r in positives),6) if positives else None
    return {'cases':len(rows), 'positive_cases':len(positives),
            'document_hit_at_5':mean('document_hit_at_5'),
            'document_hit_at_10':mean('document_hit_at_10'), 'document_mrr_at_10':mean('document_rr_at_10'),
            'all_required_anchor_coverage_rate':mean('all_required_anchors_covered'),
            'status_correct':sum(r['status_correct'] for r in rows),
            'contract_passed':sum(r['passed'] for r in rows),
            'errors':sum(bool(r.get('error_type')) for r in rows),
            'object_scope_violations':sum(len(r['object_scope_violations']) for r in rows),
            'excluded_span_leaks':sum(len(r['excluded_span_leaks']) for r in rows),
            'latency_mean_ms':round(statistics.mean(r['latency_ms'] for r in rows),3) if rows else None,
            'latency_max_ms':max((r['latency_ms'] for r in rows),default=None),
            'within_latency_budget':sum(r.get('within_latency_budget',True) for r in rows),
            'latency_budget_failed_case_ids':[r['case_id'] for r in rows if not r.get('within_latency_budget',True)],
            'failed_case_ids':[r['case_id'] for r in rows if not r['passed']], 'recall':None}
