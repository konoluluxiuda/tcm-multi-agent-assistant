"""Application adapter; MCP SDK lives in an isolated, explicitly configured runtime."""
import asyncio
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def child_environment():
    from tcm.cross_source_report import CONFIG
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','SYSTEMROOT','TEMP','TMP',*CONFIG)}
    env.update(PYTHONPATH=str(ROOT),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    # The remote runtime is not itself a second report orchestrator.
    env.pop('RESEARCH_REPORT_TRANSPORT',None)
    return env


async def call(name, arguments, *, timeout=55):
    executable=os.getenv('RESEARCH_MCP_PYTHON')
    if not executable:raise RuntimeError('MCP runtime is not configured')
    proc=await asyncio.create_subprocess_exec(executable,'-m','scripts.mcp_bridge',
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
        cwd=ROOT,env=child_environment())
    try:
        raw,_=await asyncio.wait_for(proc.communicate(json.dumps({'name':name,'arguments':arguments}).encode()),timeout)
        if proc.returncode or len(raw)>900000:raise ValueError('MCP bridge failed')
        data=json.loads(raw)
        if data.get('tool')!=name or data.get('status') not in ('ok','error'):raise ValueError('Unbound MCP result')
        return data
    finally:
        if proc.returncode is None:
            # Bridge handles TERM by cancelling the SDK session and reaping its server tree.
            proc.terminate()
            try:await asyncio.wait_for(proc.wait(),8)
            except asyncio.TimeoutError:proc.kill();await proc.wait()


async def report_branch(branch,herb,question):
    from tcm.mcp_contract import BRANCH_TO_TOOL,invoke
    name=BRANCH_TO_TOOL[branch]
    args={'herb':herb,**({'question':question} if branch=='text' else {})}
    transport=os.getenv('RESEARCH_REPORT_TRANSPORT','direct')
    if transport not in ('direct','mcp'):raise ValueError('Unknown report transport')
    result=await call(name,args) if transport=='mcp' else await invoke(name,args)
    if result['status']=='error':
        return {'status':result['error'],'tool_transport':transport}
    return {**result['data'],'tool_transport':transport}
