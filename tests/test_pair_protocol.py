"""A/C share hard completion gates, source scope and cumulative budgets."""
import copy
import json
import unittest

from scripts.check_pair_protocol import synthetic_fixture, backend_for
from experiments.architecture_candidates.pair_protocol import PairProtocol, Driver, drivers


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = synthetic_fixture()
        self.fixture = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    async def execute(self, candidate='A_protocol', actions=None, backend=None, children=None, **kwargs):
        sdk, default_children = drivers(candidate)
        if actions is not None:
            sdk = Driver(actions)
        protocol = PairProtocol(('Alpha', 'Beta'), '功能与主治、注意事项和全部共同靶点',
                                backend or backend_for(self.fixture), **kwargs)
        result = await protocol.run(candidate, sdk, children if children is not None else default_children)
        return result, sdk, default_children

    async def test_both_protocols_complete_same_real_results(self):
        results = []
        for candidate in ('A_protocol', 'C_protocol'):
            result, _, children = await self.execute(candidate)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['tool_calls'], 6)
            self.assertEqual([len(p['items']) for p in result['pages']], [20, 3])
            self.assertEqual(result['targets']['returned_count'], 23)
            for herb, driver in children.items():
                self.assertNotIn('Beta' if herb == 'Alpha' else 'Alpha', json.dumps(driver.inputs))
            results.append(result)
        self.assertEqual(results[0]['targets'], results[1]['targets'])
        self.assertEqual([r['model_substitute_calls'] for r in results], [7, 11])

    async def test_early_finish_rejected(self):
        result, _, _ = await self.execute(actions=[('finish', {'evidence_ids': []})])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['tool_calls'], 0)

    async def test_first_page_is_not_all_targets(self):
        sdk, _ = drivers('A_protocol', pages=1)
        result, _, _ = await self.execute(actions=list(sdk.actions))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['reason'], 'incomplete_or_invalid_citations')

    async def test_skip_page_rejected_before_dispatch(self):
        sdk, _ = drivers('A_protocol')
        actions = list(sdk.actions)
        actions[4] = ('shared_targets', {'page': 2})
        result, _, _ = await self.execute(actions=actions)
        self.assertEqual(result['reason'], 'page_sequence')
        self.assertEqual(result['tool_calls'], 4)

    async def test_child_cannot_read_sibling(self):
        _, children = drivers('C_protocol')
        children['Alpha'] = Driver([('read_source', {'herb': 'Beta', 'branch': 'text'})])
        result, _, _ = await self.execute('C_protocol', children=children)
        self.assertEqual(result['reason'], 'source_scope')
        self.assertEqual(result['tool_calls'], 0)

    async def test_model_budget_includes_children(self):
        result, _, _ = await self.execute('C_protocol', model_limit=4)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['model_substitute_calls'], 4)
        self.assertEqual(result['tool_calls'], 2)

    async def test_tool_budget_prevents_second_page(self):
        result, _, _ = await self.execute(tool_limit=5)
        self.assertEqual(result['reason'], 'tool_budget')
        self.assertEqual(len(result['pages']), 1)

    async def test_missing_fields_never_complete(self):
        for candidate in ('A_protocol', 'C_protocol'):
            result, _, _ = await self.execute(candidate, backend=backend_for(self.fixture, 'missing_field'))
            self.assertEqual(result['status'], 'partial')

    async def test_missing_source_prevents_target_query(self):
        result, _, _ = await self.execute(backend=backend_for(self.fixture, 'unavailable_side'))
        self.assertEqual(result['reason'], 'unresolved_sources')
        self.assertEqual(result['tool_calls'], 4)

    async def test_forged_finish_citation_rejected(self):
        sdk, _ = drivers('A_protocol')
        actions = list(sdk.actions)
        actions[-1][1]['evidence_ids'].append('fake:1')
        result, _, _ = await self.execute(actions=actions)
        self.assertEqual(result['reason'], 'incomplete_or_invalid_citations')

    async def test_changed_or_repeated_page_rejected(self):
        for mutation in ('duplicate', 'total', 'source'):
            original = backend_for(self.fixture)
            first = None
            async def backend(name, args):
                nonlocal first
                value = await original(name, args)
                if name == 'shared_targets':
                    if args['page'] == 1:
                        first = copy.deepcopy(value)
                    elif mutation == 'duplicate':
                        value['items'][0] = first['items'][0]
                    elif mutation == 'total':
                        value['total'] += 1
                    else:
                        value['source_ids'][0] = 'forged'
                return value
            result, _, _ = await self.execute(backend=backend)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(len(result['pages']), 1)

    async def test_snapshot_change_stops_read(self):
        backend = backend_for(self.fixture)
        import os
        path = self.fixture.database
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        result, _, _ = await self.execute(backend=backend)
        self.assertEqual(result['reason'], 'snapshot_changed')


if __name__ == '__main__':
    unittest.main()
