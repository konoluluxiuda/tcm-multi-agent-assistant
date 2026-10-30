"""Production pair graph: full pagination, partial preservation and checkpoint resume."""
import asyncio
import json
from collections import Counter
from unittest.mock import patch
import unittest
from langgraph.checkpoint.memory import MemorySaver

from scripts.check_pair_protocol import synthetic_fixture, backend_for, verify_raw_witnesses
from tcm.cross_source_report import run_report, local_branch
from tcm.pair_research_report import run_pair_report, requires_all_targets


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = synthetic_fixture()
        self.fixture = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.local = backend_for(self.fixture)
        self.calls = Counter()
        self.payload = {'herbs': ['Alpha', 'Beta'], 'question': '比较功能与主治、注意事项，列出全部共同靶点'}

    async def branch(self, name, herb, question):
        self.calls[(herb, name)] += 1
        return await self.local('read_source', {'herb': herb, 'branch': name})

    async def target(self, name, herb, question):
        selection = json.loads(question)
        page = selection.get('offset', 0) // 20 + 1
        self.calls[page] += 1
        return await self.local('shared_targets', {'selection': selection, 'page': page})

    async def researcher(self, payload):
        return await run_report(payload, backend=self.branch)

    async def run_report(self, **kwargs):
        return await run_pair_report(self.payload, researcher=self.researcher,
                                     target_backend=kwargs.pop('target_backend', self.target), **kwargs)

    async def test_real_workers_read_both_pages_and_verify_originals(self):
        async def researcher(payload):
            return await run_report(payload, backend=local_branch)
        result = await run_pair_report(self.payload, researcher=researcher)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['targets']['returned_count'], 23)
        self.assertEqual(result['targets']['pages_read'], 2)
        self.assertEqual(verify_raw_witnesses(self.fixture.root, result), 48)

    async def test_first_page_request_stays_one_query(self):
        self.payload['question'] = '比较功能与主治、注意事项和共同靶点首页'
        result = await self.run_report()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['targets']['items']), 20)
        self.assertEqual(self.calls[1], 1)
        self.assertEqual(self.calls[2], 0)

    async def test_failed_second_page_preserves_first_page(self):
        async def target(name, herb, question):
            if json.loads(question).get('offset') == 20:
                raise asyncio.TimeoutError()
            return await self.target(name, herb, question)
        result = await self.run_report(target_backend=target)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['targets']['status'], 'timeout')
        self.assertEqual(len(result['targets']['items']), 20)
        self.assertEqual(result['targets']['page_calls'], 2)

    async def test_changed_revision_or_duplicate_page_is_not_merged(self):
        for mutation in ('revision', 'duplicate'):
            first = None
            async def target(name, herb, question):
                nonlocal first
                result = await self.target(name, herb, question)
                if not json.loads(question).get('offset'):
                    first = result
                elif mutation == 'revision':
                    result['catalog_revision'] = 'changed'
                else:
                    result['items'][0] = first['items'][0]
                return result
            result = await self.run_report(target_backend=target)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(len(result['targets']['items']), 20)

    async def test_page_budget_does_not_claim_complete(self):
        with patch('tcm.pair_research_report.MAX_TARGET_PAGES', 1):
            result = await self.run_report()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['targets']['status'], 'page_budget_exhausted')
        self.assertEqual(self.calls[2], 0)

    async def test_missing_requested_field_blocks_complete(self):
        self.local = backend_for(self.fixture, 'missing_field')
        result = await self.run_report()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['completion']['missing_fields'], [{'herb': 'Beta', 'field': '注意'}])

    async def test_valid_empty_intersection_is_complete(self):
        async def target(name, herb, question):
            result = await self.target(name, herb, question)
            result.update(total=0, items=[], returned_count=0, next_offset=None)
            return result
        result = await self.run_report(target_backend=target)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['targets']['items'], [])
        self.assertEqual(self.calls[2], 0)

    async def test_resume_keeps_saved_page_and_sources(self):
        saver = MemorySaver()
        ready = asyncio.Event()
        async def target(name, herb, question):
            page = json.loads(question).get('offset', 0) // 20 + 1
            if page == 2 and not ready.is_set():
                ready.set()
                await asyncio.Event().wait()
            return await self.target(name, herb, question)
        async def run():
            return await run_pair_report(self.payload, checkpointer=saver, thread_id='pair',
                                         branch_backend=self.branch, target_backend=target)
        task = asyncio.create_task(run())
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        result = await run()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.calls[1], 1)
        self.assertTrue(all(self.calls[(h, b)] == 1 for h in ('Alpha', 'Beta') for b in ('text', 'structured')))
        counts = dict(self.calls)
        self.assertEqual(await run(), result)
        self.assertEqual(dict(self.calls), counts)
        self.payload['question'] = '功能与主治'
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            await run()

    async def test_next_page_waits_for_checkpoint_flush(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class DelayedSaver(MemorySaver):
            async def aput(self, config, checkpoint, metadata, new_versions):
                if checkpoint.get('channel_values', {}).get('targets', {}).get('pages_read') == 1:
                    entered.set()
                    await release.wait()
                return await super().aput(config, checkpoint, metadata, new_versions)
        task = asyncio.create_task(run_pair_report(self.payload, checkpointer=DelayedSaver(),
            thread_id='flush-boundary', branch_backend=self.branch, target_backend=self.target))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(.05)
            self.assertEqual(self.calls[2], 0, 'Next page dispatched before prior checkpoint was durable')
        finally:
            release.set()
        self.assertEqual((await task)['status'], 'complete')

    def test_all_scope_is_bound_to_target_clause(self):
        self.assertTrue(requires_all_targets('列出所有共同靶点'))
        self.assertTrue(requires_all_targets('共同靶点完整列表'))
        self.assertFalse(requires_all_targets('比较全部字段，列出共同靶点首页'))
        self.assertFalse(requires_all_targets('不要全部共同靶点，只看首页'))


if __name__ == '__main__':
    unittest.main()
