"""User requirements independent of execution tools and workspace identifiers.

Agreement is evidence, not proof of semantic correctness. The model reviewer is
blind to the first proposal; no source values are needed for this check.

row_policy is an explicit semantic enum: keep_all (audit every named object,
preserve missing), filter_complete (only rows whose requested fields are all
present), top_n (pick N complete rows). Research mode names are mapped onto
this enum for comparing proposals. Other validators still parse the question;
this projection has not replaced ResearchDraft/QueryIntent as the model input
contract. The old persisted schema needs versioning before that migration.
"""
from typing import Literal
from pydantic import Field
from tcm.dataops.contracts import Contract

ROW_POLICIES = ('keep_all', 'filter_complete', 'top_n', 'not_applicable')

# ResearchGoal research.mode -> semantic row_policy.
MODE_TO_ROW_POLICY = {
    'audit_named': 'keep_all',
    'select_complete': 'top_n',
    'filter_complete': 'filter_complete',
}


class Requirements(Contract):
    version: Literal['requirements-v1'] = 'requirements-v1'
    objects: list[str] = Field(default_factory=list)
    # Namespace-qualified: etcm:<attribute|relation> or pharmacopoeia:<field>.
    fields: list[str] = Field(default_factory=list)
    row_policy: Literal['keep_all', 'filter_complete', 'top_n', 'not_applicable']
    count: int | None = None
    output: Literal['answer', 'table']
    operation: str = 'query'
    monograph_only: bool = False
    filters: list[dict] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


def from_proposal(proposal):
    if proposal['status'] == 'ready':
        goal = proposal['goal']
        mode = goal['research']['mode']
        return Requirements(
            objects=sorted(goal['query']['names']),
            fields=sorted(['etcm:'+f for f in goal['attributes']]+
                          ['pharmacopoeia:'+f for f in goal['document_fields']]),
            row_policy=MODE_TO_ROW_POLICY.get(mode, 'not_applicable'),
            count=goal['research']['target_count'] if mode == 'select_complete' else None,
            output='table', operation='append_fields' if proposal.get('parent_id') else 'new',
            monograph_only=goal['research']['monograph_only'], filters=goal['query']['filters'])
    intent = proposal.get('_intent', {})
    return Requirements(objects=sorted(intent.get('entities', [])),
                        fields=sorted(intent.get('fields', [])),
                        row_policy='not_applicable', output='answer',
                        unresolved=intent.get('unresolved_requirements', []))


def differences(first, second):
    left, right = from_proposal(first).model_dump(), from_proposal(second).model_dump()
    return [key for key in left if left[key] != right[key]]


def policy():
    import os
    value = os.getenv('DATAOPS_UNDERSTANDING_POLICY', 'local_then_one_model_v1')
    if value not in ('local_then_one_model_v1', 'unified_v2', 'requirements_v3', 'requirements_feedback_v1'):
        raise ValueError('Unsupported DATAOPS_UNDERSTANDING_POLICY')
    return value
