"""U1 contract tests: strict schema, discriminator, refs, delivery, binding."""
import unittest
from pydantic import ValidationError
from tcm.dataops.requirements_v2 import (
    RequirementsDraftV2, BoundRequirementsV2, BoundObject, FieldRef, VERSION,
)


def ready_draft(**overrides):
    base = {
        'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
        'objects': [{'ref': 'o1', 'mention': '黄芪'}],
        'requirements': [
            {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
             'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]},
        ],
        'delivery': {'form': 'answer', 'include_evidence': True},
        'unresolved': [],
    }
    base.update(overrides)
    return base


class ContractTests(unittest.TestCase):
    def test_version_is_fixed(self):
        self.assertEqual(VERSION, 'requirements-v2')

    def test_valid_answer_draft(self):
        d = RequirementsDraftV2(**ready_draft())
        self.assertEqual(d.status, 'ready')
        self.assertEqual(d.requirements[0].kind, 'read_fields')

    def test_unknown_top_level_key_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**{**ready_draft(), 'bogus': 1})

    def test_unknown_field_key_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(
                requirements=[{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                               'fields': [{'namespace': 'etcm', 'name': 'medicinal_part', 'x': 1}]}]))

    def test_unknown_object_ref_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(
                requirements=[{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o9'],
                               'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}]))

    def test_duplicate_requirement_id_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(requirements=[
                {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'], 'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]},
                {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'], 'fields': [{'namespace': 'etcm', 'name': 'family'}]},
            ]))

    def test_dataset_no_names_allowed(self):
        d = RequirementsDraftV2(**{
            'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [],
            'requirements': [{'id': 'r1', 'kind': 'dataset', 'datasets': ['etcm', 'pharmacopoeia']}],
            'delivery': {'form': 'answer'}, 'unresolved': [],
        })
        self.assertEqual(d.objects, [])

    def test_no_objects_without_dataset_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**{
                'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
                'objects': [], 'requirements': [],
                'delivery': {'form': 'answer'}, 'unresolved': [],
            })

    def test_needs_input_requires_unresolved(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(status='needs_input'))

    def test_needs_input_cannot_carry_requirements(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(status='needs_input', unresolved=['请明确对象']))

    def test_ready_cannot_carry_unresolved(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(unresolved=['x']))

    def test_table_requires_row_policy(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(delivery={'form': 'table'}))

    def test_answer_cannot_carry_row_policy(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(delivery={'form': 'answer', 'row_policy': 'keep_all'}))

    def test_append_fields_requires_table(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(operation='append_fields'))

    def test_discriminator_rejects_wrong_kind_shape(self):
        # read_fields without fields fails its own model.
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(
                requirements=[{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1']}]))

    def test_relation_kind(self):
        d = RequirementsDraftV2(**ready_draft(requirements=[
            {'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
             'relation_kind': 'ingredient', 'completeness': 'page'}]))
        self.assertEqual(d.requirements[0].kind, 'relation')
        self.assertEqual(d.requirements[0].relation_kind, 'ingredient')


class BindingTests(unittest.TestCase):
    def _bound(self):
        return BoundRequirementsV2(
            version='requirements-v2',
            draft=RequirementsDraftV2(**ready_draft()),
            objects=[BoundObject(ref='o1', mention='黄芪', name_candidates=['黄芪'],
                                 selected_source_ids=['etcm2-local:1'])],
            data_version='local-2026-10-10', owner='abc',
        )

    def test_bound_objects_match_draft(self):
        b = self._bound()
        self.assertEqual(b.objects[0].ref, 'o1')

    def test_bound_missing_object_rejected(self):
        with self.assertRaises(ValidationError):
            BoundRequirementsV2(
                version='requirements-v2',
                draft=RequirementsDraftV2(**ready_draft()),
                objects=[], data_version='local', owner='abc',
            )

    def test_bound_extra_object_rejected(self):
        with self.assertRaises(ValidationError):
            BoundRequirementsV2(
                version='requirements-v2',
                draft=RequirementsDraftV2(**ready_draft()),
                objects=[BoundObject(ref='o1', mention='黄芪'),
                         BoundObject(ref='o2', mention='白术')],
                data_version='local', owner='abc',
            )

    def test_identity_defaults_unverified(self):
        b = self._bound()
        self.assertFalse(b.objects[0].identity_verified)


class ReviewProbeTests(unittest.TestCase):
    """Regressions for the independent review's U1 schema probes."""

    def test_clarification_without_objects(self):
        d = RequirementsDraftV2.model_validate({
            'version': 'requirements-v2', 'status': 'needs_input', 'operation': 'new',
            'objects': [], 'requirements': [],
            'delivery': {'form': 'answer'}, 'unresolved': ['请提供对象或任务目标']})
        self.assertEqual(d.status, 'needs_input')

    def test_mixed_dataset_and_herb(self):
        base = ready_draft()
        d = RequirementsDraftV2.model_validate({**base,
            'requirements': [base['requirements'][0],
                             {'id': 'r2', 'kind': 'dataset', 'datasets': ['etcm']}]})
        self.assertEqual(len(d.requirements), 2)

    def test_duplicate_object_ref_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**{**ready_draft(),
                'objects': [{'ref': 'o1', 'mention': '黄芪'}, {'ref': 'o1', 'mention': '白术'}]})

    def test_ready_empty_requirements_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(requirements=[]))

    def test_top_n_missing_count_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(delivery={'form': 'table', 'row_policy': 'top_n'}))

    def test_count_on_keep_all_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementsDraftV2(**ready_draft(
                delivery={'form': 'table', 'row_policy': 'keep_all', 'count': 2}))


if __name__ == "__main__":
    unittest.main()
