"""U4 pipeline + answer route adapter tests (SDK stub, no real model)."""
import unittest
from langchain_core.messages import AIMessage
from tcm.dataops.requirements_pipeline import run_requirements_v3
from tcm.dataops.requirements_planner import to_query_routes


def good_table_draft():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': '白术'}, {'ref': 'o2', 'mention': '黄芪'}],
            'requirements': [
                {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1', 'o2'],
                 'fields': [{'namespace': 'pharmacopoeia', 'name': '功能与主治'},
                            {'namespace': 'pharmacopoeia', 'name': '注意'}]},
            ],
            'delivery': {'form': 'table', 'include_evidence': True, 'row_policy': 'keep_all'},
            'unresolved': []}


def good_answer_draft():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': '黄芪'}],
            'requirements': [
                {'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                 'fields': [{'namespace': 'etcm', 'name': 'medicinal_part'},
                            {'namespace': 'pharmacopoeia', 'name': '功能与主治'}]},
            ],
            'delivery': {'form': 'answer', 'include_evidence': True},
            'unresolved': []}


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


class PipelineTests(unittest.TestCase):
    def test_table_chain_delivers_goal(self):
        model = Sequence(good_table_draft(), good_table_draft())  # initial + review
        result = run_requirements_v3(model, '整理白术和黄芪的功效、注意，生成表格',
                                     {'白术': ['a'], '黄芪': ['b']}, authorized=True)
        self.assertEqual(result['status'], 'ready')
        self.assertIn('goal', result)
        self.assertEqual(result['goal']['research']['mode'], 'audit_named')
        self.assertEqual(result['goal']['query']['names'], ['白术', '黄芪'])

    def test_answer_chain_delivers_query_routes(self):
        model = Sequence(good_answer_draft(), good_answer_draft())
        result = run_requirements_v3(model, '黄芪的药用部位和功效', {'黄芪': ['a']}, authorized=True)
        self.assertEqual(result['status'], 'ready')
        self.assertIn('query_routes', result)
        tools = [r['tool'] for r in result['query_routes']]
        self.assertIn('herb', tools)       # medicinal_part
        self.assertIn('research', tools)   # 功能与主治

    def test_answer_routes_cover_both_fields(self):
        from tcm.dataops.requirements_v2 import RequirementsDraftV2
        from tcm.dataops.requirements_planner import bind_requirements, compile_plan
        d = RequirementsDraftV2.model_validate(good_answer_draft())
        bound = bind_requirements(d, {'黄芪': ['a']})
        plan = compile_plan(bound)
        routes = to_query_routes(bound, plan)
        fields = [r['request'].get('field') for r in routes if r['tool'] == 'herb']
        questions = [r['request'].get('question') for r in routes if r['tool'] == 'research']
        self.assertIn('medicinal_part', fields)
        self.assertTrue(any('功能与主治' in q for q in questions))

    def test_unsupported_delivery(self):
        bad = {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
               'objects': [{'ref': 'o1', 'mention': '白术'}],
               'requirements': [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
                                 'relation_kind': 'ingredient'}],
               'delivery': {'form': 'table', 'include_evidence': True, 'row_policy': 'keep_all'},
               'unresolved': []}
        model = Sequence(bad, bad)
        result = run_requirements_v3(model, '把白术的成分放进表格', {'白术': ['a']}, authorized=True)
        self.assertEqual(result['status'], 'unsupported_delivery')


if __name__ == '__main__':
    unittest.main()
