"""U2 planner tests: bind, validate, compile, coverage without any question string."""
import unittest
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.requirements_planner import (
    bind_requirements, validate_requirements, compile_plan, validate_plan_coverage,
    to_research_goal, to_query_routes,
)


def draft(objects, requirements, delivery='answer', operation='new'):
    return RequirementsDraftV2(
        version='requirements-v2', status='ready', operation=operation,
        objects=objects, requirements=requirements,
        delivery={'form': delivery, 'include_evidence': True,
                  **(dict(row_policy='keep_all') if delivery == 'table' else {})},
        unresolved=[],
    )


class PlannerTests(unittest.TestCase):
    def test_bind_mentions_to_candidates(self):
        d = draft(
            [{'ref': 'o1', 'mention': '黄芪'}, {'ref': 'o2', 'mention': '白术'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
        )
        bound = bind_requirements(d, {'黄芪': ['src1'], '白术': ['src2', 'src3']})
        self.assertEqual(bound.objects[0].name_candidates, ['src1'])
        self.assertEqual(bound.objects[1].name_candidates, ['src2', 'src3'])
        self.assertEqual(bound.objects[0].selected_source_ids, ['src1'])  # unique candidate
        self.assertEqual(bound.objects[1].selected_source_ids, [])        # ambiguous

    def test_validate_valid_answer(self):
        d = draft(
            [{'ref': 'o1', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                         {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
        )
        r = validate_requirements(bind_requirements(d, {'黄芪': ['src1']}))
        self.assertTrue(r.ok, r.errors)

    def test_validate_ingredient_in_table_rejected(self):
        d = draft(
            [{'ref': 'o1', 'mention': '白术'}],
            [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
              'relation_kind': 'ingredient', 'completeness': 'page'}],
            delivery='table',
        )
        r = validate_requirements(bind_requirements(d, {'白术': ['src1']}))
        self.assertFalse(r.ok)
        self.assertTrue(any('table_cell' in e or 'ingredient' in e for e in r.errors), r.errors)

    def test_validate_unknown_field_rejected(self):
        d = draft(
            [{'ref': 'o1', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'not_a_field'}]}],
        )
        r = validate_requirements(bind_requirements(d, {'黄芪': ['src1']}))
        self.assertFalse(r.ok)

    def test_common_target_requires_two_objects(self):
        d = draft(
            [{'ref': 'o1', 'mention': '当归'}],
            [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
              'relation_kind': 'common_target'}],
        )
        r = validate_requirements(bind_requirements(d, {'当归': ['src1']}))
        self.assertFalse(r.ok)
        self.assertTrue(any('two object' in e for e in r.errors))

    def test_compile_and_full_coverage(self):
        d = draft(
            [{'ref': 'o1', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                         {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
        )
        bound = bind_requirements(d, {'黄芪': ['src1']})
        plan = compile_plan(bound)
        self.assertEqual(len(plan.steps), 2, plan)
        self.assertEqual(validate_plan_coverage(bound, plan).ok, True)

    def test_deleted_step_detected(self):
        d = draft(
            [{'ref': 'o1', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                         {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
        )
        bound = bind_requirements(d, {'黄芪': ['src1']})
        plan = compile_plan(bound)
        plan.steps = plan.steps[:1]  # drop the pharmacopoeia step
        cov = validate_plan_coverage(bound, plan)
        self.assertFalse(cov.ok)
        self.assertEqual(len(cov.missing), 1)
        self.assertEqual(cov.missing[0].name, '功能与主治')

    def test_swapped_object_detected(self):
        # 白术 needs family, 黄芪 needs 功能与主治. Swapping objects must not count as coverage.
        d = draft(
            [{'ref': 'o1', 'mention': '白术'}, {'ref': 'o2', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'family'}]},
             {'id': 'r2', 'kind': 'read_fields', 'object_refs': ['o2'],
              'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
        )
        bound = bind_requirements(d, {'白术': ['a'], '黄芪': ['b']})
        plan = compile_plan(bound)
        # Corrupt: point the family step at o2 and the 功能与主治 step at o1.
        for step in plan.steps:
            if step.arguments['name'] == 'family':
                step.arguments['object_ref'] = 'o2'
                step.covers = [step.covers[0].model_copy(update={'object_ref': 'o2'})]
            elif step.arguments['name'] == '功能与主治':
                step.arguments['object_ref'] = 'o1'
                step.covers = [step.covers[0].model_copy(update={'object_ref': 'o1'})]
        cov = validate_plan_coverage(bound, plan)
        self.assertFalse(cov.ok)
        self.assertEqual(len(cov.missing), 2, cov.missing)

    def test_extra_step_detected(self):
        d = draft(
            [{'ref': 'o1', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}],
        )
        bound = bind_requirements(d, {'黄芪': ['src1']})
        plan = compile_plan(bound)
        # Inject an unrequested step covering a different field.
        from tcm.dataops.requirements_planner import PlanStep, Coverage
        plan.steps.append(PlanStep(step_id='s99', tool='herb_attributes',
                                   arguments={'object_ref': 'o1', 'namespace': 'etcm', 'name': 'family'},
                                   covers=[Coverage(requirement_id='r1', object_ref='o1',
                                                    namespace='etcm', name='family')]))
        cov = validate_plan_coverage(bound, plan)
        self.assertFalse(cov.ok)
        self.assertTrue(any('unrequested' in e for e in cov.errors))


class ReviewCoverageTests(unittest.TestCase):
    """Regressions for the review's coverage/table/registry probes."""

    def _bound(self):
        from tcm.dataops.requirements_v2 import RequirementsDraftV2
        d = RequirementsDraftV2.model_validate({
            'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': '黄芪'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                              'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []})
        return bind_requirements(d, {'黄芪': ['src']})

    def test_coverage_arguments_mutation_rejected(self):
        bound = self._bound()
        plan = compile_plan(bound)
        plan.steps[0].arguments['object_ref'] = 'o999'
        plan.steps[0].arguments['name'] = '注意'
        self.assertFalse(validate_plan_coverage(bound, plan).ok)

    def test_coverage_tool_mutation_rejected(self):
        bound = self._bound()
        plan = compile_plan(bound)
        plan.steps[0].tool = 'nonexistent_tool'
        self.assertFalse(validate_plan_coverage(bound, plan).ok)

    def test_coverage_duplicate_step_rejected(self):
        bound = self._bound()
        plan = compile_plan(bound)
        plan.steps.append(plan.steps[0].model_copy(deep=True))
        self.assertFalse(validate_plan_coverage(bound, plan).ok)

    def test_coverage_extra_step_rejected(self):
        bound = self._bound()
        plan = compile_plan(bound)
        from tcm.dataops.requirements_planner import PlanStep
        plan.steps.append(PlanStep(step_id='extra', tool='nonexistent_tool', covers=[]))
        self.assertFalse(validate_plan_coverage(bound, plan).ok)


class ReviewTableTests(unittest.TestCase):
    def _draft(self, objects, requirements, delivery, operation='new'):
        from tcm.dataops.requirements_v2 import RequirementsDraftV2
        return RequirementsDraftV2.model_validate({
            'version': 'requirements-v2', 'status': 'ready', 'operation': operation,
            'objects': objects, 'requirements': requirements, 'delivery': delivery, 'unresolved': []})

    def test_append_merges_parent_fields(self):
        d = self._draft([{'ref': 'o1', 'mention': 'Herb1'}],
                        [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                          'fields': [{'namespace': 'pharmacopoeia', 'name': '贮藏'}]}],
                        {'form': 'table', 'row_policy': 'keep_all'}, operation='append_fields')
        parent = {'id': 'p', 'revision': 1,
                  'goal': {'query': {'names': ['Herb1'], 'filters': [], 'limit': 20},
                           'document_fields': ['功能与主治', '注意'], 'attributes': ['family'],
                           'research': {'mode': 'audit_named', 'target_count': 1, 'monograph_only': False}}}
        goal = to_research_goal(bind_requirements(d, {'Herb1': ['src']}, parent=parent))
        self.assertEqual(goal['document_fields'], ['功能与主治', '注意', '贮藏'])
        self.assertEqual(goal['attributes'], ['family'])

    def test_per_object_field_difference_rejected(self):
        d = self._draft([{'ref': 'o1', 'mention': '黄芪'}, {'ref': 'o2', 'mention': '白术'}],
                        [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                          'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]},
                         {'id': 'r2', 'kind': 'read_fields', 'object_refs': ['o2'],
                          'fields': [{'namespace': 'etcm', 'name': 'family'}]}],
                        {'form': 'table', 'row_policy': 'filter_complete'})
        b = bind_requirements(d, {'黄芪': ['a'], '白术': ['b']})
        with self.assertRaises(ValueError):
            to_research_goal(b)

    def test_six_objects_rejected_by_real_goal(self):
        objs = [{'ref': f'o{i+1}', 'mention': f'Herb{i+1}'} for i in range(6)]
        d = self._draft(objs,
                        [{'id': 'r1', 'kind': 'read_fields', 'object_refs': [o['ref'] for o in objs],
                          'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
                        {'form': 'table', 'row_policy': 'keep_all'})
        b = bind_requirements(d, {o['mention']: ['src'] for o in objs})
        with self.assertRaises(Exception):
            to_research_goal(b)

    def test_common_target_uses_distinct_tool(self):
        d = self._draft([{'ref': 'o1', 'mention': '当归'}, {'ref': 'o2', 'mention': '川芎'}],
                        [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1', 'o2'],
                          'relation_kind': 'common_target', 'completeness': 'page'}],
                        {'form': 'answer'})
        b = bind_requirements(d, {'当归': ['a'], '川芎': ['b']})
        self.assertTrue(validate_requirements(b).ok)
        plan = compile_plan(b)
        self.assertEqual(plan.steps[0].tool, 'assistant_etcm_relations')
        routes = to_query_routes(b, plan)
        self.assertEqual(routes[0]['request']['kind'], 'common_target')
        self.assertEqual(routes[0]['request']['entities'], ['当归', '川芎'])

    def test_all_pages_rejected_before_execution(self):
        d = self._draft([{'ref': 'o1', 'mention': '当归'}],
                        [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
                          'relation_kind': 'target', 'completeness': 'all'}],
                        {'form': 'answer'})
        b = bind_requirements(d, {'当归': ['a']})
        self.assertFalse(validate_requirements(b).ok)


class ReviewBindingTests(unittest.TestCase):
    def test_no_candidate_rejected(self):
        from tcm.dataops.requirements_v2 import RequirementsDraftV2
        d = RequirementsDraftV2.model_validate({
            'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': '黄芪'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                              'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []})
        b = bind_requirements(d, {})
        self.assertFalse(validate_requirements(b).ok)

    def test_ambiguous_candidate_rejected(self):
        from tcm.dataops.requirements_v2 import RequirementsDraftV2
        d = RequirementsDraftV2.model_validate({
            'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': '黄芪'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                              'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []})
        b = bind_requirements(d, {'黄芪': ['a', 'b']})
        self.assertFalse(validate_requirements(b).ok)


if __name__ == "__main__":
    unittest.main()
