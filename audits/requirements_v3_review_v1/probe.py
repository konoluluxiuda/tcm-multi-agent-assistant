"""Independent offline review probes. Run with run_offline_ci.test_environment().

Only SDK fixtures and temporary ASGI state are used. Findings are observations,
not acceptance tests declaring the new implementation correct.
"""
import copy
import json
import socket
from pathlib import Path
from unittest.mock import patch

import dotenv


def deny(*args, **kwargs):
    raise AssertionError('Review forbids network')


def main():
    from tests.test_requirements_understanding import Sequence, good_draft
    from tcm.dataops.requirements_v2 import RequirementsDraftV2 as Draft
    from tcm.dataops.requirements_understanding import request_requirements, normalize
    from tcm.dataops.requirements_planner import (
        bind_requirements, validate_requirements, compile_plan,
        validate_plan_coverage, to_research_goal, to_query_routes, PlanStep,
    )
    from tcm.dataops.requirements_pipeline import run_requirements_v3
    from tcm.dataops.research_contracts import ResearchGoal
    from tcm.research_business_tools import RelationArgs
    observations = {}

    def observe(name, fn):
        try:
            observations[name] = fn()
        except Exception as exc:
            observations[name] = {'exception': type(exc).__name__, 'message': str(exc)[:500]}

    def model_run(*responses):
        model = Sequence(*responses)
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src']}, authorized=True)
        return {'status': result['status'], 'actual_calls': model.calls,
                'reported_calls': result['external_model_calls'], 'usage': result['usage'],
                'failure': result.get('failure'), 'phases': result['understanding_attempts']}

    invalid = good_draft()
    del invalid['requirements'][0]['fields']
    wrong = good_draft()
    wrong['requirements'][0]['fields'] = [{'namespace': 'etcm', 'name': 'family'}]
    observe('correction_failure_accounting', lambda: model_run(invalid, invalid))
    observe('review_failure_accounting', lambda: model_run(good_draft(), invalid))
    observe('review_unknown_dispatch', lambda: model_run(good_draft(), TimeoutError('unknown')))
    observe('wrong_semantics_after_correction', lambda: model_run(invalid, wrong))

    renamed = good_draft()
    renamed['objects'][0]['ref'] = 'o9'
    renamed['requirements'][0]['id'] = 'r9'
    renamed['requirements'][0]['object_refs'] = ['o9']
    observe('same_semantics_different_refs', lambda: model_run(good_draft(), renamed))

    class Capture(Sequence):
        def __init__(self, *replies):
            super().__init__(*replies)
            self.payloads = []
        def bind_tools(self, tools, **kwargs):
            self.payloads.append({'tools': tools})
            return super().bind_tools(tools, **kwargs)
        def invoke(self, messages):
            self.payloads.append({'messages': messages})
            return super().invoke(messages)

    def context_probe():
        model = Capture(invalid, good_draft())
        request_requirements(model, '增加贮藏', {'CATALOG_SENTINEL': ['src']},
                             parent={'goal': {'document_fields': ['PARENT_FIELD_SENTINEL']}}, authorized=True)
        payload = json.dumps(model.payloads, ensure_ascii=False)
        return {'catalog_sent': 'CATALOG_SENTINEL' in payload,
                'parent_sent': 'PARENT_FIELD_SENTINEL' in payload,
                'correction_code_sent': 'requirements_schema_failed' in payload}
    observe('model_context', context_probe)

    def parse(data):
        d = Draft.model_validate(data)
        return {'accepted': True, 'objects': len(d.objects), 'requirements': len(d.requirements)}
    observe('clarification_without_objects', lambda: parse({
        'status': 'needs_input', 'operation': 'new', 'objects': [], 'requirements': [],
        'delivery': {'form': 'answer'}, 'unresolved': ['请提供对象或任务目标']}))
    mixed = good_draft()
    mixed['requirements'].append({'id': 'r2', 'kind': 'dataset', 'datasets': ['etcm']})
    observe('mixed_dataset_and_herb', lambda: parse(mixed))
    duplicate = good_draft()
    duplicate['objects'].append({'ref': 'o1', 'mention': '白术'})
    observe('duplicate_object_ref', lambda: parse(duplicate))
    empty = good_draft()
    empty['requirements'] = []
    observe('ready_empty_requirements', lambda: parse(empty))

    d = Draft.model_validate(good_draft())
    bound = bind_requirements(d, {'黄芪': ['src']})
    for label, catalog in [('no_candidate', {}), ('ambiguous_candidate', {'黄芪': ['a', 'b']})]:
        observe(label, lambda catalog=catalog: validate_requirements(bind_requirements(d, catalog)).model_dump())

    def mutated_plan(kind):
        plan = compile_plan(bound)
        if kind == 'arguments':
            plan.steps[0].arguments['object_ref'] = 'o999'
            plan.steps[0].arguments['name'] = '注意'
        elif kind == 'tool':
            plan.steps[0].tool = 'nonexistent_tool'
        elif kind == 'duplicate':
            plan.steps.append(plan.steps[0].model_copy(deep=True))
        elif kind == 'extra':
            plan.steps.append(PlanStep(step_id='extra', tool='nonexistent_tool', covers=[]))
        return validate_plan_coverage(bound, plan).model_dump()
    for kind in ['arguments', 'tool', 'duplicate', 'extra']:
        observe('coverage_' + kind, lambda kind=kind: mutated_plan(kind))

    def all_relation():
        data = good_draft()
        data['requirements'] = [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
                                 'relation_kind': 'target', 'completeness': 'all'}]
        b = bind_requirements(Draft.model_validate(data), {'黄芪': ['src']})
        plan = compile_plan(b)
        return {'validation': validate_requirements(b).model_dump(),
                'coverage': validate_plan_coverage(b, plan).model_dump(),
                'routes': to_query_routes(b, plan)}
    observe('all_targets_becomes_page_one', all_relation)
    observe('common_target_real_tool_schema', lambda: RelationArgs.model_validate(
        {'name': '黄芪', 'kind': 'common_target'}).model_dump())

    def table_case(count=1, append=False):
        data = good_draft()
        data['objects'] = [{'ref': f'o{i+1}', 'mention': f'Herb{i+1}'} for i in range(count)]
        data['requirements'][0]['object_refs'] = [o['ref'] for o in data['objects']]
        data['delivery'] = {'form': 'table', 'row_policy': 'keep_all'}
        parent = None
        if append:
            data['operation'] = 'append_fields'
            data['requirements'][0]['fields'][0]['name'] = '贮藏'
            parent = {'id': 'parent', 'revision': 1,
                      'goal': {'document_fields': ['功能与主治', '注意'], 'attributes': ['family']}}
        b = bind_requirements(Draft.model_validate(data), {o['mention']: ['src'] for o in data['objects']}, parent=parent)
        goal = to_research_goal(b)
        try:
            ResearchGoal.model_validate(goal)
            valid = True
        except Exception:
            valid = False
        return {'mechanical_valid': validate_requirements(b).ok, 'goal': goal, 'real_goal_valid': valid}
    observe('append_loses_old_fields', lambda: table_case(append=True))
    observe('six_named_rows', lambda: table_case(count=6))

    def per_object_table():
        data = good_draft()
        data['objects'].append({'ref': 'o2', 'mention': '白术'})
        data['requirements'].append({'id': 'r2', 'kind': 'read_fields', 'object_refs': ['o2'],
                                     'fields': [{'namespace': 'etcm', 'name': 'family'}]})
        data['delivery'] = {'form': 'table', 'row_policy': 'filter_complete'}
        b = bind_requirements(Draft.model_validate(data), {'黄芪': ['a'], '白术': ['b']})
        return {'coverage': validate_plan_coverage(b, compile_plan(b)).ok,
                'goal': to_research_goal(b)}
    observe('table_per_object_scope_widened', per_object_table)

    def missing_count():
        data = good_draft()
        data['delivery'] = {'form': 'table', 'row_policy': 'top_n'}
        b = bind_requirements(Draft.model_validate(data), {'黄芪': ['src']})
        return {'mechanical_valid': validate_requirements(b).ok,
                'inferred_count': to_research_goal(b)['research']['target_count']}
    observe('top_n_missing_count', missing_count)

    # HTTP fixture has its own temporary stores and source patches.
    from tests.test_requirements_v3_http import RequirementsV3HTTPTests, RequirementsSequence, table_draft
    case = RequirementsV3HTTPTests('test_idempotent_replay_no_new_model_call')
    case.setUp()
    try:
        case.client.get('/dataops/research')
        def local_optout():
            model = RequirementsSequence(table_draft(), table_draft())
            with patch('tcm.dataops.research_model.configured_model', return_value=model) as factory:
                try:
                    response = case.client.post('/api/dataops/research-questions', json={
                        'question': '整理Alpha和Beta的功效和注意，生成表格',
                        'request_key': 'review-local-optout', 'use_model': False})
                    return {'http': response.status_code, 'model_constructed': factory.call_count,
                            'model_calls': model.calls, 'body': response.json()}
                except Exception as exc:
                    return {'exception': type(exc).__name__, 'model_constructed': factory.call_count,
                            'model_calls': model.calls, 'permission_error': 'PermissionError' in str(exc)}
        observe('http_local_optout', local_optout)
    finally:
        case.doCleanups()
    return observations


if __name__ == '__main__':
    with patch.object(dotenv, 'load_dotenv', return_value=False), \
         patch.object(socket.socket, 'connect', deny), \
         patch.object(socket.socket, 'connect_ex', deny), \
         patch.object(socket.socket, 'sendto', deny), \
         patch.object(socket, 'getaddrinfo', deny):
        result = main()
    Path(__file__).with_name('probe-results.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
