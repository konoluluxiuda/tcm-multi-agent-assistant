"""Second client: small JSON-RPC stdio probe, independent of the MCP client SDK."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import signal
import sys


class StdioProbe:
    def __init__(self,process):self.process=process;self.sequence=0
    async def send(self,message):
        self.process.stdin.write((json.dumps({'jsonrpc':'2.0',**message})+'\n').encode())
        await self.process.stdin.drain()
    async def request(self,method,params=None,timeout=55):
        self.sequence+=1;identifier=self.sequence
        await self.send({'id':identifier,'method':method,**({'params':params} if params is not None else {})})
        async def receive():
            while True:
                line=await self.process.stdout.readline()
                if not line:raise RuntimeError('MCP server disconnected')
                data=json.loads(line)
                if data.get('id')==identifier:
                    if 'error' in data:raise RuntimeError('MCP protocol error')
                    return data['result']
        return await asyncio.wait_for(receive(),timeout)


@asynccontextmanager
async def connect(*,module='tcm.mcp_server',env=None):
    process=await asyncio.create_subprocess_exec(sys.executable,'-m',module,
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
        env=env,limit=1100000,start_new_session=sys.platform!='win32')
    probe=StdioProbe(process)
    try:
        await probe.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'tcm-raw-stdio-probe','version':'1.0'}})
        await probe.send({'method':'notifications/initialized'})
        yield probe
    finally:
        if process.returncode is None:
            process.stdin.close()
            try:await asyncio.wait_for(process.wait(),3)
            except asyncio.TimeoutError:
                if sys.platform!='win32':os.killpg(process.pid,signal.SIGKILL)
                else:process.kill()
                await process.wait()
