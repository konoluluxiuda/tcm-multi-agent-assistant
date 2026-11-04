"""Read-only business tools isolated in cancellable, network-disabled child processes."""
import asyncio
import json
import os
from pathlib import Path
import sys

from langchain_core.messages import AIMessage, ToolMessage
from tcm.research_business_tools import CALL, SPECS,REPORT_TOOLS
from tcm.disease_catalog import connect, lookup
from tcm.research_run import ALLOWED, run
from tcm.research_run_scope import RunScope
from tcm.target_similarity import file_token, SnapshotChanged


class LocalAnalysisTools:
    metered_business_tools=True
    def __init__(self, path, *, herbs=None, enrichment=None,pharmacopoeia=None):
        self.path=Path(path).resolve() if path else None
        self.databases={}
        if self.path:
            con,_=connect(self.path);con.close()
            self.databases['ETCM_DISEASE_DB']=self.path
        if bool(herbs)!=bool(enrichment):raise ValueError('Both herb databases are required')
        if herbs:
            self.databases.update(TCM_DB=Path(herbs).resolve(),ETCM_ENRICHMENT_DB=Path(enrichment).resolve())
        self.pharmacopoeia=Path(pharmacopoeia).resolve() if pharmacopoeia else None
        self.pharma_files=([self.pharmacopoeia/'research.sqlite3',self.pharmacopoeia/'manifest.json'] if self.pharmacopoeia else [])
        self.pharma_tokens=[file_token(p) for p in self.pharma_files]
        if not self.databases and not self.pharmacopoeia:raise ValueError('No business database configured')
        self.dataset_version=os.getenv('TCM_DATASET_VERSION') or None
        self.tokens={key:file_token(value) for key,value in self.databases.items()}
        self.token=self.tokens.get('ETCM_DISEASE_DB')

    @property
    def contract(self):
        base={'transport':'local-analysis-worker-v3','dataset_version':self.dataset_version,
                'databases':{key:{'path':str(path),'token':self.tokens[key]}
                             for key,path in self.databases.items()}}
        if self.pharmacopoeia:base['pharmacopoeia']={'path':str(self.pharmacopoeia),'tokens':self.pharma_tokens,'retrieval':'guarded_lexical_direct_v1'}
        return base

    def validate_snapshot(self):
        if any(self.tokens[key]!=file_token(path) for key,path in self.databases.items()):
            raise SnapshotChanged('Local research snapshot changed')
        if self.pharma_tokens!=[file_token(p) for p in self.pharma_files]:raise SnapshotChanged('Pharmacopoeia changed')

    async def invoke_metered(self,name,args,charge):
        from tcm.research_report_tools import execute
        from tcm.cross_source_report import local_branch
        self.validate_snapshot()
        config={**{key:str(path) for key,path in self.databases.items()},'TCM_DATASET_VERSION':self.dataset_version or '',
            'LOCAL_DENSE_PYTHON':'','LOCAL_DENSE_MODEL':'','LOCAL_DENSE_CACHE':'','LOCAL_RERANKER_MODEL':'',
            'LOCAL_QDRANT_GENERATION':'','RESEARCH_REPORT_TRANSPORT':'direct','RESEARCH_MCP_PYTHON':''}
        if self.pharmacopoeia:config['PHARMACOPOEIA_INDEX']=str(self.pharmacopoeia)
        async def branch(kind,herb,question,*,source_id=None):
            return await local_branch(kind,herb,question,source_record_id=source_id,config=config)
        result=await execute(name,args,branch,charge,self.contract)
        self.validate_snapshot()
        return result

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
            raw,_=await proc.communicate(json.dumps({'databases':{key:str(path) for key,path in self.databases.items()},'dataset_version':self.dataset_version,'call':parsed.model_dump()}).encode())
            if proc.returncode or len(raw)>4_000_000:raise ValueError('Local tool failed or exceeded result budget')
            self.validate_snapshot()
            return json.loads(raw)
        finally:
            if proc.returncode is None:
                proc.kill();await proc.wait()


class LocalDiseaseTools(LocalAnalysisTools):
    """Compatibility name for the disease-only transport configuration."""


