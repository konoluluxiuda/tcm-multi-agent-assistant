import json
import unittest
from langchain_core.messages import SystemMessage,HumanMessage,AIMessage,ToolMessage
from tcm.research_context import ContextPolicy,project,size
from tcm.research_run import run
from tcm.research_run_demo import OfflineModel,synthetic_tool


class ContextTests(unittest.IsolatedAsyncioTestCase):
    def messages(self):
        messages=[SystemMessage(content='Keep original sources and requirements'),HumanMessage(content='Compare A and B; preserve both original record locations')]
        for i in range(4):
            messages.append(AIMessage(content='',tool_calls=[{'id':str(i),'name':'disease_gene_profile','args':{'name':'source-'+str(i)}}]))
            messages.append(ToolMessage(tool_call_id=str(i),content=json.dumps({'evidence_id':'obs-'+str(i),'tool':'disease_gene_profile','status':'ok','data':{'record_id':'source-'+str(i),'target_ids':['P12345'],'rows':[{'raw':'中'*5000}],'profile':{'target_count':1}}},ensure_ascii=False)))
        return messages

    def test_old_results_shrink_without_losing_pairs_requirements_or_ids(self):
        messages=self.messages();original=[m.model_dump() for m in messages]
        projected,check=project(messages,[],ContextPolicy(max_bytes=40000,reserve_bytes=1000,keep_recent_tools=2))
        self.assertTrue(check['fits']);self.assertLess(check['input_bytes_after'],check['input_bytes_before'])
        self.assertEqual(projected[:2],messages[:2]);self.assertEqual(projected[-4:],messages[-4:])
        self.assertEqual(original,[m.model_dump() for m in messages])
        for i in range(2,len(projected),2):
            self.assertEqual(projected[i].tool_calls[0]['id'],projected[i+1].tool_call_id)
        data=json.loads(projected[3].content)
        self.assertEqual(data['evidence_id'],'obs-0');self.assertEqual(data['data']['record_id'],'source-0')
        self.assertEqual(data['data']['target_id_count'],1);self.assertTrue(data['compacted'])

    async def test_overflow_stops_before_sdk_request(self):
        class Never(OfflineModel):
            async def ainvoke(self,m):raise AssertionError('No request allowed')
        result=await run('q',Never(),synthetic_tool,context_policy=ContextPolicy(max_bytes=1000,reserve_bytes=0))
        self.assertEqual((result['reason'],result['model_calls']),('context_budget',0))

    def test_schema_and_recent_huge_result_are_not_silently_dropped(self):
        messages=self.messages()[-2:]
        projected,check=project(messages,[],ContextPolicy(max_bytes=1000,reserve_bytes=0))
        self.assertFalse(check['fits']);self.assertEqual(projected,messages)
        self.assertGreater(size([], [{'description':'x'*5000}]),5000)

    async def test_short_run_unchanged_and_checks_recorded(self):
        result=await run('synthetic',OfflineModel(),synthetic_tool)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(len(result['context_checks']),4)
        self.assertTrue(all(c['fits'] and not c['compacted_call_ids'] for c in result['context_checks']))

    async def test_full_loop_compacts_but_preserves_complete_evidence(self):
        async def large(name,args):
            result=await synthetic_tool(name,args)
            if name=='search_disease_sources' and result['data']['items']:
                result['data']['items'][0]['source']['synthetic_padding']='x'*12000
            if name=='disease_gene_profile':
                result['data']['rows']=[{'synthetic_padding':'y'*12000}]
            return result
        result=await run('synthetic',OfflineModel(),large,
                         context_policy=ContextPolicy(max_bytes=24000,reserve_bytes=1000,keep_recent_tools=1))
        self.assertEqual(result['status'],'complete')
        self.assertTrue(any(c['compacted_call_ids'] for c in result['context_checks']))
        self.assertEqual(len(result['evidence']['obs-2']['data']['items'][0]['source']['synthetic_padding']),12000)
        self.assertEqual(result['events'][-1]['evidence_ids'],['obs-2','obs-3'])
