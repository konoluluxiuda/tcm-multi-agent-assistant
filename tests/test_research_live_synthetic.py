import unittest
import httpx
from scripts.eval_research_live_synthetic import BudgetTransport,evaluate,preview
from tcm.research_run_protocol import SyntheticProtocol


class LivePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_tasks_with_actual_sdk_and_mock_http(self):
        report=await evaluate('fixture-key',transport=httpx.MockTransport(SyntheticProtocol()))
        self.assertEqual((report['passed'],report['run_tasks'],report['http_requests']),(2,2,8))

    async def test_service_failure_stops_before_second_task(self):
        report=await evaluate('fixture-key',transport=httpx.MockTransport(SyntheticProtocol('http')))
        self.assertEqual((report['passed'],report['run_tasks'],report['http_requests']),(0,1,1))

    async def test_transport_destination_and_total_budget(self):
        transport=BudgetTransport(httpx.MockTransport(lambda r:httpx.Response(200)))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(ValueError):await client.post('https://other.example/chat/completions')
            self.assertEqual(transport.calls,0)
            for i in range(8):await client.post('https://api.deepseek.com/chat/completions')
            with self.assertRaises(ValueError):await client.post('https://api.deepseek.com/chat/completions')
            self.assertEqual(transport.calls,8)
        self.assertEqual(preview()['max_http_requests'],8)
