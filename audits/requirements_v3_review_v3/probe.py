"""Third review; fixtures only, preserving both preceding review receipts."""
import importlib.util
import json
import socket
from pathlib import Path
from unittest.mock import patch
import dotenv


def deny(*args, **kwargs):
    raise AssertionError('Network forbidden')


def main():
    spec = importlib.util.spec_from_file_location('review2', 'audits/requirements_v3_review_v2/probe.py')
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    out = {'previous_review': previous.main(), 'remaining_boundaries': {}}
    findings = out['remaining_boundaries']
    from tests.test_requirements_understanding import good_draft, Sequence
    from tcm.dataops.requirements_v2 import RequirementsDraftV2 as Draft
    from tcm.dataops.requirements_planner import bind_requirements, compile_plan, validate_plan_coverage, to_query_routes
    from tcm.dataops.requirements_pipeline import run_requirements_v3

    def observe(name, fn):
        try:
            findings[name] = fn()
        except Exception as exc:
            findings[name] = {'exception': type(exc).__name__, 'message': str(exc)[:400]}

    def answer_to_table_tool():
        bound = bind_requirements(Draft.model_validate(good_draft()), {'黄芪': ['src']})
        plan = compile_plan(bound)
        plan.steps[0].tool = 'dataops_table'
        return {'coverage': validate_plan_coverage(bound, plan).model_dump(),
                'routes': to_query_routes(bound, plan)}
    observe('answer_replaced_by_table_step', answer_to_table_tool)

    def swapped_pairs():
        d = good_draft()
        d['objects'] = [{'ref': f'o{i}', 'mention': n} for i, n in enumerate(['甲', '乙', '丙', '丁'], 1)]
        d['requirements'] = [
            {'id': 'r1', 'kind': 'relation', 'object_refs': ['o1', 'o2'], 'relation_kind': 'common_target'},
            {'id': 'r2', 'kind': 'relation', 'object_refs': ['o3', 'o4'], 'relation_kind': 'common_target'}]
        bound = bind_requirements(Draft.model_validate(d), {n: [n] for n in ['甲', '乙', '丙', '丁']})
        plan = compile_plan(bound)
        plan.steps[0].arguments['object_refs'] = ['o1', 'o3']
        plan.steps[1].arguments['object_refs'] = ['o2', 'o4']
        return {'coverage': validate_plan_coverage(bound, plan).model_dump(),
                'executed_pairs': [r['request']['entities'] for r in to_query_routes(bound, plan)]}
    observe('plan_pairing_mutation', swapped_pairs)

    def all_rejected():
        d = good_draft()
        d['requirements'] = [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
                              'relation_kind': 'target', 'completeness': 'all'}]
        result = run_requirements_v3(Sequence(d,d), '黄芪全部靶点', {'黄芪':['src']}, authorized=True)
        return {'status': result['status'], 'errors': result.get('errors'), 'routes_exist': 'query_routes' in result}
    observe('all_explicitly_stopped_in_pipeline', all_rejected)

    from tests.test_requirements_v3_http import RequirementsV3HTTPTests, RequirementsSequence, table_draft
    case = RequirementsV3HTTPTests('test_idempotent_replay_no_new_model_call')
    case.setUp()
    try:
        def conflicting_append():
            d = table_draft()
            with patch('tcm.dataops.research_model.configured_model', return_value=RequirementsSequence(d,d)):
                response = case.client.post('/chat', json={'query':'整理Alpha和Beta的功效注意，生成表格',
                                                           'request_key':'review3-parent'})
                parent = case.wait(response.json()['assistant']['workspace_id'])
            child = {'version':'requirements-v2','status':'ready','operation':'append_fields',
                     'objects':[{'ref':'o1','mention':'Gamma'}],
                     'requirements':[{'id':'r1','kind':'read_fields','object_refs':['o1'],
                                       'fields':[{'namespace':'pharmacopoeia','name':'贮藏'}]}],
                     'delivery':{'form':'table','row_policy':'keep_all'},'unresolved':[]}
            model = RequirementsSequence(child,child)
            with patch('tcm.dataops.research_model.configured_model', return_value=model):
                response = case.client.post('/api/dataops/research-questions', json={
                    'question':'给Gamma增加贮藏，保留旧字段', 'request_key':'review3-child',
                    'parent_id':parent['id'],'expected_revision':parent['revision']})
                body = response.json()
                result = {'http':response.status_code,'model_calls':model.calls,
                          'submitted_object':'Gamma', 'proposal_status':body.get('proposal',{}).get('status')}
                if 'workspace' in body:
                    doc = case.wait(body['workspace']['id'])
                    result.update(task_status=doc['status'], executed_names=doc['goal']['query']['names'],
                                  requirement_objects=doc['goal_understanding']['requirements']['objects'])
                else:
                    result['response'] = body
                return result
        observe('append_explicitly_changed_object', conflicting_append)
    finally:
        case.doCleanups()
    return out


if __name__ == '__main__':
    with patch.object(dotenv,'load_dotenv',return_value=False), \
         patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny), \
         patch.object(socket.socket,'sendto',deny),patch.object(socket,'getaddrinfo',deny):
        result = main()
    Path(__file__).with_name('probe-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['remaining_boundaries'],ensure_ascii=False,indent=2))
