"""Server checks a user-declared request independently of the executable plan."""
from typing import Annotated,Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tcm.task_plan import plan_model

ENTITIES=('示例甲草','示例乙草','示例丙草')
DELIVERABLE_LABELS={'source_relations':'共同靶点及双方来源记录',
                    'evidence_review':'合成研究记录核查','medicinal_part_table':'药用部位并排表'}


class RequestScope(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version:Literal[1]=1
    data_mode:Literal['synthetic_only']='synthetic_only'
    origin:Literal['user_declared']='user_declared'
    entities:list[Literal['示例甲草','示例乙草','示例丙草']]=Field(min_length=2,max_length=3)
    deliverables:list[Literal['source_relations','evidence_review','medicinal_part_table']]=Field(default_factory=list,max_length=3)
    goal_to_confirm:bool=False
    other_requirements:list[Annotated[str,Field(min_length=1,max_length=120)]]=Field(default_factory=list,max_length=6)

    @model_validator(mode='after')
    def unique_items(self):
        if (len(set(self.entities))!=len(self.entities) or len(set(self.deliverables))!=len(self.deliverables)
                or len(set(self.other_requirements))!=len(self.other_requirements)
                or any(not text.strip() or text!=text.strip() for text in self.other_requirements)):
            raise ValueError('Request items must be unique')
        return self


class LocalRequestScope(RequestScope):
    data_mode:Literal['etcm_local']='etcm_local'
    entities:list[Annotated[str,Field(min_length=1,max_length=100)]]=Field(min_length=2,max_length=8)

    @model_validator(mode='after')
    def clean_names(self):
        if any(not n.strip() or n!=n.strip() for n in self.entities):
            raise ValueError('Local names must be nonblank and trimmed')
        return self


def scope_model(task):
    return LocalRequestScope if task.get('data_mode')=='etcm_local' else RequestScope


def deliverable_labels(task):
    return {**DELIVERABLE_LABELS,'evidence_review':'文献核查（本任务未执行）'} if task.get('data_mode')=='etcm_local' else DELIVERABLE_LABELS


def assess_coverage(task,plan=None):
    """Recompute from immutable request; never trust client-supplied coverage flags."""
    model=scope_model(task)
    original=model.model_validate(task['request_scope'])
    plan=plan_model(task).model_validate(plan if plan is not None else task['plan'])
    effective=original.model_copy(deep=True)
    # With no specified goal, explicit plan confirmation resolves that slot.
    if (effective.goal_to_confirm or (not effective.deliverables and not effective.other_requirements)) and plan.intent:
        effective.deliverables=list(dict.fromkeys([plan.intent,*effective.deliverables]))
        effective.goal_to_confirm=False
    capabilities=({'source_relations','evidence_review'} if plan.intent=='evidence_review'
                  else {'source_relations'} if plan.intent=='source_relations' else set())
    permitted=set(effective.deliverables)
    if 'evidence_review' in permitted:permitted.add('source_relations')
    missing_entities=[n for n in effective.entities if n not in plan.names()]
    missing_deliverables=[d for d in effective.deliverables if d not in capabilities]
    extra_entities=[n for n in plan.names() if n not in effective.entities]
    extra_deliverables=sorted(capabilities-permitted)
    ready=not plan.missing()
    missing_other=list(original.other_requirements)
    needs_reduction=bool(missing_entities or missing_deliverables or missing_other)
    acceptance=task.get('scope_acceptance')
    accepted_request=(model.model_validate(acceptance['effective_request']).model_dump()
                      if acceptance else None)
    accepted=bool(acceptance and acceptance.get('plan')==plan.model_dump()
                  and accepted_request==effective.model_dump())
    return {'version':1,'original_request':original.model_dump(),'effective_request':effective.model_dump(),
        'uncovered_entities':missing_entities,'uncovered_deliverables':missing_deliverables,
        'uncovered_other_requirements':missing_other,
        'extra_entities':extra_entities,'extra_deliverables':extra_deliverables,
        'plan_ready':ready,'needs_reduction':needs_reduction,
        'acceptance_matches_plan':not acceptance or accepted,
        'reduction_accepted':accepted and acceptance.get('reduced') is True,
        'can_execute':ready and not extra_entities and not extra_deliverables and (not acceptance or accepted) and
            (not needs_reduction or (accepted and acceptance.get('reduced') is True))}


class ScopeDecisionRequired(ValueError):pass
class ScopeMismatch(ValueError):pass


def confirm_scope(task,plan,*,accept_reduced_scope):
    coverage=assess_coverage(task,plan)
    if not coverage['plan_ready'] or coverage['extra_entities'] or coverage['extra_deliverables']:
        raise ScopeMismatch('Selected plan exceeds the declared request or is incomplete')
    if coverage['needs_reduction'] and accept_reduced_scope is not True:
        raise ScopeDecisionRequired('Explicit scope reduction required')
    task['scope_acceptance']={'plan':plan.model_dump(),'effective_request':coverage['effective_request'],
        'reduced':coverage['needs_reduction'],'uncovered_entities':coverage['uncovered_entities'],
        'uncovered_deliverables':coverage['uncovered_deliverables'],
        'uncovered_other_requirements':coverage['uncovered_other_requirements']}


def require_covered_scope(task):
    if task.get('request_scope') and not assess_coverage(task)['can_execute']:
        raise ScopeDecisionRequired('Request scope has not been confirmed')


def cover_report(task,report):
    if not task.get('request_scope'):return report
    coverage=assess_coverage(task)
    subset_complete=report['complete']
    original_complete=subset_complete and coverage['can_execute'] and not coverage['needs_reduction']
    gaps=list(report['gaps'])
    gaps += ['原请求未覆盖对象：'+n for n in coverage['uncovered_entities']]
    gaps += ['原请求未覆盖交付：'+deliverable_labels(task)[d] for d in coverage['uncovered_deliverables']]
    gaps += ['原请求未覆盖其他要求：'+text for text in coverage['uncovered_other_requirements']]
    result={**report,'complete':bool(original_complete),'confirmed_scope_complete':subset_complete,
            'original_request_complete':bool(original_complete),'request_coverage':coverage,'gaps':gaps}
    if 'request_draft' in task:
        result.update(request_inventory_user_confirmed=bool((task.get('request_review') or {}).get('confirmed_complete')),
                      raw_question_completeness_automatically_verified=False)
    return result
