"""Bounded closure checks for the two review-v3 blockers; no external models."""
import copy
import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import patch
import dotenv


def deny(*args, **kwargs):
    raise AssertionError('Network forbidden')


def main():
    from tests.test_requirements_review3 import PlanCoverageTests, ParentConflictTests, parent_goal
    from tcm.dataops.requirements_planner import bind_requirements, compile_plan, validate_plan_coverage, to_research_goal
    from tests.test_requirements_v3_http import RequirementsV3HTTPTests, RequirementsSequence, table_draft
    checks = []
    coverage_case = PlanCoverageTests()
    bound = coverage_case._relation_bound()
    plan = compile_plan(bound)
    assert validate_plan_coverage(bound, plan).ok
    plan.steps[0].arguments['object_refs'] = ['o1', 'o3']
    plan.steps[1].arguments['object_refs'] = ['o2', 'o4']
    assert not validate_plan_coverage(bound, plan).ok
    checks.append({'name': 'correct_and_swapped_pairing', 'status': 'passed'})
    coverage_case.test_answer_tool_swap_to_table_rejected()
    coverage_case.test_adapter_raises_on_unhandled_tool()
    checks.append({'name': 'answer_table_swap_and_adapter', 'status': 'passed'})

    pc = ParentConflictTests()
    child = pc._child([{'ref':'o1','mention':'Alpha'}, {'ref':'o2','mention':'Beta'}])
    catalog = {'Alpha':['a'], 'Beta':['b']}
    for label, changed, parent in [
        ('row_policy',child,parent_goal(['Alpha','Beta'],mode='filter_complete')),
        ('target_count',child.model_copy(deep=True),parent_goal(['Alpha','Beta'],mode='select_complete',target_count=1))]:
        if label == 'target_count':
            changed.delivery = changed.delivery.model_copy(update={'row_policy':'top_n','count':2})
        bound = bind_requirements(changed,catalog,parent=parent)
        try:
            to_research_goal(bound)
        except ValueError as exc:
            assert 'append cannot change' in str(exc)
        else:
            raise AssertionError(label+' conflict accepted')
        checks.append({'name':label+'_conflict','status':'passed'})

    case = RequirementsV3HTTPTests('test_idempotent_replay_no_new_model_call')
    case.setUp()
    try:
        draft = table_draft()
        with patch('tcm.dataops.research_model.configured_model',return_value=RequirementsSequence(draft,draft)):
            response = case.client.post('/chat',json={'query':'整理Alpha和Beta的功效和注意，生成表格',
                                                    'request_key':'closure-parent'})
            assert response.status_code == 200,response.text
            parent = case.wait(response.json()['assistant']['workspace_id'])
        assert parent['status'] == 'complete'
        root = case.app.dataops_service.store().root
        path = root/parent['id']/parent['artifact']['filename']
        before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        def ids():
            return sorted(x['id'] for x in case.client.get('/api/dataops/workspaces').json()['items'])
        before_ids = ids()
        for label,name in [('foreign','Gamma'),('subset','Alpha')]:
            child = {'version':'requirements-v2','status':'ready','operation':'append_fields',
                     'objects':[{'ref':'o1','mention':name}],
                     'requirements':[{'id':'r1','kind':'read_fields','object_refs':['o1'],
                                       'fields':[{'namespace':'pharmacopoeia','name':'贮藏'}]}],
                     'delivery':{'form':'table','row_policy':'keep_all'},'unresolved':[]}
            model = RequirementsSequence(child,child)
            body = {'question':f'给{name}增加贮藏，保留旧字段','request_key':'closure-'+label,
                    'parent_id':parent['id'],'expected_revision':parent['revision']}
            with patch('tcm.dataops.research_model.configured_model',return_value=model):
                first = case.client.post('/api/dataops/research-questions',json=body)
                replay = case.client.post('/api/dataops/research-questions',json=body)
            assert first.status_code == replay.status_code == 200,(first.text,replay.text)
            a,b = first.json(),replay.json()
            assert 'workspace' not in a and 'workspace' not in b
            assert a['proposal']['failure']['code'] == 'unsupported_delivery'
            assert a['proposal'] == b['proposal'] and b['replayed']
            assert model.calls == 2
            assert before_ids == ids()
            assert before_hash == hashlib.sha256(path.read_bytes()).hexdigest()
            checks.append({'name':label+'_parent_conflict_http','status':'passed',
                           'first_http':200,'replay_http':200,'actual_model_fixture_calls':2,
                           'new_child_workspace':False,'parent_artifact_unchanged':True})
    finally:
        case.doCleanups()
    return {'status':'passed','checks':checks,'real_model_requests':0}


if __name__ == '__main__':
    with patch.object(dotenv,'load_dotenv',return_value=False), \
         patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny), \
         patch.object(socket.socket,'sendto',deny),patch.object(socket,'getaddrinfo',deny):
        result = main()
    Path(__file__).with_name('closure-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
