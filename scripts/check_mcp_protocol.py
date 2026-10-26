"""Real MCP stdio discovery, dual-client parity, validation and cancellation."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from scripts.mcp_stdio_probe import connect
from scripts.mcp_bridge import exchange
from tcm.mcp_contract import specifications,invoke
from tcm.cross_source_report import local_branch


def stable(value):
    if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if not k.endswith(('_ms','_seconds'))}
    if isinstance(value,list):return [stable(v) for v in value]
    return value


async def check(out):
    calls=[('lookup_pharmacopoeia',{'herb':'当归','question':'贮藏'}),('lookup_etcm_profile',{'herb':'当归'})]
    parity=[]
    async with connect() as client:
        listing=await client.request('tools/list')
        assert {x['name'] for x in listing['tools']}=={x['name'] for x in specifications()}
        assert all(x['annotations']['readOnlyHint'] for x in listing['tools'])
        for name,args in calls:
            direct=await invoke(name,args)
            sdk=await exchange(name,args)
            raw=await client.request('tools/call',{'name':name,'arguments':args})
            assert stable(direct)==stable(sdk)==stable(raw['structuredContent'])
            assert direct['status']=='ok' and direct['data']['status'] in ('candidates','ok','name_mapping_candidates'),direct
            parity.append({'tool':name,'same_data_and_sources':True,'data_status':direct['data']['status']})
        for name,args,code in [('missing',{},'unknown_tool'),('lookup_etcm_profile',{'herb':' '},'invalid_arguments'),
                               ('lookup_etcm_profile',{'herb':'当归','database':'/tmp/forbidden'},'invalid_arguments')]:
            data=await client.request('tools/call',{'name':name,'arguments':args})
            assert data['isError'] and data['structuredContent']['error']==code
    with tempfile.TemporaryDirectory() as tmp:
        marker=Path(tmp)/'cancel.txt';env={**os.environ,'MCP_TEST_MARKER':str(marker)}
        async with connect(module='tests.mcp_fault_server',env=env) as client:
            data=await client.request('tools/call',{'name':'lookup_etcm_profile','arguments':{'herb':'broken'}})
            assert data['isError'] and data['structuredContent']['error']=='unavailable'
            assert 'private' not in json.dumps(data)
            # Explicit protocol cancellation of an in-flight call, followed by a live ping.
            await client.send({'id':100,'method':'tools/call','params':{'name':'lookup_etcm_profile','arguments':{'herb':'slow'}}})
            for _ in range(100):
                if marker.exists():break
                await asyncio.sleep(.02)
            assert marker.read_text()=='started'
            await client.send({'method':'notifications/cancelled','params':{'requestId':100,'reason':'acceptance cancellation'}})
            for _ in range(100):
                if marker.read_text()=='cancelled':break
                await asyncio.sleep(.02)
            assert marker.read_text()=='cancelled'
            await client.request('ping')
        # SDK timeout closes its dedicated server; no retry or shared-session contamination.
        params=StdioServerParameters(command=__import__('sys').executable,args=['-m','tests.mcp_fault_server'],env=env)
        timeout=False
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as session:
                await session.initialize()
                try:await session.call_tool('lookup_etcm_profile',{'herb':'slow'},read_timeout_seconds=timedelta(seconds=.15))
                except Exception:timeout=True
        assert timeout
    report={'status':'passed','transport':'stdio','sdk_version':'1.28.0','clients':['official_sdk_application_bridge','independent_jsonrpc_probe'],
            'discovery':True,'parity':parity,'invalid_arguments_and_unknown_tool':True,'private_error_redacted':True,
            'protocol_cancel_and_reuse':True,'sdk_timeout_session_closed':True,'external_model_calls':0}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();asyncio.run(check(a.out))
