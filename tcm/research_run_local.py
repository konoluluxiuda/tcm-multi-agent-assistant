"""Read-only disease tools isolated in cancellable, network-disabled child processes."""
import asyncio
import json
import os
from pathlib import Path
import sys

from langchain_core.messages import AIMessage, ToolMessage
from tcm.analysis_tools import CALL
from tcm.disease_catalog import connect, lookup
from tcm.research_run import ALLOWED, run
from tcm.research_run_scope import RunScope
from tcm.target_similarity import file_token, SnapshotChanged


class LocalDiseaseTools:
    def __init__(self,path):
        self.path=Path(path).resolve()
        con,_=connect(self.path);con.close()
        self.token=file_token(self.path)

    def validate_snapshot(self):
        if self.token!=file_token(self.path):raise SnapshotChanged('Local research snapshot changed')

    async def __call__(self,name,args):
        if name not in ALLOWED:raise ValueError('Unknown local tool')
        parsed=CALL.validate_python({'tool':name,'arguments':args})
        self.validate_snapshot()
        root=Path(__file__).resolve().parents[1]
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','SYSTEMROOT','TEMP','TMP')}
        env.update(PYTHONPATH=str(root),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        proc=await asyncio.create_subprocess_exec(sys.executable,'-m','tcm.research_run_local','--worker',
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            cwd=root,env=env)
        try:
            raw,_=await proc.communicate(json.dumps({'database':str(self.path),'call':parsed.model_dump()}).encode())
            if proc.returncode or len(raw)>48000:raise ValueError('Local tool failed or exceeded result budget')
            self.validate_snapshot()
            return json.loads(raw)
        finally:
            if proc.returncode is None:
                proc.kill();await proc.wait()


class DeclaredActionsDriver:
    """Offline replay of caller-declared actions, never labelled an LLM planner."""
    def __init__(self,scope):self.scope=scope
    def bind_tools(self,*args,**kwargs):return self
    async def ainvoke(self,messages):
        observations=[json.loads(m.content) for m in messages if isinstance(m,ToolMessage)]
        if len(observations)<len(self.scope.requirements):
            call=self.scope.requirements[len(observations)].call;name,args=call['tool'],call['arguments']
        else:
            name='finish_research';args={'status':'complete','evidence_ids':[o['evidence_id'] for o in observations],
                                        'unresolved':self.scope.unhandled_requirements}
        return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':'local-'+str(len(observations)),'type':'tool_call'}])


async def run_local_profile(path,disease, *, on_state=None):
    backend=LocalDiseaseTools(path)
    found=lookup(path,disease)
    if found['resolution_status']!='source_record':
        return {'mode':'local_offline_driver','status':'needs_input','reason':found['resolution_status'],
                'options':found['options'],'options_truncated':found['options_truncated']}
    scope=RunScope.model_validate({'requirements':[{'id':'profile-first-page-and-target-set',
        'call':{'tool':'disease_gene_profile','arguments':{'name':found['record_id'],'page':1}}}]})
    result=await run('Read the selected disease target set and first page of original rows.',
                     DeclaredActionsDriver(scope),backend,scope=scope,on_state=on_state)
    backend.validate_snapshot()
    return {'mode':'local_offline_driver','status':result['status'],'resolved_source_id':found['record_id'],'run':result}


def worker():
    import socket
    def denied(*args,**kwargs):raise RuntimeError('Local research worker forbids network access')
    for name in ('connect','connect_ex','sendto'):setattr(socket.socket,name,denied)
    socket.getaddrinfo=denied
    request=json.loads(sys.stdin.buffer.read(8192))
    parsed=CALL.validate_python(request['call'])
    if parsed.tool not in ALLOWED:raise ValueError('Unknown local tool')
    os.environ['ETCM_DISEASE_DB']=request['database']
    from tcm.analysis_tools import invoke
    result=invoke(parsed.model_dump()).model_dump()
    raw=json.dumps(result,ensure_ascii=False)
    if len(raw.encode())>48000:raise ValueError('Result exceeds worker budget')
    print(raw)


if __name__=='__main__':
    if sys.argv[1:]!=['--worker']:raise SystemExit('Internal read-only worker only')
    worker()
