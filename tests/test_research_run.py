import asyncio
import unittest
from langchain_core.messages import AIMessage
from tcm.research_run import run,Limits
from tcm.research_run_demo import OfflineModel,synthetic_tool,demo

class ReplyModel:
    def __init__(self,name,args):self.name=name;self.args=args;self.i=0
    def bind_tools(self,*a,**kw):return self
    async def ainvoke(self,messages):
        self.i+=1
        return AIMessage(content='',tool_calls=[{'id':str(self.i),'name':self.name,'args':self.args,'type':'tool_call'}])

class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_driven_retry_and_two_tools(self):
        r=await run('synthetic',OfflineModel(),synthetic_tool)
        self.assertEqual((r['status'],r['model_calls'],r['tool_calls']),('complete',4,3))
        self.assertEqual([e['status'] for e in r['events']],['empty','ok','ok','complete'])
    async def test_model_budget(self):
        r=await run('q',OfflineModel(),synthetic_tool,limits=Limits(model_calls=1))
        self.assertEqual(r['reason'],'model_budget')
    async def test_tool_budget(self):
        r=await run('q',OfflineModel(),synthetic_tool,limits=Limits(tool_calls=1))
        self.assertEqual((r['reason'],r['tool_calls']),('tool_budget',1))
    async def test_unknown_tool(self):
        r=await run('q',ReplyModel('shell',{}),synthetic_tool)
        self.assertEqual((r['reason'],r['tool_calls']),('unknown_tool',0))
    async def test_invalid_arguments(self):
        r=await run('q',ReplyModel('search_disease_sources',{'page':0}),synthetic_tool)
        self.assertEqual(r['reason'],'invalid_arguments')
    async def test_repeated_action(self):
        r=await run('q',ReplyModel('search_disease_sources',{'name':'missing'}),synthetic_tool)
        self.assertEqual((r['reason'],r['tool_calls']),('duplicate_action',1))
    async def test_finish_without_scope_is_partial(self):
        r=await run('q',ReplyModel('finish_research',{'status':'complete','evidence_ids':[],'unresolved':[]}),synthetic_tool)
        self.assertEqual(r['status'],'partial')
    async def test_fabricated_evidence(self):
        r=await run('q',ReplyModel('finish_research',{'status':'complete','evidence_ids':['fake'],'unresolved':[]}),synthetic_tool)
        self.assertEqual(r['reason'],'unknown_evidence')
    async def test_cancellation_in_flight(self):
        cancel=asyncio.Event()
        class Slow(OfflineModel):
            async def ainvoke(self,m):cancel.set();await asyncio.sleep(2)
        r=await run('q',Slow(),synthetic_tool,cancel=cancel)
        self.assertEqual((r['status'],r['tool_calls']),('cancelled',0))
    async def test_model_timeout(self):
        class Slow(OfflineModel):
            async def ainvoke(self,m):await asyncio.sleep(1)
        r=await run('q',Slow(),synthetic_tool,limits=Limits(call_seconds=.01))
        self.assertEqual(r['reason'],'timeout')
    async def test_tool_timeout_observed_and_not_repeated(self):
        async def slow(n,a):await asyncio.sleep(1)
        r=await run('q',ReplyModel('search_disease_sources',{'name':'missing'}),slow,limits=Limits(call_seconds=.01))
        self.assertEqual(r['evidence']['obs-1']['error']['code'],'timeout')
        self.assertEqual(r['reason'],'duplicate_action')
    async def test_malformed_tool_result_and_api(self):
        async def bad(n,a):return {'tool':'other'}
        r=await run('q',ReplyModel('search_disease_sources',{'name':'missing'}),bad)
        self.assertEqual(r['evidence']['obs-1']['status'],'error')
        import httpx
        from tcm.research_run_demo import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/synthetic-research/run')
        self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(result['run']['status'],'complete')
        self.assertEqual(result['mode'],'synthetic_offline_model')