class DeclaredActionsDriver:
    """Execute host-declared requirements; this is not an LLM intent planner."""
    def __init__(self,scope,*,batch_size=1,evidence_store=None):
        if type(batch_size) is not int or not 1<=batch_size<=4:raise ValueError('Invalid batch size')
        self.scope=scope;self.batch_size=batch_size;self.evidence_store=evidence_store
    def bind_tools(self,*args,**kwargs):return self
    def bind_evidence(self,store):self.evidence_store=store
    async def ainvoke(self,messages):
        by_id={c['id']:c for m in messages for c in getattr(m,'tool_calls',[])}
        events=[];evidence={};seen=[]
        for m in messages:
            if not isinstance(m,ToolMessage):continue
            payload=json.loads(m.content);call=by_id[m.tool_call_id]
            if self.evidence_store:
                eid=payload['evidence_id'];payload={'evidence_id':eid,**self.evidence_store.resolve(eid,payload)}
            args=SPECS[call['name']][0].model_validate(call['args']).model_dump()
            events.append({'call_id':m.tool_call_id,'tool':call['name'],'arguments':args,'evidence_id':payload['evidence_id']})
            evidence[payload['evidence_id']]=payload
            seen.append({'tool':call['name'],'arguments':args})
        pending=[c for c in self.scope.pending(events,evidence) if c not in seen]
        if pending:
            batch=[];page_keys=set()
            for item in pending:
                key=(item['tool'],item['arguments'].get('name',tuple(item['arguments'].get('herbs',[]))))
                if item['tool'] in ('search_disease_sources','disease_gene_profile'):
                    if key in page_keys:continue
                    page_keys.add(key)
                batch.append(item)
                if len(batch)==self.batch_size:break
            return AIMessage(content='',tool_calls=[{'name':item['tool'],'args':item['arguments'],
                'id':'local-'+str(len(events)+i),'type':'tool_call'} for i,item in enumerate(batch)])
        else:
            coverage=self.scope.coverage(events,evidence)
            unresolved=[r['requirement_id'] for r in coverage['requirements'] if not r['fulfilled']]
            name='finish_research';args={'status':'complete' if coverage['complete'] else 'partial',
                'evidence_ids':list(evidence),'unresolved':(unresolved+self.scope.unhandled_requirements)[:8]}
        return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':'local-'+str(len(events)),'type':'tool_call'}])


async def run_local_actions(path, scope, *, herbs=None, enrichment=None, on_state=None,evidence_store=None,pharmacopoeia=None,journal=None):
    """Run an explicitly declared business plan locally without any model calls."""
    from tcm.research_run import Limits
    scope=RunScope.model_validate(scope)
    tools={r.call['tool'] for r in scope.requirements}
    needs_disease=bool(tools & {'search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set'})
    needs_herbs=bool(tools & {'herb_target_profile','herb_target_similarity','rank_herbs_for_gene_set','herb_attributes','herb_relation_records','pair_evidence_report'})
    needs_pharma=bool(tools & {'pharmacopoeia_lookup','pair_evidence_report'})
    if needs_disease and not path or needs_herbs and not (herbs and enrichment):raise ValueError('Required analysis databases missing')
    backend=LocalAnalysisTools(path if needs_disease else None,
        herbs=herbs if needs_herbs else None,enrichment=enrichment if needs_herbs else None,
        pharmacopoeia=pharmacopoeia if needs_pharma else None)
    if needs_pharma and not backend.pharmacopoeia:raise ValueError('Pharmacopoeia index required')
    result=await run('Execute the declared local business requirements.',DeclaredActionsDriver(scope,batch_size=4,evidence_store=evidence_store),backend,
                     scope=scope,limits=Limits(model_calls=8,tool_calls=16,call_seconds=30),on_state=on_state,evidence_store=evidence_store,journal=journal)
    backend.validate_snapshot()
    return {'mode':'local_offline_driver','status':result['status'],'run':result,'source_contract':backend.contract}


async def run_local_profile(path,disease, *, on_state=None,evidence_store=None):
    backend=LocalDiseaseTools(path)
    found=lookup(path,disease)
    if found['resolution_status']!='source_record':
        return {'mode':'local_offline_driver','status':'needs_input','reason':found['resolution_status'],
                'options':found['options'],'options_truncated':found['options_truncated']}
    scope=RunScope.model_validate({'requirements':[{'id':'profile-first-page-and-target-set',
        'call':{'tool':'disease_gene_profile','arguments':{'name':found['record_id'],'page':1}}}]})
    result=await run('Read the selected disease target set and first page of original rows.',
                     DeclaredActionsDriver(scope,evidence_store=evidence_store),backend,scope=scope,on_state=on_state,evidence_store=evidence_store)
    backend.validate_snapshot()
    return {'mode':'local_offline_driver','status':result['status'],'resolved_source_id':found['record_id'],'run':result,'source_contract':backend.contract}


def worker():
    import socket
    def denied(*args,**kwargs):raise RuntimeError('Local research worker forbids network access')
    for name in ('connect','connect_ex','sendto'):setattr(socket.socket,name,denied)
    socket.getaddrinfo=denied
    request=json.loads(sys.stdin.buffer.read(8192))
    parsed=CALL.validate_python(request['call'])
    if parsed.tool not in ALLOWED:raise ValueError('Unknown local tool')
    databases=request['databases']
    if set(databases)-{'ETCM_DISEASE_DB','TCM_DB','ETCM_ENRICHMENT_DB'}:raise ValueError('Unknown database key')
    os.environ.update(databases)
    if request.get('dataset_version'):os.environ['TCM_DATASET_VERSION']=request['dataset_version']
    from tcm.analysis_tools import invoke
    record=None;version=None
    if parsed.tool in ('herb_target_profile','herb_target_similarity'):
        from tcm.chat import execute_turn
        found,_=execute_turn({'source_record_id':parsed.arguments.name,'profile':True},None)
        if found['status']!='ok' or len(found.get('records',[]))!=1:
            raise ValueError('Select one herb source first')
        record=found['records'][0];version=found['dataset_version']
    result=invoke(parsed.model_dump(),record,version).model_dump()
    raw=json.dumps(result,ensure_ascii=False)
    if len(raw.encode())>4_000_000:raise ValueError('Result exceeds worker budget')
    print(raw)


if __name__=='__main__':
    if sys.argv[1:]!=['--worker']:raise SystemExit('Internal read-only worker only')
    worker()
