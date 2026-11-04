"""U3 understanding tests: two-request budget with SDK stubs (no real model)."""
import json
import unittest
from langchain_core.messages import AIMessage
from tcm.dataops.requirements_understanding import (
    request_requirements, submit_requirements_v2_tool, GoalResponseRejected, normalize,
)
from tcm.dataops.requirements_v2 import RequirementsDraftV2


def good_draft():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': '黄芪'}],
            'requirements': [
                {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                 'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'}]},
            ],
            'delivery': {'form': 'answer', 'include_evidence': True},
            'unresolved': []}


def invalid_draft():
    d = good_draft()
    del d['requirements'][0]['fields']
    return d


class Sequence:
    max_retries = 0

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages):
        self.calls += 1
        if not self.replies:
            raise AssertionError('Unexpected extra model request')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        tool, args = reply if isinstance(reply, tuple) else ('submit_requirements_v2', reply)
        return AIMessage(content='', tool_calls=[{'id': str(self.calls), 'name': tool, 'args': args}],
                         usage_metadata={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12})


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


class UnderstandingTests(unittest.TestCase):
    def test_accept_after_independent_agreement(self):
        model = Sequence(good_draft(), good_draft())
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['external_model_calls'], 2)
        self.assertEqual(model.calls, 2)

    def test_correction_exhausts_budget_and_clarifies(self):
        # Correction consumes the review budget; without independent semantic
        # basis the corrected result cannot be trusted -> clarify.
        model = Sequence(invalid_draft(), good_draft())
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['failure']['code'], 'semantic_review_budget_exhausted')
        self.assertEqual(result['external_model_calls'], 2)
        self.assertEqual(model.calls, 2)

    def test_correction_failure_accounting(self):
        model = Sequence(invalid_draft(), invalid_draft())
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['failure']['code'], 'correction_exhausted')
        self.assertEqual(result['external_model_calls'], 2)
        self.assertEqual(result['usage']['total_tokens'], 24)

    def test_review_failure_accounting(self):
        model = Sequence(good_draft(), invalid_draft())
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['failure']['code'], 'semantic_review_failed')
        self.assertEqual(result['external_model_calls'], 2)
        self.assertEqual(result['usage']['total_tokens'], 24)

    def test_review_unknown_dispatch_propagates(self):
        model = Sequence(good_draft(), TimeoutError('unknown'))
        with self.assertRaises(TimeoutError):
            request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(model.calls, 2)

    def test_wrong_semantics_after_correction_clarifies(self):
        wrong = good_draft()
        wrong['requirements'][0]['fields'] = [{'namespace': 'etcm', 'name': 'family'}]
        model = Sequence(invalid_draft(), wrong)
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'needs_input')
        self.assertNotEqual(result['failure']['code'], 'requirements_unresolved')

    def test_review_disagreement(self):
        other = {**good_draft(),
                 'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                                   'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'}]}]}
        model = Sequence(good_draft(), other)
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['failure']['code'], 'semantic_disagreement')

    def test_unknown_dispatch_propagates(self):
        model = Sequence(TimeoutError('unknown'))
        with self.assertRaises(TimeoutError):
            request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(model.calls, 1)

    def test_unauthorized_raises(self):
        model = Sequence(good_draft())
        with self.assertRaises(PermissionError):
            request_requirements(model, 'x', {'黄芪': []}, authorized=False)
        self.assertEqual(model.calls, 0)

    def test_tool_schema_is_strict(self):
        schema = submit_requirements_v2_tool()['function']['parameters']
        self.assertEqual(schema.get('additionalProperties'), False)

    def test_same_semantics_different_refs(self):
        renamed = good_draft()
        renamed['objects'][0]['ref'] = 'o9'
        renamed['requirements'][0]['id'] = 'r9'
        renamed['requirements'][0]['object_refs'] = ['o9']
        model = Sequence(good_draft(), renamed)
        result = request_requirements(model, '黄芪有什么作用', {'黄芪': ['src1']}, authorized=True)
        self.assertEqual(result['status'], 'ready')

    def test_model_context_sends_catalog_parent_feedback(self):
        model = Capture(invalid_draft(), good_draft())
        request_requirements(model, '增加贮藏', {'CATALOG_SENTINEL': ['src']},
                             parent={'goal': {'document_fields': ['PARENT_FIELD_SENTINEL']}},
                             authorized=True)
        payload = json.dumps(model.payloads, ensure_ascii=False)
        self.assertIn('CATALOG_SENTINEL', payload)
        self.assertIn('PARENT_FIELD_SENTINEL', payload)
        self.assertIn('requirements_schema_failed', payload)


if __name__ == '__main__':
    unittest.main()
