import json
from pathlib import Path
import unittest
from langchain_core.messages import AIMessage
from tcm.analysis_tools import SPECS
from tcm.research_run import run
from tcm.research_run_demo import SID
from tcm.research_synthetic_v2 import NAME,synthetic_tool
from tcm.task_tools import ToolResult


class SyntheticV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_unique_name_and_id_match_and_unknown_is_not_invented(self):
        by_name=await synthetic_tool('disease_gene_profile',{'name':NAME})
        by_id=await synthetic_tool('disease_gene_profile',{'name':SID})
        self.assertEqual(by_name,by_id)
        self.assertTrue(by_name['data']['rows'][0]['raw_row']['synthetic'])
        ToolResult[SPECS['disease_gene_profile'][1]].model_validate(by_name)
        with self.assertRaises(ValueError):await synthetic_tool('disease_gene_profile',{'name':'other'})
        empty=await synthetic_tool('search_disease_sources',{'name':'other'})
        self.assertEqual((empty['status'],empty['data']['items']),('empty',[]))

    async def test_complete_and_missing_detail_are_distinct(self):
        complete=await synthetic_tool('disease_gene_profile',{'name':NAME})
        missing=await synthetic_tool('disease_gene_profile',{'name':NAME},missing_rows=True)
        self.assertEqual(complete['data']['target_ids'],missing['data']['target_ids'])
        self.assertEqual(missing['data']['rows'],[])
        self.assertEqual(missing['data']['profile']['detail_status'],'missing')
        class Driver:
            def bind_tools(self,*a,**kw):return self
            async def ainvoke(self,messages):
                observations=[m for m in messages if m.type=='tool']
                if not observations:name,args='disease_gene_profile',{'name':NAME}
                else:
                    data=json.loads(observations[-1].content)['data']
                    lack=data['profile']['detail_status']=='missing'
                    name,args='finish_research',{'status':'partial' if lack else 'complete','evidence_ids':['obs-1'],
                                                'unresolved':['Original detail rows missing'] if lack else []}
                return AIMessage(content='',tool_calls=[{'id':str(len(observations)),'name':name,'args':args}])
        for lacks,status in ((False,'complete'),(True,'partial')):
            async def backend(name,args):return await synthetic_tool(name,args,missing_rows=lacks)
            result=await run('Read the target profile and original detail rows',Driver(),backend,required=('disease_gene_profile',))
            self.assertEqual(result['status'],status)

    async def test_recorded_live_tool_arguments_replay_without_contract_errors(self):
        root=Path(__file__).resolve().parents[1]
        report=json.loads((root/'audits/research_live_synthetic_v1/live_result.json').read_text())
        count=0
        for task in report['results']:
            for event in task['run']['events']:
                if event['tool']=='finish_research':continue
                result=await synthetic_tool(event['tool'],event['arguments'])
                ToolResult[SPECS[event['tool']][1]].model_validate(result)
                expected='empty' if event['arguments'].get('name')=='missing' else 'ok'
                self.assertEqual(result['status'],expected)
                count+=1
        self.assertEqual(count,6)
        self.assertEqual(report['passed'],0)  # Original evaluation is not rewritten.
