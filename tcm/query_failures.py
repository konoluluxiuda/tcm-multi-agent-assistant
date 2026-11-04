"""Public reasons for ordinary local queries, based only on observed outcomes.

Do not infer a parser failure from an empty report-request herb slot: that slot
is intentionally empty for ordinary lexical retrieval. Boundary rejection is
reported only when the retriever's name guard actually rejected context.
"""
from tcm.dataops.failures import public_failure


REASONS={
    'not_found':('name_not_found','name_resolution'),
    'unsupported_name_mapping':('name_not_found','name_resolution'),
    'name_not_resolved':('name_not_found','name_resolution'),
    'ambiguous_entity':('ambiguous_source','name_resolution'),
    'name_mapping_candidates':('ambiguous_source','name_resolution'),
    'ambiguous_name':('ambiguous_source','name_resolution'),
    'clarify_source':('ambiguous_source','name_resolution'),
    'clarify_entity':('entity_required','name_resolution'),
    'multiple_names':('multiple_entities','name_resolution'),
    'variant_scope_unresolved':('preparation_scope_unresolved','name_resolution'),
    'field_not_resolved':('field_not_resolved','field_resolution'),
    'field_not_observed':('field_not_recorded','field_resolution'),
    'ambiguous_field':('ambiguous_field','field_resolution'),
    'reference_unresolved':('evidence_incomplete','evidence_validation'),
    'partial_evidence':('evidence_incomplete','evidence_validation'),
    'no_match':('evidence_not_found','retrieval'),
    'context_stale':('context_stale','context_validation'),
    'invalid_query':('query_parameters_invalid','query_mapping'),
    'empty_query':('query_parameters_invalid','query_mapping'),
    'clarify_comparison':('query_parameters_invalid','query_mapping'),
    'research_disabled':('tool_disabled','routing'),
    'research_unavailable':('source_unavailable','source_read'),
    'database_unavailable':('source_unavailable','source_read'),
    'dataset_not_found':('source_unavailable','source_read'),
}


def with_failure(result):
    status=result.get('status')
    reason=REASONS.get(status)
    if reason:
        data=result.get('tool_result') or {}
        interpretation=data.get('query_interpretation') or {}
        if status=='name_not_resolved' and interpretation.get('name_resolution')=='context_rejected':
            reason=('name_boundary_unresolved','name_resolution')
        if status=='name_mapping_candidates' and result.get('name_candidate_count',result.get('total_record_count'))==1:
            reason=('candidate_confirmation_required','name_resolution')
        result={**result,'failure':public_failure(*reason)}
        if status=='name_not_resolved':result['message']=result['failure']['message']
    return result
