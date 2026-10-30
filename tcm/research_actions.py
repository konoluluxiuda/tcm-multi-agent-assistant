"""Validate an entire model action batch before any business tool is dispatched."""
import json
from tcm.research_business_tools import SPECS,maximum_cost
from tcm.research_evidence_read import NAME as READ_EVIDENCE, ReadEvidence


class ActionRejected(ValueError):
    pass


def action_key(name, args):
    return json.dumps([name, args], sort_keys=True, ensure_ascii=False)


def admit(calls, state, scope, allowed, finish_schema, tool_limit):
    def reject(reason):
        raise ActionRejected(reason)
    if not 1 <= len(calls) <= 4:
        reject('invalid_model_action')
    ids = {e['call_id'] for e in state['events']}
    keys = set(state['seen'])
    admitted = []
    for call in calls:
        identifier = call.get('id')
        if not isinstance(identifier, str) or not identifier:
            reject('invalid_call_id')
        if identifier in ids:
            reject('duplicate_call_id')
        ids.add(identifier)
        name = call.get('name')
        if name not in (*allowed, READ_EVIDENCE, 'finish_research'):
            reject('unknown_tool')
        if name == 'finish_research' and len(calls) != 1:
            reject('mixed_finish_batch')
        schema = finish_schema if name == 'finish_research' else ReadEvidence if name == READ_EVIDENCE else SPECS[name][0]
        try:
            args = schema.model_validate(call.get('args')).model_dump()
        except Exception:
            reject('invalid_finish' if name == 'finish_research' else 'invalid_arguments')
        if scope and name in allowed and not scope.permits(name, args, state['events'], state['evidence']):
            reject('out_of_scope')
        key = action_key(name, args)
        if name != 'finish_research' and key in keys:
            reject('duplicate_action')
        keys.add(key)
        admitted.append({**call, 'args': args})
    # Prevent ordered pages being treated as independent reads without a scope.
    if len(calls) > 1:
        profiles = [(c['name'], c['args']['name']) for c in admitted
                    if c['name'] in ('disease_gene_profile', 'search_disease_sources')]
        if len(profiles) != len(set(profiles)):
            reject('dependent_batch')
    if state['tool_calls'] + sum(maximum_cost(c['name'],c['args']) for c in admitted if c['name']!='finish_research') > tool_limit:
        reject('tool_budget')
    return admitted
