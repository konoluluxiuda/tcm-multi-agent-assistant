"""Run explicitly in the optional candidate environment; production CI needs no Deep Agents."""
import unittest

from scripts.check_pair_protocol import synthetic_fixture, backend_for
from experiments.architecture_candidates.pair_protocol import PairProtocol, Driver, drivers
from experiments.architecture_candidates.deepagents_pair import run_deepagents_pair


class NativePairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = synthetic_fixture()
        self.fixture = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    async def run_case(self, *, actions=None, fault=None, **limits):
        sdk, _ = drivers('A_protocol')
        if actions is not None:
            sdk = Driver(actions)
        protocol = PairProtocol(('Alpha', 'Beta'), '比较功能与主治、注意事项和全部共同靶点',
                                backend_for(self.fixture, fault), **limits)
        return await run_deepagents_pair(protocol, sdk), sdk

    async def test_native_tools_finish_without_extra_model_round(self):
        result, sdk = await self.run_case()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual((result['model_substitute_calls'], result['tool_calls']), (7, 6))
        self.assertEqual(len(result['targets']['items']), 23)
        self.assertEqual(set(result['model_tools_exposed']), {'read_source', 'shared_targets', 'finish'})
        self.assertGreater(len(result['native_tools_offered']), 3)
        self.assertEqual(len(sdk.inputs), 7)

    async def test_early_finish_blocks_completion(self):
        result, sdk = await self.run_case(actions=[('finish', {'evidence_ids': []})])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(len(sdk.inputs), 1)

    async def test_native_filesystem_tool_cannot_execute(self):
        result, _ = await self.run_case(actions=[('read_file', {'file_path': '/synthetic/out-of-scope'})])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['reason'], 'invalid_action')
        self.assertEqual(result['tool_calls'], 0)

    async def test_out_of_scope_herb_is_blocked_before_query(self):
        result, sdk = await self.run_case(actions=[('read_source', {'herb': 'Other', 'branch': 'text'})])
        self.assertEqual(result['reason'], 'source_scope')
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(len(sdk.inputs), 1)

    async def test_invalid_schema_does_not_trigger_retry(self):
        result, sdk = await self.run_case(actions=[('shared_targets', {'page': 0})])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(len(sdk.inputs), 1)

    async def test_model_budget_is_shared_with_native_runtime(self):
        result, sdk = await self.run_case(model_limit=4)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['model_substitute_calls'], 4)
        self.assertEqual(len(sdk.inputs), 4)

    async def test_tool_budget_keeps_first_page(self):
        result, sdk = await self.run_case(tool_limit=5)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['tool_calls'], 5)
        self.assertEqual(len(result['pages']), 1)
        self.assertEqual(len(sdk.inputs), 6)

    async def test_source_failure_stops_without_pair_query(self):
        result, sdk = await self.run_case(fault='unavailable_side')
        self.assertEqual(result['reason'], 'unresolved_sources')
        self.assertEqual(result['tool_calls'], 4)
        self.assertEqual(len(sdk.inputs), 5)


if __name__ == '__main__':
    unittest.main()
