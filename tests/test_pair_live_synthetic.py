import json
import unittest
import httpx

from experiments.architecture_candidates.pair_protocol import drivers
from scripts.eval_pair_live_synthetic import BudgetTransport, evaluate, preview


class LivePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_with_mock_http_completes_both_candidates(self):
        a, _ = drivers('A_protocol')
        c, children = drivers('C_protocol')
        actions = list(a.actions)
        for action in c.actions:
            actions.append(action)
            if action[0] == 'delegate':
                actions.extend(children[action[1]['herb']].actions)
        pending = iter(actions)
        seen = []
        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            self.assertEqual(body['messages'][0]['role'], 'system')
            name, args = next(pending)
            self.assertIn(name, [t['function']['name'] for t in body['tools']])
            return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion',
                'created': 0, 'model': 'deepseek-flash', 'choices': [{'index': 0,
                'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                    'tool_calls': [{'id': str(len(seen)), 'type': 'function',
                                    'function': {'name': name, 'arguments': json.dumps(args)}}]}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
        report = await evaluate('mock-key', transport=httpx.MockTransport(handler))
        self.assertEqual(report['passed'], 2)
        self.assertEqual(report['http_requests'], 18)
        self.assertEqual(report['live_model_calls'], 0)
        self.assertEqual([r['http_requests'] for r in report['results']], [7, 11])
        self.assertLessEqual(max(report['request_bytes']), 98304)
        self.assertTrue(all(r['cost'] is None for r in report['results']))

    async def test_provider_failure_is_not_retried(self):
        report = await evaluate('mock-key', transport=httpx.MockTransport(lambda r:httpx.Response(500)))
        self.assertEqual(report['http_requests'], 1)
        self.assertEqual(len(report['results']), 1)
        self.assertEqual(report['results'][0]['status'], 'provider_error')

    async def test_destination_and_candidate_budget(self):
        transport = BudgetTransport(httpx.MockTransport(lambda r:httpx.Response(200)))
        body = {'model': 'deepseek-flash', 'max_tokens': 650}
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(ValueError):
                await client.post('https://example.com/chat/completions', json=body)
            for _ in range(12):
                await client.post('https://api.deepseek.com/chat/completions', json=body)
            with self.assertRaises(ValueError):
                await client.post('https://api.deepseek.com/chat/completions', json=body)
            transport.candidate_calls = 0
            for _ in range(12):
                await client.post('https://api.deepseek.com/chat/completions', json=body)
            transport.candidate_calls = 0
            with self.assertRaises(ValueError):
                await client.post('https://api.deepseek.com/chat/completions', json=body)
        self.assertEqual(transport.calls, 24)

    async def test_oversize_or_changed_model_blocked_before_dispatch(self):
        transport = BudgetTransport(httpx.MockTransport(lambda r:httpx.Response(200)))
        async with httpx.AsyncClient(transport=transport) as client:
            for body in ({'model': 'other', 'max_tokens': 650},
                         {'model': 'deepseek-flash', 'max_tokens': 650, 'data': 'x'*98304}):
                with self.assertRaises(ValueError):
                    await client.post('https://api.deepseek.com/chat/completions', json=body)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(preview()['model_calls_executed'], 0)


if __name__ == '__main__':
    unittest.main()
