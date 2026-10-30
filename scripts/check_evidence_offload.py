"""Synthetic process-restart acceptance for isolated structured evidence storage."""
import argparse
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys
# Standalone synthetic checks must not inherit remote tracing.
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
from experiments.architecture_candidates.evidence_store import EvidenceStore,Scope
from tests.large_observation_fixture import fixture,MARKER
from tcm.research_context import project,ContextPolicy
from langchain_core.messages import HumanMessage,ToolMessage


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--worker',choices=['write','read']);args=p.parse_args()
    scope=Scope('synthetic-owner','synthetic-run','context-synthetic-v1','synthetic')
    if args.worker:
        store=EvidenceStore(args.out/'evidence.sqlite3')
        if args.worker=='write':
            ref=store.put(scope,'obs-1',fixture(14000))
            (args.out/'reference.json').write_text(json.dumps(ref))
        else:
            ref=json.loads((args.out/'reference.json').read_text())
            eid,digest,payload=store.load(scope,ref['evidence_handle'])
            result=store.read(scope,ref['evidence_handle'],field='target_ids',offset=7000,limit=1)
            assert payload==fixture(14000) and result['data']['items']==[MARKER]
            assert result['data']['content_sha256']==digest and eid=='obs-1'
            (args.out/'read.json').write_text(json.dumps(result))
        return
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    for step in ('write','read'):
        subprocess.run([sys.executable,'-m','scripts.check_evidence_offload','--out',str(args.out),'--worker',step],check=True,timeout=20)
    ref=json.loads((args.out/'reference.json').read_text())
    messages=[HumanMessage(content='Read the original middle item'),ToolMessage(content=json.dumps(ref),tool_call_id='offload')]
    _,check=project(messages,[],ContextPolicy(max_bytes=18000,reserve_bytes=2000,keep_recent_tools=1))
    assert check['fits']
    payload=fixture(14000)
    report={'status':'passed','external_calls':0,'synthetic_only':True,'separate_process_read':True,
        'original_structure_preserved':True,'marker_recovered':True,'latest_reference_fits':check,
        'pretty_payload_bytes':len(json.dumps(payload,indent=2).encode()),'reference_bytes':len(json.dumps(ref).encode()),
        'source_binding_preserved':True,'limits':['Not integrated into research_run or chat',
        'No model comprehension evaluation','Graceful writer exit then new reader process; not SIGKILL',
        'Private SQLite authority; not tamperproof against a database administrator']}
    paths=['experiments/architecture_candidates/evidence_store.py','scripts/check_evidence_offload.py','tcm/research_evidence_read.py']
    report['source_sha256']={s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in paths}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
