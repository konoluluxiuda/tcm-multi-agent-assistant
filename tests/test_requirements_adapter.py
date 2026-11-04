"""U4 adapter tests: new contract -> existing ResearchGoal (table delivery)."""
import unittest
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.requirements_planner import (
    bind_requirements, validate_requirements, to_research_goal,
)
from tcm.dataops.research_contracts import ResearchGoal


def table_draft(objects, requirements, row_policy='keep_all', count=None):
    delivery = {'form': 'table', 'include_evidence': True, 'row_policy': row_policy}
    if count is not None:
        delivery['count'] = count
    return RequirementsDraftV2(
        version='requirements-v2', status='ready', operation='new',
        objects=objects, requirements=requirements, delivery=delivery, unresolved=[],
    )


class AdapterTests(unittest.TestCase):
    def test_keep_all_maps_to_audit_named(self):
        d = table_draft(
            [{'ref': 'o1', 'mention': '白术'}, {'ref': 'o2', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
              'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'},
                         {'namespace': 'pharmacopoeia', 'name': '注意'}]}],
            row_policy='keep_all',
        )
        bound = bind_requirements(d, {'白术': ['a'], '黄芪': ['b']})
        goal = to_research_goal(bound)
        self.assertEqual(goal['research']['mode'], 'audit_named')
        self.assertEqual(goal['research']['target_count'], 2)
        self.assertEqual(goal['query']['names'], ['白术', '黄芪'])
        self.assertEqual(goal['document_fields'], ['功能与主治', '注意'])
        # Validate against the real ResearchGoal contract.
        ResearchGoal.model_validate(goal)

    def test_top_n_maps_to_select_complete(self):
        d = table_draft(
            [{'ref': 'o1', 'mention': '白术'}, {'ref': 'o2', 'mention': '黄芪'},
             {'ref': 'o3', 'mention': '当归'}, {'ref': 'o4', 'mention': '川芎'},
             {'ref': 'o5', 'mention': '甘草'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2', 'o3', 'o4', 'o5'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                         {'namespace': 'pharmacopoeia', 'name': '功能与主治'},
                         {'namespace': 'pharmacopoeia', 'name': '注意'}]}],
            row_policy='top_n', count=2,
        )
        bound = bind_requirements(d, {o.mention: [o.mention] for o in d.objects})
        goal = to_research_goal(bound)
        self.assertEqual(goal['research']['mode'], 'select_complete')
        self.assertEqual(goal['research']['target_count'], 2)
        ResearchGoal.model_validate(goal)

    def test_filter_complete(self):
        d = table_draft(
            [{'ref': 'o1', 'mention': '白术'}, {'ref': 'o2', 'mention': '黄芪'}],
            [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
              'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                         {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
            row_policy='filter_complete',
        )
        bound = bind_requirements(d, {'白术': ['a'], '黄芪': ['b']})
        goal = to_research_goal(bound)
        self.assertEqual(goal['research']['mode'], 'filter_complete')
        self.assertEqual(goal['research']['target_count'], 2)
        ResearchGoal.model_validate(goal)

    def test_answer_delivery_rejected_by_adapter(self):
        d = RequirementsDraftV2(
            version='requirements-v2', status='ready', operation='new',
            objects=[{'ref': 'o1', 'mention': '黄芪'}],
            requirements=[{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                           'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]}],
            delivery={'form': 'answer'}, unresolved=[],
        )
        bound = bind_requirements(d, {'黄芪': ['a']})
        with self.assertRaises(ValueError):
            to_research_goal(bound)


if __name__ == '__main__':
    unittest.main()
