"""Review v3 regressions: parent-scope conflicts, delivery-form/relation-group coverage."""
import unittest
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.requirements_planner import (
    bind_requirements, validate_requirements, compile_plan, validate_plan_coverage,
    to_research_goal, to_query_routes)
from tcm.dataops.requirements_pipeline import run_requirements_v3
from tests.test_requirements_understanding import Sequence


def draft(objects, requirements, delivery, operation='new'):
    return RequirementsDraftV2.model_validate({
        'version': 'requirements-v2', 'status': 'ready', 'operation': operation,
        'objects': objects, 'requirements': requirements, 'delivery': delivery, 'unresolved': []})


def parent_goal(names, mode='audit_named', target_count=2, attributes=None, document_fields=None):
    return {'id': 'p', 'revision': 1, 'goal': {
        'query': {'names': names, 'filters': [], 'limit': 20},
        'attributes': attributes or ['medicinal_part'],
        'document_fields': document_fields or ['功能与主治', '注意'],
        'research': {'mode': mode, 'target_count': target_count, 'monograph_only': False}}}


class ParentConflictTests(unittest.TestCase):
    def _child(self, objects):
        return draft(objects,
                     [{'id': 'r1', 'kind': 'read_fields', 'object_refs': [o['ref'] for o in objects],
                       'fields': [{'namespace': 'pharmacopoeia', 'name': '贮藏'}]}],
                     {'form': 'table', 'row_policy': 'keep_all'}, operation='append_fields')

    def test_explicit_foreign_object_rejected(self):
        child = self._child([{'ref': 'o1', 'mention': 'Gamma'}])
        b = bind_requirements(child, {'Gamma': ['g']}, parent=parent_goal(['Alpha', 'Beta']))
        with self.assertRaises(ValueError):
            to_research_goal(b)

    def test_subset_object_rejected(self):
        child = self._child([{'ref': 'o1', 'mention': 'Alpha'}])
        b = bind_requirements(child, {'Alpha': ['a']}, parent=parent_goal(['Alpha', 'Beta']))
        with self.assertRaises(ValueError):
            to_research_goal(b)

    def test_normal_append_inherits_parent_objects(self):
        child = self._child([{'ref': 'o1', 'mention': 'Alpha'}, {'ref': 'o2', 'mention': 'Beta'}])
        b = bind_requirements(child, {'Alpha': ['a'], 'Beta': ['b']},
                              parent=parent_goal(['Alpha', 'Beta']))
        goal = to_research_goal(b)
        self.assertEqual(goal['query']['names'], ['Alpha', 'Beta'])

    def test_parent_object_order_does_not_matter(self):
        child = self._child([{'ref': 'o1', 'mention': 'Beta'}, {'ref': 'o2', 'mention': 'Alpha'}])
        b = bind_requirements(child, {'Beta': ['b'], 'Alpha': ['a']},
                              parent=parent_goal(['Alpha', 'Beta']))
        goal = to_research_goal(b)
        self.assertEqual(sorted(goal['query']['names']), ['Alpha', 'Beta'])


