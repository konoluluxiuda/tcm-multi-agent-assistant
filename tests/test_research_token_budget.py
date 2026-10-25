import unittest
from langchain_core.messages import AIMessage
from tcm.research_run import run
from tcm.research_run_demo import OfflineModel,synthetic_tool
from tcm.research_token_budget import InputBudget,TokenEstimator


class FixedEstimator:
    label='test_units'
    def count(self,messages,tools):return 100


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_budget_blocks_before_third_request(self):
        result=await run('q',OfflineModel(),synthetic_tool,input_budget=InputBudget(per_call=100,cumulative=200),token_estimator=FixedEstimator())
        self.assertEqual((result['reason'],result['model_calls'],result['estimated_input_units']),('input_cumulative_budget',2,200))
        self.assertEqual(result['input_tokens'],0)

    async def test_per_call_budget_blocks_first_request(self):
        result=await run('q',OfflineModel(),synthetic_tool,input_budget=InputBudget(per_call=99,cumulative=1000),token_estimator=FixedEstimator())
        self.assertEqual((result['reason'],result['model_calls']),('input_call_budget',0))

    async def test_reported_usage_does_not_replace_estimated_units(self):
        class Usage(OfflineModel):
            async def ainvoke(self,messages):
                reply=await super().ainvoke(messages)
                reply.usage_metadata={'input_tokens':7,'output_tokens':3,'total_tokens':10}
                return reply
        result=await run('q',Usage(),synthetic_tool,token_estimator=FixedEstimator())
        self.assertEqual(result['status'],'complete')
        self.assertEqual((result['estimated_input_units'],result['input_tokens'],result['output_tokens']),(400,28,12))

    def test_offline_counter_contract_and_unicode(self):
        class Encoding:
            def encode(self,text,**kwargs):
                assert kwargs=={'disallowed_special':()}
                return list(text)
        from langchain_core.messages import HumanMessage
        messages=[HumanMessage(content='中医药')]
        self.assertGreater(TokenEstimator().count(messages,[]),TokenEstimator(Encoding()).count(messages,[]))
        self.assertEqual(TokenEstimator().label,'utf8_byte_proxy')
