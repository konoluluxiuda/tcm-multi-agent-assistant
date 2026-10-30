"""Fixed publication/recovery protocol experiment, not a model planning loop."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from tcm.research_run_durable import RunJournal, RunConflict
from experiments.architecture_candidates.evidence_store import EvidenceStore


async def execute(*, path, evidence_path, scope, backend, resume=False,
                  hook=None, field='target_ids', offset=7000, limit=1):
    store=EvidenceStore(evidence_path)
    code={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
          (Path(__file__),Path(__file__).with_name('evidence_store.py'))}
    contract=json.dumps({'scope':asdict(scope),'evidence_path':str(Path(evidence_path).resolve()),
                        'read':{'field':field,'offset':offset,'limit':limit},'code':code},sort_keys=True)
    binding=hashlib.sha256(contract.encode()).hexdigest()
    journal=RunJournal(path,scope.owner,scope.run_id,contract,resume=resume)
    initial={'messages':[],'status':'running','reason':'','tool_calls':0,'reference':None,'result':None}
    async def notify(boundary):
        if hook:await hook(boundary)
    with journal.locked():
        state,node,_=journal.load(initial,binding,120)
        if state['reference']:
            # Verify a published reference even when returning a cached terminal run.
            try:
                eid,digest,_=store.load(scope,state['reference']['evidence_handle'])
                if eid!=state['reference']['source_evidence_id'] or digest!=state['reference']['content_sha256']:
                    raise ValueError('Reference mismatch')
            except (KeyError,ValueError) as exc:
                state.update(status='partial',reason='published_evidence_unavailable',result=None)
                journal.save(state,'terminal');return state
        if state['status']!='running':return state
        if state['reference'] is None:
            state['tool_calls']+=1
            journal.save(state,'pending',operation='tool',next_node='fetch')
            # Keep pending on unknown cancellation/crash, including after store commit.
            payload=await backend()
            ref=store.put(scope,'obs-1',payload)
            await notify('evidence_saved')
            state['reference']=ref
            journal.save(state,'ready',next_node='read')
            await notify('reference_saved')
        state['result']=store.read(scope,state['reference']['evidence_handle'],field=field,offset=offset,limit=limit)
        state['status']='complete' if state['result']['status'] in ('ok','empty') else 'partial'
        state['reason']='evidence_read_complete' if state['status']=='complete' else 'evidence_read_failed'
        journal.save(state,'terminal')
        return state
