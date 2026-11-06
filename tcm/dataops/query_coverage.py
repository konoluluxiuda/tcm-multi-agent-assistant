"""Compare recognized field requirements with a query's actual field coverage.

This is a bounded host constraint, not general semantic verification. A fully
consumed local field contract resolves shared labels (e.g. ETCM 性味); otherwise
only explicit requirements are enforced. No new wording whitelist is introduced.
"""
from tcm.dataops.diagnostics import record, reject


def validate_query_fields(intent, question, catalog):
    if intent.action not in ('herb_profile', 'herb_field', 'research', 'compare'):
        return  # Plans retain their per-object/per-clause coverage validator.
    from tcm.dataops.research_question import explicit_requirements
    from tcm.question_contract import field_tasks
    from tcm.intent_planner import HERB_FIELDS
    positive, negative = explicit_requirements(question, catalog)
    local = field_tasks(question)
    if local:
        positive = {'attributes': set(), 'document_fields': set()}
        for task in local:
            namespace = 'attributes' if task['tool'] == 'herb_attributes' else 'document_fields'
            positive[namespace].update(task['arguments']['fields'])
    provided = {'attributes': set(), 'document_fields': set()}
    if intent.action == 'herb_profile':
        provided['attributes'].update(HERB_FIELDS)
    elif intent.action == 'herb_field':
        provided['attributes'].update(intent.fields)
    else:
        provided['document_fields'].update(intent.fields)
    # Shared labels use the same default as templates and model instructions.
    # Keep this fallback bounded; explicit sources and negations retain guards.
    import re
    shared = '性味' in question or '归经' in question
    explicit_source = re.search(r'药典|ETCM', question, re.I)
    if shared and not explicit_source and not any(negative.values()):
        from tcm.dataops.field_sources import DEFAULTS
        attrs = {name for label in ('性味', '归经') if label in question
                 for name in DEFAULTS[label][1]}
        positive['attributes'].difference_update(attrs)
        positive['document_fields'].discard('性味与归经')
        positive['attributes'].update(attrs)
    missing = {key: sorted(positive[key] - provided[key]) for key in provided}
    forbidden = {key: sorted(negative[key] & provided[key]) for key in provided}
    record('query_field_coverage', 'rejected' if any(missing.values()) or any(forbidden.values()) else 'accepted',
           action=intent.action, missing=missing, forbidden=forbidden,
           basis='complete_local_field_contract' if local else 'recognized_explicit_requirements')
    if any(missing.values()) or any(forbidden.values()):
        code = ('query_routed_to_wrong_tool' if intent.action in ('herb_profile', 'herb_field')
                and positive['document_fields'] and not positive['attributes'] else 'query_requirement_omitted')
        reject(code, 'Query does not cover the requested source fields', missing=missing, forbidden=forbidden)