class PlanCoverageTests(unittest.TestCase):
    def _relation_bound(self):
        d = draft([{'ref': 'o1', 'mention': '甲'}, {'ref': 'o2', 'mention': '乙'},
                   {'ref': 'o3', 'mention': '丙'}, {'ref': 'o4', 'mention': '丁'}],
                  [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1', 'o2'], 'relation_kind': 'common_target'},
                   {'id': 'r2', 'kind': 'relation', 'object_refs': ['o3', 'o4'], 'relation_kind': 'common_target'}],
                  {'form': 'answer'})
        return bind_requirements(d, {'甲': ['a'], '乙': ['b'], '丙': ['c'], '丁': ['d']})

    def test_relation_pairing_swap_rejected(self):
        bound = self._relation_bound()
        plan = compile_plan(bound)
        for step in plan.steps:
            if step.arguments.get('object_refs') == ['o1', 'o2']:
                step.arguments['object_refs'] = ['o1', 'o3']
            elif step.arguments.get('object_refs') == ['o3', 'o4']:
                step.arguments['object_refs'] = ['o2', 'o4']
        self.assertFalse(validate_plan_coverage(bound, plan).ok)

    def test_relation_correct_pairing_ok(self):
        bound = self._relation_bound()
        plan = compile_plan(bound)
        self.assertTrue(validate_plan_coverage(bound, plan).ok)

    def test_answer_tool_swap_to_table_rejected(self):
        d = draft([{'ref': 'o1', 'mention': '黄芪'}],
                  [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                    'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                  {'form': 'answer'})
        bound = bind_requirements(d, {'黄芪': ['s']})
        plan = compile_plan(bound)
        plan.steps[0].tool = 'dataops_table'
        self.assertFalse(validate_plan_coverage(bound, plan).ok)

    def test_relation_tool_alias_routes_to_same_executor(self):
        d = draft([{'ref': 'o1', 'mention': 'Alpha'}],
                  [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'], 'relation_kind': 'target'}],
                  {'form': 'answer'})
        bound = bind_requirements(d, {'Alpha': ['a']})
        plan = compile_plan(bound)
        plan.steps[0].tool = 'assistant_etcm_relations'  # equivalent adapter for the same relation executor
        # assistant_etcm_relations accepts relation_kind, so it passes the tool check;
        # the declared covers still refer to r1 and the item stays consistent.
        # This case is therefore NOT automatically rejected; assert the plan still
        # routes to a real relation executor instead of silently dropping.
        routes = to_query_routes(bound, plan)
        self.assertEqual(routes[0]['tool'], 'assistant')
        self.assertEqual(routes[0]['request']['kind'], 'target')

    def test_adapter_raises_on_unhandled_tool(self):
        d = draft([{'ref': 'o1', 'mention': '黄芪'}],
                  [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                    'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                  {'form': 'answer'})
        bound = bind_requirements(d, {'黄芪': ['s']})
        plan = compile_plan(bound)
        plan.steps[0].tool = 'dataops_table'
        with self.assertRaises(ValueError):
            to_query_routes(bound, plan)


class PipelineTests(unittest.TestCase):
    def test_relation_pipeline_ready(self):
        rel = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
               'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
               'requirements': [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
                                 'relation_kind': 'target', 'completeness': 'page'}],
               'delivery': {'form': 'answer'}, 'unresolved': []}
        model = Sequence(rel, rel)
        r = run_requirements_v3(model, 'Alpha的靶点', {'Alpha': ['src']}, authorized=True)
        self.assertEqual(r['status'], 'ready')
        self.assertEqual(r['query_routes'][0]['request']['kind'], 'target')

    def test_dataset_pipeline_ready(self):
        ds = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
              'objects': [],
              'requirements': [{'id': 'r1', 'kind': 'dataset', 'datasets': ['etcm', 'pharmacopoeia']}],
              'delivery': {'form': 'answer'}, 'unresolved': []}
        model = Sequence(ds, ds)
        r = run_requirements_v3(model, '介绍两个数据库', {}, authorized=True)
        self.assertEqual(r['status'], 'ready')
        self.assertEqual(r['query_routes'][0]['request']['topic'], 'dataset_info')


class RuntimeMetadataTests(unittest.TestCase):
    def test_requirements_v3_is_reported_in_runtime_metadata(self):
        from unittest.mock import patch
        from tcm.capabilities import research_runtime
        with patch.dict('os.environ', {'DATAOPS_UNDERSTANDING_POLICY': 'requirements_v3'}):
            meta = research_runtime()
        self.assertEqual(meta['question_policy'], 'requirements_v3')
        self.assertEqual(meta['question_request_limit'], 2)

    def test_default_policy_is_unchanged(self):
        from unittest.mock import patch
        from tcm.capabilities import research_runtime
        with patch.dict('os.environ', {'DATAOPS_UNDERSTANDING_POLICY': 'local_then_one_model_v1'}):
            meta = research_runtime()
        self.assertEqual(meta['question_policy'], 'local_then_one_model_v1')
        self.assertEqual(meta['question_request_limit'], 1)


class RouteContractTests(unittest.TestCase):
    """to_query_routes must produce the same route shape the existing planner emits."""

    def test_herb_route_matches_existing_planner_contract(self):
        from tcm.intent_planner import QueryIntent, mapped
        d = draft([{'ref': 'o1', 'mention': 'Alpha'}],
                  [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                    'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
                  {'form': 'answer'})
        bound = bind_requirements(d, {'Alpha': ['sid-a']})
        ours = to_query_routes(bound, compile_plan(bound))[0]
        planner = mapped(QueryIntent(action='herb_field', entities=['Alpha'], fields=['medicinal_part']),
                         'Alpha的药用部位', known_names={'Alpha'})
        self.assertEqual(ours['tool'], planner['tool'])
        self.assertEqual(set(ours['request']), set(planner['request']))
        self.assertEqual(ours['request']['name'], planner['request']['name'])
        self.assertEqual(ours['request']['field'], planner['request']['field'])

    def test_relation_route_matches_existing_planner_contract(self):
        from tcm.intent_planner import QueryIntent, mapped
        d = draft([{'ref': 'o1', 'mention': 'Alpha'}],
                  [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'], 'relation_kind': 'target'}],
                  {'form': 'answer'})
        bound = bind_requirements(d, {'Alpha': ['sid-a']})
        ours = to_query_routes(bound, compile_plan(bound))[0]
        planner = mapped(QueryIntent(action='relation', entities=['Alpha'], relation_kind='target'),
                         'Alpha的靶点', known_names={'Alpha'})
        self.assertEqual(ours['tool'], planner['tool'])
        self.assertEqual(ours['request']['topic'], planner['request']['topic'])
        self.assertEqual(ours['request']['kind'], planner['request']['kind'])
        self.assertEqual(ours['request']['entities'], planner['request']['entities'])


if __name__ == "__main__":
    unittest.main()
