"""Follow-up review: fixtures only; preserve the preceding audit receipts."""
import copy
import importlib.util
import json
import socket
from pathlib import Path
from unittest.mock import patch
import dotenv


def deny(*args, **kwargs):
    raise AssertionError('Network forbidden')


def main():
    spec = importlib.util.spec_from_file_location('previous_review', 'audits/requirements_v3_review_v1/probe.py')
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    result = {'previous_probes': previous.main(), 'additional_probes': {}}
    out = result['additional_probes']
    from tests.test_requirements_understanding import good_draft, Sequence
    from tcm.dataops.requirements_v2 import RequirementsDraftV2 as Draft
    from tcm.dataops.requirements_planner import bind_requirements, compile_plan, validate_plan_coverage, to_query_routes, to_research_goal
    from tcm.dataops.requirements_pipeline import run_requirements_v3
    from tcm.dataops.requirements_understanding import normalize

    def observe(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:
            out[name] = {'exception': type(exc).__name__, 'message': str(exc)[:400]}

    def pipeline(kind):
        data = good_draft()
        if kind == 'relation':
            data['requirements'] = [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'], 'relation_kind': 'target'}]
        else:
            data['objects'] = []
            data['requirements'] = [{'id': 'r1', 'kind': 'dataset', 'datasets': ['etcm']}]
        model = Sequence(data, data)
        r = run_requirements_v3(model, '查询合成资料', {'黄芪': ['src']}, authorized=True)
        return {'status': r['status'], 'model_calls': model.calls}
    observe('relation_pipeline', lambda: pipeline('relation'))
    observe('dataset_pipeline', lambda: pipeline('dataset'))

    def wrong_known_tool():
        b = bind_requirements(Draft.model_validate(good_draft()), {'黄芪': ['src']})
        p = compile_plan(b)
        p.steps[0].tool = 'herb_attributes'
        return {'coverage': validate_plan_coverage(b, p).model_dump(), 'routes': to_query_routes(b, p)}
    observe('wrong_known_tool', wrong_known_tool)

    def relation_normalize():
        d = good_draft()
        d['objects'] = [{'ref': f'o{i}', 'mention': name} for i, name in enumerate(['甲', '乙', '丙', '丁'], 1)]
        d['requirements'] = [
            {'id': 'r1', 'kind': 'relation', 'object_refs': ['o1', 'o2'], 'relation_kind': 'common_target'},
            {'id': 'r2', 'kind': 'relation', 'object_refs': ['o3', 'o4'], 'relation_kind': 'common_target'}]
        other = copy.deepcopy(d)
        other['requirements'][0]['object_refs'] = ['o1', 'o3']
        other['requirements'][1]['object_refs'] = ['o2', 'o4']
        return {'different_pairs_compare_equal': normalize(Draft.model_validate(d)) == normalize(Draft.model_validate(other))}
    observe('relation_pair_semantics', relation_normalize)

    def default_columns():
        d = good_draft()
        d['delivery'] = {'form': 'table', 'row_policy': 'filter_complete'}
        b = bind_requirements(Draft.model_validate(d), {'黄芪': ['src']})
        return to_research_goal(b)
    observe('unrequested_filter_attribute', default_columns)

    def changed_parent_scope():
        d = good_draft()
        d['operation'] = 'append_fields'
        d['delivery'] = {'form': 'table', 'row_policy': 'keep_all'}
        parent = {'id': 'p', 'revision': 1, 'goal': {
            'query': {'names': ['黄芪', '白术'], 'filters': [], 'limit': 20},
            'attributes': ['family'], 'document_fields': ['注意'],
            'research': {'mode': 'filter_complete', 'target_count': 2, 'monograph_only': True}}}
        goal = to_research_goal(bind_requirements(Draft.model_validate(d), {'黄芪': ['src']}, parent=parent))
        return {'parent': parent['goal'], 'new_goal': goal}
    observe('append_changes_parent_scope', changed_parent_scope)

    from tests.test_relation_queries import RelationQueryTests
    from tcm.relation_queries import execute, parse
    case = RelationQueryTests('test_parser_is_bounded_and_does_not_capture_clinical_questions')
    case.setUp()
    try:
        def actual_all_pages():
            base = parse('/relations 靶点 Alpha', explicit=True)
            paged = execute(base)
            all_result = execute({**base, 'all_pages': True})
            info = all_result['relations']['data']
            return {'status': all_result['status'], 'total': info['total'],
                    'returned_count': info['returned_count'], 'next_offset': info['next_offset'],
                    'equal_to_page_one': all_result == paged}
        observe('actual_relation_executor_all_pages', actual_all_pages)
    finally:
        case.doCleanups()

    from tests.test_requirements_v3_http import RequirementsV3HTTPTests, RequirementsSequence, table_draft
    http_case = RequirementsV3HTTPTests('test_idempotent_replay_no_new_model_call')
    http_case.setUp()
    try:
        http_case.client.get('/dataops/research')
        d = table_draft()
        d['requirements'][0]['object_refs'] = ['o1']
        d['requirements'].append({'id': 'r2', 'kind': 'read_fields', 'object_refs': ['o2'],
                                   'fields': [{'namespace': 'etcm', 'name': 'family'}]})
        model = RequirementsSequence(d, d)
        def unsupported_http():
            with patch('tcm.dataops.research_model.configured_model', return_value=model):
                body = {'question': '整理Alpha功效注意、Beta科名，生成表格', 'request_key': 'review-per-object'}
                first = http_case.client.post('/api/dataops/research-questions', json=body)
                second = http_case.client.post('/api/dataops/research-questions', json=body)
                return {'first_http': first.status_code, 'first': first.json(),
                        'replay_http': second.status_code, 'replay': second.json(), 'model_calls': model.calls}
        observe('expected_adapter_rejection_http', unsupported_http)
    finally:
        http_case.doCleanups()
    return result


if __name__ == '__main__':
    with patch.object(dotenv, 'load_dotenv', return_value=False), \
         patch.object(socket.socket, 'connect', deny), patch.object(socket.socket, 'connect_ex', deny), \
         patch.object(socket.socket, 'sendto', deny), patch.object(socket, 'getaddrinfo', deny):
        result = main()
    Path(__file__).with_name('probe-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result['additional_probes'], ensure_ascii=False, indent=2))
