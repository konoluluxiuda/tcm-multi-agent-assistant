import unittest
import httpx
from tcm.research_run_sdk import create_sdk
from tcm.research_run_protocol import SyntheticProtocol
from tcm.research_run_demo import synthetic_tool
from tcm.research_run import run,Limits


class SdkTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, failure=None, limits=None):
        protocol=SyntheticProtocol(failure)
        async with httpx.AsyncClient(transport=httpx.MockTransport(protocol)) as client:
            sdk=create_sdk(model='synthetic-sdk',api_key='fixture-key',base_url='https://synthetic.invalid/v1',http_client=client)
            result=await run('Synthetic fixture only',sdk,synthetic_tool,limits=limits)
        return result,protocol.requests

    async def test_actual_sdk_roundtrip_and_observation_branching(self):
        result,requests=await self.execute()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(len(requests),4)
        self.assertEqual([e['status'] for e in result['events']],['empty','ok','ok','complete'])
        self.assertEqual((result['input_tokens'],result['output_tokens'],result['usage_reported_calls']),(400,80,4))
        for body in requests:
            self.assertEqual(body['tool_choice'],'auto')
            self.assertFalse(body['parallel_tool_calls'])
            self.assertEqual(body['max_completion_tokens'],650)
            self.assertEqual({t['function']['name'] for t in body['tools']},{'search_disease_sources','disease_gene_profile','read_run_evidence','finish_research'})
        observed=[m for m in requests[1]['messages'] if m['role']=='tool']
        self.assertEqual(observed[0]['tool_call_id'],'sdk-1')
        self.assertIn('"status": "empty"',observed[0]['content'])

    async def test_provider_failure_has_no_implicit_retry(self):
        result,requests=await self.execute('http')
        self.assertEqual((result['reason'],result['tool_calls'],len(requests)),('model_error',0,1))

    async def test_invalid_native_arguments_never_execute(self):
        result,requests=await self.execute('malformed')
        self.assertEqual((result['reason'],result['tool_calls']),('invalid_model_action',0))

    async def test_decision_budget_also_limits_http_requests(self):
        result,requests=await self.execute(limits=Limits(model_calls=2))
        self.assertEqual(result['reason'],'model_budget')
        self.assertEqual(len(requests),2)

    async def test_output_bound_is_required(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500))) as client:
            for count in (0,2001,True):
                with self.assertRaises(ValueError):
                    create_sdk(model='synthetic',api_key='fixture',base_url='https://synthetic.invalid',http_client=client,max_output_tokens=count)
