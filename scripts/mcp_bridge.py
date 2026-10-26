"""Official SDK stdio client used by the application; no provider calls/retries."""
import asyncio
from datetime import timedelta
import json
import os
import signal
import sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from tcm.mcp_contract import specifications


async def exchange(name,arguments):
    parameters=StdioServerParameters(command=sys.executable,args=['-m','tcm.mcp_server'],env=dict(os.environ))
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=48)) as session:
            await session.initialize()
            listing=await session.list_tools()
            expected={s['name']:s['inputSchema'] for s in specifications()}
            if {t.name:t.inputSchema for t in listing.tools}!=expected:raise ValueError('MCP schema mismatch')
            result=await session.call_tool(name,arguments)
            data=result.structuredContent
            if not isinstance(data,dict) or result.isError!=(data.get('status')=='error'):raise ValueError('Invalid MCP envelope')
            return data


async def main():
    loop=asyncio.get_running_loop();task=asyncio.current_task()
    if sys.platform!='win32':loop.add_signal_handler(signal.SIGTERM,task.cancel)
    raw=sys.stdin.buffer.read(8193)
    if len(raw)>8192:raise ValueError('Input too large')
    request=json.loads(raw)
    print(json.dumps(await exchange(request['name'],request['arguments']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:asyncio.run(main())
    except asyncio.CancelledError:pass
