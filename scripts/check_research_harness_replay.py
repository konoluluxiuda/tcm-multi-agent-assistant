"""Offline admission check of frozen failed actions, not a new model evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from tcm.dataops.research_context import action_rejection
from tcm.dataops.research_model import definitions,valid

ROOT=Path(__file__).resolve().parents[1]
CODE=['tcm/dataops/research_context.py','tcm/dataops/research_model.py',
      'tcm/dataops/research_graph.py','scripts/check_research_harness_replay.py']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(prior,out):
    out.mkdir(parents=True,exist_ok=False)
    records=[];inputs={}
    for case in ('S01_A','S02_A'):
        requests=sorted((prior/case).glob('request_*.private.json'))
        if len(requests)!=4:raise ValueError('Expected the original four recorded decisions per case')
        for step,path in enumerate(requests,1):
            response=path.with_name(path.name.replace('request_','response_'))
            inputs[case+'/'+path.name]=sha(path);inputs[case+'/'+response.name]=sha(response)
            request=json.loads(path.read_text())['request']
            context=json.loads(next(m['content'] for m in reversed(request['messages']) if m['role']=='user'))
            reply=json.loads(response.read_text())['response']['choices'][0]['message']['tool_calls']
            if len(reply)!=1:raise ValueError('Expected a single recorded native action')
            fn=reply[0]['function'];tool=fn['name'];args=json.loads(fn['arguments'])
            exposed=[t['function']['name'] for t in definitions(context)]
            reason=action_rejection(context,tool,args) if valid(tool,args) else 'invalid_tool_arguments'
            admitted=tool in exposed and reason is None
            expected=step<=2
            records.append({'case':case,'step':step,'tool':tool,'old_schema_exposed':any(
                t['function']['name']==tool for t in request['tools']),
                'new_schema_exposed':tool in exposed,'host_rejection':reason,
                'admitted':admitted,'expected_admitted':expected,'check_passed':admitted==expected,
                'available_alternatives':exposed})
    for name in CODE:
        dest=out/'frozen_source'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    result={'version':'research-harness-v2-offline-replay-v1',
            'status':'passed' if all(r['check_passed'] for r in records) else 'failed',
            'records':records,'recorded_actions':len(records),'useful_original_actions_admitted':sum(r['admitted'] for r in records),
            'known_no_progress_actions_blocked':sum(not r['admitted'] for r in records),
            'provider_requests':0,'new_model_task_completion_measured':False,'historical_A_score':'0/2 unchanged',
            'input_files_sha256':inputs,'code_sha256':{n:sha(ROOT/n) for n in CODE}}
    (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior',type=Path,default=ROOT/'var/rag_r4_v1/live_continuation')
    p.add_argument('--out',type=Path,required=True);args=p.parse_args();result=check(args.prior,args.out)
    print(json.dumps({k:v for k,v in result.items() if k not in ('input_files_sha256','code_sha256','records')},ensure_ascii=False))
    raise SystemExit(0 if result['status']=='passed' else 1)
