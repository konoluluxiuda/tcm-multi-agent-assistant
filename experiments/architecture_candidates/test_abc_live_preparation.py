"""Actual provider SDK serialization with local HTTP substitutes; never live requests."""
import json
import unittest
import httpx
from experiments.architecture_candidates.pair_protocol import drivers
from scripts.eval_architecture_abc_live_synthetic import BudgetTransport, evaluate


class LiveProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_native_adapters_complete_via_mock_http(self):
        a, _ = drivers('A_protocol')
        base = list(a.actions)
        actions = base + base
        c, children = drivers('C_protocol')
        for action in c.actions:
            actions.append(action)
            if action[0] == 'delegate':
                actions.extend(children[action[1]['herb']].actions)
        queue = iter(actions)
        seen = []
        def handler(request):
            body = json.loads(request.content)
            self.assertIs(body['parallel_tool_calls'], False)
            seen.append(body)
            name, args = next(queue)
            exposed = [tool['function']['name'] for tool in body['tools']]
            self.assertIn(name, exposed)
            self.assertNotIn('read_file', exposed)
            return httpx.Response(200, json={'id':'mock','object':'chat.completion','created':0,
                'model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls',
                'message':{'role':'assistant','content':None,'tool_calls':[{'id':str(len(seen)),
                'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}],
                'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
        report = await evaluate('mock-only-key', transport=httpx.MockTransport(handler))
        self.assertEqual(report['passed'], 3)
        self.assertEqual(report['http_requests'], 25)
        self.assertEqual(report['live_model_calls'], 0)
        self.assertEqual([r['http_requests'] for r in report['results']], [7,7,11])
        self.assertTrue(all(r['cost'] is None for r in report['results']))
        self.assertLessEqual(max(report['request_bytes']), 98304)

    async def test_36_request_and_12_candidate_budget(self):
        transport = BudgetTransport(httpx.MockTransport(lambda r:httpx.Response(200)))
        body = {'model':'deepseek-flash','max_completion_tokens':650,'parallel_tool_calls':False}
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(ValueError):
                await client.post('https://example.com/chat/completions',json=body)
            for _ in range(3):
                transport.candidate_calls = 0
                for _ in range(12):
                    await client.post('https://api.deepseek.com/chat/completions',json=body)
                with self.assertRaises(ValueError):
                    await client.post('https://api.deepseek.com/chat/completions',json=body)
            transport.candidate_calls = 0
            with self.assertRaises(ValueError):
                await client.post('https://api.deepseek.com/chat/completions',json=body)
        self.assertEqual(transport.calls, 36)

    async def test_request_must_explicitly_disable_parallel_tool_calls(self):
        transport = BudgetTransport(httpx.MockTransport(lambda r:httpx.Response(200)))
        async with httpx.AsyncClient(transport=transport) as client:
            for value in (None, True):
                body = {'model':'deepseek-flash','max_completion_tokens':650}
                if value is not None:
                    body['parallel_tool_calls'] = value
                with self.assertRaises(ValueError):
                    await client.post('https://api.deepseek.com/chat/completions',json=body)
        self.assertEqual(transport.calls, 0)

    async def test_http_failure_stops_without_retry(self):
        report = await evaluate('mock-only-key', transport=httpx.MockTransport(lambda r:httpx.Response(500)))
        self.assertEqual(report['http_requests'], 1)
        self.assertEqual(len(report['results']), 1)
        self.assertEqual(report['passed'], 0)


if __name__ == '__main__':
    unittest.main()
