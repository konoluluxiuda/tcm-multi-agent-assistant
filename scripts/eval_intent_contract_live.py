"""One request per frozen synthetic question; no corpus/tool execution or retries."""
import argparse
import hashlib
import json
from pathlib import Path
from tcm.intent_planner import QueryIntent, mapped, plan_question

ROOT=Path(__file__).resolve().parents[1]


def score(case, route, proposal):
    if route.get('intent_status') != 'ok' or proposal is None:
        return {'passed':False,'failure':'invalid_or_unavailable'}
    try:
        actual=QueryIntent.model_validate(proposal)
        mapped(actual,case['question'])
    except Exception:
        return {'passed':False,'failure':'invalid_contract'}
    failures=[]
    for key,value in case['expected'].items():
        got=getattr(actual,key)
        if (set(got)!=set(value)) if key=='fields' else (got!=value):
            failures.append(key)
    return {'passed':not failures,'failure':','.join(failures) or None}


class Recorder:
    def __init__(self,sdk):self.sdk=sdk;self.calls=0;self.proposal=None;self.usage=None
    def bind_tools(self,*args,**kwargs):
        self.bound=self.sdk.bind_tools(*args,**kwargs);return self
    def invoke(self,messages):
        if self.calls:raise RuntimeError('No retry allowed')
        self.calls+=1
        reply=self.bound.invoke(messages)
        calls=getattr(reply,'tool_calls',[])
        if len(calls)==1 and calls[0].get('name')=='submit_query_intent':self.proposal=calls[0].get('args')
        usage=getattr(reply,'usage_metadata',None)
        if usage:self.usage={k:usage.get(k) for k in ('input_tokens','output_tokens','total_tokens')}
        return reply


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--live',action='store_true')
    args=p.parse_args()
    source=ROOT/'eval/intent_contract_synthetic_v1.json'
    suite=json.loads(source.read_text())
    args.out.mkdir(parents=True,exist_ok=False)
    manifest={'question_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'planner_sha256':hashlib.sha256((ROOT/'tcm/intent_planner.py').read_bytes()).hexdigest(),
              'mode':'live' if args.live else 'offline_contract_only','max_calls':len(suite['cases']) if args.live else 0,
              'per_question_max_calls':1,'max_output_tokens':650,'timeout_seconds':10,'retries':0}
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from unittest.mock import patch
    from tcm.assistant_router import resolve
    rows=[]
    for case in suite['cases']:
        with patch.dict('os.environ',{'ENABLE_NATURAL_ROUTING':'1'}), patch('tcm.agent_research.runtime.get_service',return_value=None):
            baseline=resolve(case['question'])
        if args.live:
            # Durable reservation before SDK construction/call; existing output is never resumed.
            (args.out/(case['id']+'.reserved')).write_text('one attempt reserved\n')
            from config import create_chat_model
            import os
            prefix='INTENT' if all(os.getenv('INTENT_'+k) for k in ('MODEL','API_KEY','BASE_URL')) else 'CHAT'
            sdk=create_chat_model(temperature=0,timeout=10,max_retries=0,max_tokens=650,env_prefix=prefix)
            recorder=Recorder(sdk);route=plan_question(case['question'],sdk=recorder)
            proposal=recorder.proposal;usage=recorder.usage
        else:
            # Contract validity only, explicitly not language comprehension.
            proposal=dict(case['expected'])
            if proposal.get('priority_field'):proposal['priority_evidence']=case['question']
            intent=QueryIntent.model_validate(proposal)
            route={**mapped(intent,case['question']),'intent_status':'ok','intent_model_calls':0}
            usage=None
        row={'id':case['id'],'rule_route':baseline,'proposal':proposal,'route':route,'usage':usage,**score(case,route,proposal)}
        rows.append(row)
        (args.out/(case['id']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'id':case['id'],'passed':row['passed'],'failure':row['failure']},ensure_ascii=False),flush=True)
    summary={**manifest,'total':len(rows),'passed':sum(r['passed'] for r in rows),
             'model_calls':sum(r['route']['intent_model_calls'] for r in rows),
             'rule_unmatched':sum(r['rule_route'] is None for r in rows),
             'cost':None,'cost_note':'No price assumption; token usage reported only when supplied'}
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
