"""Mechanical planner: BoundRequirementsV2 -> CompiledPlan without the question.

Coverage is derived from each step's actual tool and arguments, preserving the
requirement identity, the whole relation object group and the read scope. The
adapter (to_research_goal) rejects parent-scope conflicts and per-object field
differences it cannot express in a single ResearchGoal.
"""
from collections import Counter
import json
from pydantic import BaseModel, ConfigDict, Field

from tcm.dataops.capability_matrix import (
    resolve, deny_reason, FORM_ANSWER, FORM_TABLE_CELL, FORM_RELATION,
)
from tcm.dataops.requirements_v2 import BoundRequirementsV2, RequirementsDraftV2

ANSWER_TOOLS = {'herb_attributes', 'pharmacopoeia_lookup', 'herb_relation_records',
                'assistant_etcm_relations', 'dataset_info'}
TABLE_TOOLS = {'dataops_table'}
KNOWN_TOOLS = ANSWER_TOOLS | TABLE_TOOLS


class Coverage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    requirement_id: str
    object_ref: str | None = None
    namespace: str
    name: str
    scope: str = 'full'


class PlanStep(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    step_id: str
    tool: str
    arguments: dict = Field(default_factory=dict)
    covers: list[Coverage] = Field(default_factory=list)


class CompiledPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    steps: list[PlanStep] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ok: bool
    errors: list[str] = Field(default_factory=list)


class CoverageResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ok: bool
    missing: list[Coverage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def bind_requirements(draft: RequirementsDraftV2, catalog: dict, *, parent=None) -> BoundRequirementsV2:
    """Bind literal mentions to host name candidates. Source IDs are host-owned."""
    objects = []
    for o in draft.objects:
        candidates = sorted(catalog.get(o.mention, []))
        objects.append({
            'ref': o.ref, 'mention': o.mention, 'name_candidates': candidates,
            'selected_source_ids': candidates[:1] if len(candidates) == 1 else [],
            'identity_verified': False,
        })
    return BoundRequirementsV2(
        version='requirements-v2', draft=draft, objects=objects,
        data_version=catalog.get('__data_version__', ''),
        owner=catalog.get('__owner__', ''),
        parent_workspace_id=parent.get('id') if parent else None,
        parent_revision=parent.get('revision') if parent else None,
        parent_goal=parent.get('goal') if parent else None,
    )


def _form_for(delivery: str) -> str:
    return FORM_TABLE_CELL if delivery == 'table' else FORM_ANSWER


def validate_requirements(bound: BoundRequirementsV2) -> ValidationResult:
    """Mechanical field/relation validation against the capability registry."""
    errors = []
    delivery = bound.draft.delivery.form
    for req in bound.draft.requirements:
        if req.kind == 'read_fields':
            for f in req.fields:
                try:
                    resolve(f.namespace, f.name, _form_for(delivery))
                except KeyError as exc:
                    errors.append(str(exc))
        elif req.kind == 'relation':
            if delivery == 'table':
                errors.append(deny_reason('etcm', req.relation_kind, FORM_TABLE_CELL))
            else:
                try:
                    cap = resolve('etcm', req.relation_kind, FORM_RELATION)
                except KeyError as exc:
                    errors.append(str(exc))
                    continue
                n = len(req.object_refs)
                if not (cap.object_min <= n <= cap.object_max):
                    errors.append(f'{req.relation_kind} requires {cap.object_min}-{cap.object_max} object refs')
                if req.relation_kind == 'common_target' and n != 2:
                    errors.append('common_target requires exactly two object refs')
                if req.completeness == 'all':
                    errors.append('full-read (completeness=all) is not yet supported; request a bounded page')
        elif req.kind == 'dataset':
            pass
    if bound.draft.objects:
        for obj in bound.objects:
            if len(obj.name_candidates) == 0:
                errors.append(f'object {obj.mention} has no source candidate')
            elif len(obj.name_candidates) > 1:
                errors.append(f'object {obj.mention} has ambiguous candidates; select a source')
    return ValidationResult(ok=not errors, errors=errors)


def _expected_items(bound):
    """Canonical coverage items: (kind, namespace, name, object_group, scope)."""
    items = []
    for req in bound.draft.requirements:
        if req.kind == 'read_fields':
            for ref in req.object_refs:
                for f in req.fields:
                    items.append(('read_fields', f.namespace, f.name, (ref,), 'full'))
        elif req.kind == 'relation':
            items.append(('relation', 'etcm', req.relation_kind,
                          tuple(sorted(req.object_refs)), req.completeness))
        elif req.kind == 'dataset':
            items.append(('dataset', 'dataset', 'dataset_info', (), tuple(sorted(req.datasets))))
    return items


def _actual_item(step: PlanStep):
    """Canonical coverage item this step actually performs, from arguments."""
    args = step.arguments
    tool = step.tool
    if tool in ('herb_attributes', 'pharmacopoeia_lookup', 'dataops_table'):
        obj, ns, name = args.get('object_ref'), args.get('namespace'), args.get('name')
        if obj and ns and name:
            return ('read_fields', ns, name, (obj,), 'full')
        return None
    if tool in ('herb_relation_records', 'assistant_etcm_relations'):
        refs = tuple(sorted(args.get('object_refs') or []))
        if not refs or not args.get('relation_kind'):
            return None
        return ('relation', 'etcm', args.get('relation_kind'), refs, args.get('completeness', 'page'))
    if tool == 'dataset_info':
        return ('dataset', 'dataset', 'dataset_info', (), tuple(sorted(args.get('datasets') or [])))
    return None


def _declared_items(step: PlanStep, req_index):
    """Canonical coverage items claimed by the step's covers, grouped per requirement."""
    items = []
    relation_groups = {}
    for cov in step.covers:
        req = req_index.get(cov.requirement_id)
        if req is None:
            continue
        if req.kind == 'read_fields':
            if cov.object_ref and cov.namespace and cov.name:
                items.append(('read_fields', cov.namespace, cov.name, (cov.object_ref,), 'full'))
        elif req.kind == 'relation':
            relation_groups.setdefault(req.id, set()).add(cov.object_ref)
        elif req.kind == 'dataset':
            items.append(('dataset', 'dataset', 'dataset_info', (), tuple(sorted(req.datasets))))
    for req_id, refs in relation_groups.items():
        req = req_index[req_id]
        items.append(('relation', 'etcm', req.relation_kind, tuple(sorted(refs)), req.completeness))
    return items


def _tool_argument_mismatch(step: PlanStep, delivery: str):
    args = step.arguments
    tool = step.tool
    if delivery == 'answer' and tool not in ANSWER_TOOLS:
        return f'{tool} cannot serve answer delivery'
    if delivery == 'table' and tool not in TABLE_TOOLS:
        return f'{tool} cannot serve table delivery'
    ns = args.get('namespace')
    if tool == 'herb_attributes' and ns != 'etcm':
        return f'herb_attributes cannot serve namespace {ns}'
    if tool == 'pharmacopoeia_lookup' and ns != 'pharmacopoeia':
        return f'pharmacopoeia_lookup cannot serve namespace {ns}'
    if tool == 'dataops_table' and ns not in ('etcm', 'pharmacopoeia'):
        return f'dataops_table cannot serve namespace {ns}'
    if tool in ('herb_relation_records', 'assistant_etcm_relations') and args.get('relation_kind') is None:
        return f'{tool} requires relation_kind'
    return None


def compile_plan(bound: BoundRequirementsV2) -> CompiledPlan:
    """Resolve requirements to steps, each declaring its covered requirement ids."""
    errors = []
    steps = []
    seq = 0
    delivery = bound.draft.delivery.form
    for req in bound.draft.requirements:
        seq += 1
        if req.kind == 'read_fields':
            for obj in req.object_refs:
                for f in req.fields:
                    form = _form_for(delivery)
                    try:
                        cap = resolve(f.namespace, f.name, form)
                    except KeyError:
                        errors.append(deny_reason(f.namespace, f.name, form))
                        continue
                    steps.append(PlanStep(
                        step_id=f's{seq}_{obj}_{f.namespace}_{f.name}',
                        tool=cap.tool,
                        arguments={'object_ref': obj, 'namespace': f.namespace, 'name': f.name},
                        covers=[Coverage(requirement_id=req.id, object_ref=obj,
                                         namespace=f.namespace, name=f.name)],
                    ))
        elif req.kind == 'relation':
            try:
                cap = resolve('etcm', req.relation_kind, FORM_RELATION)
            except KeyError:
                errors.append(deny_reason('etcm', req.relation_kind, FORM_RELATION))
                continue
            steps.append(PlanStep(
                step_id=f's{seq}_{req.relation_kind}',
                tool=cap.tool,
                arguments={'object_refs': req.object_refs, 'relation_kind': req.relation_kind,
                           'completeness': req.completeness},
                covers=[Coverage(requirement_id=req.id, object_ref=o,
                                 namespace='etcm', name=req.relation_kind)
                        for o in req.object_refs],
            ))
        elif req.kind == 'dataset':
            steps.append(PlanStep(
                step_id=f's{seq}_dataset',
                tool='dataset_info',
                arguments={'datasets': req.datasets},
                covers=[Coverage(requirement_id=req.id, namespace='dataset', name='dataset_info')],
            ))
    return CompiledPlan(steps=steps, errors=errors)


def validate_plan_coverage(bound: BoundRequirementsV2, plan: CompiledPlan) -> CoverageResult:
    """Derive coverage from actual tool/arguments and declared covers; never trust covers alone."""
    errors = list(plan.errors)
    delivery = bound.draft.delivery.form
    req_index = {r.id: r for r in bound.draft.requirements}
    expected = _expected_items(bound)
    actual = []
    seen_ids = set()
    seen_args = set()
    for step in plan.steps:
        if step.step_id in seen_ids:
            errors.append(f'duplicate step id {step.step_id}')
        seen_ids.add(step.step_id)
        if step.tool not in KNOWN_TOOLS:
            errors.append(f'unknown tool {step.tool}')
        else:
            mismatch = _tool_argument_mismatch(step, delivery)
            if mismatch:
                errors.append(mismatch)
        key = (step.tool, json.dumps(step.arguments, sort_keys=True, ensure_ascii=False, default=str))
        if key in seen_args:
            errors.append(f'duplicate step {step.tool} {step.arguments}')
        seen_args.add(key)
        item = _actual_item(step)
        if item is None:
            errors.append(f'step {step.step_id} has no derivable coverage')
            continue
        actual.append(item)
        declared = _declared_items(step, req_index)
        if Counter(declared) != Counter([item]):
            errors.append(f'declared covers do not match derived coverage for {step.step_id}')
    exp_counter = Counter(expected)
    act_counter = Counter(actual)
    missing = [e for e in expected if act_counter[e] < exp_counter[e]]
    extra = [a for a in actual if act_counter[a] > exp_counter.get(a, 0)]
    if extra:
        errors.append('plan covers unrequested work')
    missing_cov = [Coverage(requirement_id='', object_ref=(m[3] or (None,))[0],
                            namespace=m[1], name=m[2]) for m in missing]
    return CoverageResult(ok=not missing and not errors, missing=missing_cov, errors=errors)


ROW_POLICY_TO_MODE = {'keep_all': 'audit_named', 'filter_complete': 'filter_complete', 'top_n': 'select_complete'}


def to_research_goal(bound: BoundRequirementsV2) -> dict:
    """Adapter: table-delivery bound requirements -> existing ResearchGoal dict."""
    from tcm.dataops.research_contracts import ResearchGoal
    delivery = bound.draft.delivery
    if delivery.form != 'table':
        raise ValueError('to_research_goal requires table delivery')
    names = [o.mention for o in bound.objects]
    per_object = {}
    for req in bound.draft.requirements:
        if req.kind == 'read_fields':
            for ref in req.object_refs:
                per_object.setdefault(ref, (set(), set()))
                for f in req.fields:
                    if f.namespace == 'etcm':
                        per_object[ref][0].add(f.name)
                    else:
                        per_object[ref][1].add(f.name)
    attr_sets = {tuple(sorted(v[0])) for v in per_object.values()}
    doc_sets = {tuple(sorted(v[1])) for v in per_object.values()}
    if len(attr_sets) > 1 or len(doc_sets) > 1:
        raise ValueError('per-object field differences cannot be expressed in a single ResearchGoal')
    attributes = sorted(attr_sets.pop()) if attr_sets else []
    document_fields = sorted(doc_sets.pop()) if doc_sets else []
    parent_goal = bound.parent_goal or {}
    if parent_goal:
        parent_names = [str(x) for x in (parent_goal.get('query') or {}).get('names') or []]
        if set(names) != set(parent_names):
            raise ValueError('append cannot change the parent object set; clarify or start a new task')
        parent_mode = (parent_goal.get('research') or {}).get('mode')
        child_mode = ROW_POLICY_TO_MODE.get(delivery.row_policy)
        if parent_mode and child_mode and parent_mode != child_mode:
            raise ValueError('append cannot change the parent row policy')
        parent_count = (parent_goal.get('research') or {}).get('target_count')
        if delivery.row_policy == 'top_n' and delivery.count != parent_count:
            raise ValueError('append cannot change the parent target count')
        query = dict(parent_goal.get('query') or {'names': names, 'filters': [], 'limit': 20})
        research = dict(parent_goal.get('research') or {})
        mode = research.get('mode', ROW_POLICY_TO_MODE.get(delivery.row_policy, 'audit_named'))
        target_count = research.get('target_count', len(names))
        monograph_only = research.get('monograph_only', False)
        attributes = sorted(set(parent_goal.get('attributes', [])) | set(attributes))
        document_fields = sorted(set(parent_goal.get('document_fields', [])) | set(document_fields))
    else:
        query = {'names': names, 'filters': [], 'limit': 20}
        mode = ROW_POLICY_TO_MODE.get(delivery.row_policy, 'audit_named')
        if mode == 'select_complete':
            if delivery.count is None:
                raise ValueError('top_n requires count')
            target_count = delivery.count
        else:
            target_count = len(names)
        monograph_only = False
    if mode == 'select_complete' and target_count is None:
        raise ValueError('top_n requires count')
    if not attributes and not document_fields:
        raise ValueError('Table requires at least one explicit output field')
    goal = {
        'title': '问句资料核对',
        'query': query,
        'attributes': attributes,
        'document_fields': document_fields,
        'completion_policy': 'audit',
        'research': {'mode': mode, 'target_count': target_count, 'monograph_only': monograph_only},
    }
    ResearchGoal.model_validate(goal)
    return goal


def to_query_routes(bound: BoundRequirementsV2, plan: CompiledPlan) -> list:
    """Adapter: answer-delivery plan steps -> existing query route dicts.

    Raises if a step cannot be served by the answer delivery, so an adapter gap
    never produces a silently empty route list.
    """
    mention = {o.ref: o.mention for o in bound.objects}
    routes = []
    for step in plan.steps:
        if step.tool not in ANSWER_TOOLS:
            raise ValueError(f'answer delivery cannot serve step tool {step.tool}')
        if step.tool == 'herb_attributes':
            ref, name = step.arguments.get('object_ref'), step.arguments.get('name')
            routes.append({'tool': 'herb', 'request': {'name': mention.get(ref, ref), 'field': name,
                                                       'resolve_unique_name': True}})
        elif step.tool == 'pharmacopoeia_lookup':
            ref, name = step.arguments.get('object_ref'), step.arguments.get('name')
            routes.append({'tool': 'research', 'request': {'question': mention.get(ref, ref) + '的' + name}})
        elif step.tool in ('herb_relation_records', 'assistant_etcm_relations'):
            refs = step.arguments.get('object_refs', [])
            kind = step.arguments.get('relation_kind')
            routes.append({'tool': 'assistant',
                           'request': {'topic': 'etcm_relations', 'kind': kind,
                                       'entities': [mention.get(r, r) for r in refs], 'page': 1,
                                       'resolve_unique_sources': True}})
        elif step.tool == 'dataset_info':
            routes.append({'tool': 'assistant', 'request': {'topic': 'dataset_info',
                                                           'datasets': step.arguments.get('datasets', [])}})
    return routes
