"""U4 pipeline: question -> draft -> bind -> validate -> plan -> coverage -> deliverable.

This is the full understanding->planning chain for the requirements_v3 strategy,
self-contained and testable without touching DataOpsService. The service wiring
only needs to call run_requirements_v3 and enqueue the returned ResearchGoal (or
execute the returned query routes). Diagnostics are recorded at every stage;
no real model is called unless authorized.
"""
from tcm.dataops import diagnostics as diag
from tcm.dataops.requirements_planner import (
    bind_requirements, validate_requirements, compile_plan, validate_plan_coverage,
    to_research_goal, to_query_routes,
)
from tcm.dataops.requirements_understanding import request_requirements


def run_requirements_v3(model, question, catalog, *, parent=None, authorized=False):
    """Run the full requirements_v3 chain. Returns a dict with status + deliverable.

    status: 'ready' (goal or query_routes) | 'needs_input' | 'unsupported_delivery'
            | 'plan_requirement_omitted'. No tools execute here.
    """
    diag.record('requirements_extract', 'started', policy='requirements_v3',
                authorized=authorized, allowed_names=sorted(catalog))
    understood = request_requirements(model, question, catalog, parent=parent, authorized=authorized)
    if understood['status'] != 'ready':
        diag.record('requirements_extract', 'stopped', code=understood.get('failure', {}).get('code', 'requirements_unresolved'))
        return {**understood, 'policy': 'requirements_v3'}

    prepared = prepare_requirements(understood['draft'], catalog, parent=parent)
    return {**prepared, 'policy': 'requirements_v3',
            'external_model_calls': understood['external_model_calls'], 'usage': understood['usage']}


def prepare_requirements(draft, catalog, *, parent=None):
    """Read-only mechanical validation and adapter construction; no model or execution."""
    diag.record('object_binding', 'started')
    bound = bind_requirements(draft, catalog, parent=parent)
    diag.record('object_binding', 'bound', bound_refs=[o.ref for o in bound.objects])

    diag.record('capability_resolution', 'started')
    validation = validate_requirements(bound)
    diag.record('capability_resolution', 'rejected' if not validation.ok else 'accepted', errors=validation.errors)
    if not validation.ok:
        unsupported = any('不支持' in e for e in validation.errors)
        return {'status': 'unsupported_delivery' if unsupported else 'needs_input',
                'draft': draft, 'errors': validation.errors,
                'requirements': draft}

    diag.record('plan_compile', 'started')
    plan = compile_plan(bound)
    diag.record('plan_compile', 'compiled', step_count=len(plan.steps), errors=plan.errors)

    diag.record('plan_coverage', 'started')
    coverage = validate_plan_coverage(bound, plan)
    diag.record('plan_coverage', 'rejected' if not coverage.ok else 'accepted',
                missing=coverage.missing, errors=coverage.errors)
    if not coverage.ok:
        return {'status': 'plan_requirement_omitted', 'draft': draft,
                'missing': [m.model_dump() for m in coverage.missing], 'errors': coverage.errors,
                'requirements': draft}

    if draft.delivery.form == 'table':
        try:
            goal = to_research_goal(bound)
        except ValueError as exc:
            diag.record('delivery_verification', 'rejected', reason=str(exc)[:300])
            return {'status': 'unsupported_delivery', 'draft': draft,
                    'errors': [str(exc)],
                    'failure': {'code': 'unsupported_delivery', 'stage': 'delivery_adapter',
                                'message': '当前表格契约无法表达该需求：' + str(exc),
                                'automatic_retry': False},
                    'requirements': draft}
        diag.record('delivery_verification', 'goal_built', goal=goal)
        return {'status': 'ready', 'goal': goal, 'plan': plan,
                'requirements': draft}
    try:
        routes = to_query_routes(bound, plan)
    except ValueError as exc:
        diag.record('delivery_verification', 'rejected', reason=str(exc)[:300])
        return {'status': 'unsupported_delivery', 'draft': draft,
                'errors': [str(exc)],
                'failure': {'code': 'unsupported_delivery', 'stage': 'delivery_adapter',
                            'message': '当前查询契约无法表达该需求：' + str(exc),
                            'automatic_retry': False},
                'requirements': draft}
    if not routes:
        return {'status': 'unsupported_delivery', 'draft': draft,
                'errors': ['answer delivery produced no route'],
                'failure': {'code': 'unsupported_delivery', 'stage': 'delivery_adapter',
                            'message': '当前查询契约无法为该需求生成可执行路由。',
                            'automatic_retry': False},
                'requirements': draft}
    diag.record('delivery_verification', 'routes_built', route_count=len(routes))
    return {'status': 'ready', 'query_routes': routes, 'plan': plan,
            'requirements': draft}
