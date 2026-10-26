import asyncio
import os
import unittest
from unittest.mock import AsyncMock,patch
from tcm.mcp_contract import invoke,specifications
from tcm.mcp_client import report_branch,child_environment,call


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_schemas_and_validation_before_execution(self):
        self.assertEqual(len(specifications()),2)
        backend=AsyncMock()
        for name,args,error in [('bad',{},'unknown_tool'),('lookup_pharmacopoeia',{'herb':'a'},'invalid_arguments'),
                                 ('lookup_etcm_profile',{'herb':' '},'invalid_arguments'),('lookup_etcm_profile',{'herb':'a','path':'/tmp'},'invalid_arguments'),
                                 ('lookup_etcm_profile',{'herb':123},'invalid_arguments')]:
            self.assertEqual((await invoke(name,args,backend=backend))['error'],error)
        backend.assert_not_called()

    async def test_source_result_preserved(self):
        data={'status':'candidates','hits':[{'text':'原文','evidence':{'page':12,'hash':'fixed'}}]}
        backend=AsyncMock(return_value=data)
        result=await invoke('lookup_pharmacopoeia',{'herb':' 当归 ','question':' 贮藏 '},backend=backend)
        self.assertEqual(result['data'],data)
        backend.assert_awaited_once_with('text','当归','贮藏')

    async def test_errors_and_cancellation(self):
        for error,code in [(RuntimeError('/secret/path'),'unavailable'),(asyncio.TimeoutError(),'timeout')]:
            result=await invoke('lookup_etcm_profile',{'herb':'a'},backend=AsyncMock(side_effect=error))
            self.assertEqual(result['error'],code);self.assertNotIn('secret',str(result))
        with self.assertRaises(asyncio.CancelledError):
            await invoke('lookup_etcm_profile',{'herb':'a'},backend=AsyncMock(side_effect=asyncio.CancelledError()))
        result=await invoke('lookup_etcm_profile',{'herb':'a'},backend=AsyncMock(return_value={}))
        self.assertEqual(result['error'],'invalid_result')

    async def test_transport_selects_one_path(self):
        result={'tool':'lookup_etcm_profile','status':'ok','data':{'status':'ok','rows':[]}}
        with patch('tcm.mcp_client.call',new_callable=AsyncMock,return_value=result) as remote,patch('tcm.mcp_contract.invoke',new_callable=AsyncMock,return_value=result) as direct:
            with patch.dict(os.environ,{'RESEARCH_REPORT_TRANSPORT':'mcp'}):
                self.assertEqual((await report_branch('structured','a',''))['tool_transport'],'mcp')
            remote.assert_awaited_once();direct.assert_not_called()
            with patch.dict(os.environ,{'RESEARCH_REPORT_TRANSPORT':'direct'}):await report_branch('structured','a','')
            self.assertEqual(remote.await_count,1);direct.assert_awaited_once()

    async def test_failed_mcp_does_not_retry_locally(self):
        with patch.dict(os.environ,{'RESEARCH_REPORT_TRANSPORT':'mcp'}),patch('tcm.mcp_client.call',new_callable=AsyncMock,return_value={'tool':'lookup_etcm_profile','status':'error','error':'timeout'}) as remote,patch('tcm.mcp_contract.invoke',new_callable=AsyncMock) as direct:
            self.assertEqual((await report_branch('structured','a',''))['status'],'timeout')
            remote.assert_awaited_once();direct.assert_not_called()

    async def test_environment_and_disabled_runtime(self):
        with patch.dict(os.environ,{'CHAT_API_KEY':'private','RESEARCH_CHECKPOINT_DSN':'private','CHAT_MEMORY_DB':'private','TCM_DB':'local.sqlite','RESEARCH_MCP_PYTHON':''}):
            env=child_environment()
            self.assertEqual(env['TCM_DB'],'local.sqlite')
            for key in ['CHAT_API_KEY','RESEARCH_CHECKPOINT_DSN','CHAT_MEMORY_DB']:self.assertNotIn(key,env)
            with self.assertRaises(RuntimeError):await call('lookup_etcm_profile',{'herb':'a'})

    async def test_cancel_reaps_bridge(self):
        started=asyncio.Event()
        class Process:
            returncode=None
            async def communicate(self,data):started.set();await asyncio.Event().wait()
            def terminate(self):self.returncode=-15
            async def wait(self):return self.returncode
        proc=Process()
        with patch.dict(os.environ,{'RESEARCH_MCP_PYTHON':'/local/python'}),patch('tcm.mcp_client.asyncio.create_subprocess_exec',new_callable=AsyncMock,return_value=proc):
            task=asyncio.create_task(call('lookup_etcm_profile',{'herb':'a'}));await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(proc.returncode,-15)

    async def test_graph_keeps_available_branch_on_mcp_failure(self):
        from tcm.cross_source_report import run_report
        async def remote(name,args):
            if name=='lookup_pharmacopoeia':return {'tool':name,'status':'error','error':'timeout'}
            return {'tool':name,'status':'ok','data':{'status':'ok','rows':[{'label':'药用部位','value':'synthetic','source':{'id':'fixture'}}]}}
        with patch.dict(os.environ,{'RESEARCH_REPORT_TRANSPORT':'mcp'}),patch('tcm.mcp_client.call',side_effect=remote):
            result=await run_report({'herb':'示例叶','question':'功效'})
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['text']['status'],'timeout')
        self.assertEqual(result['structured']['rows'][0]['source'],{'id':'fixture'})
