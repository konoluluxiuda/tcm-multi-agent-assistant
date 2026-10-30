"""Preview-first bounded synthetic F/P/A pilot. No provider call without explicit opt-in."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
CASE_IDS=('B01','B04','R01')


def preview():
    suite_path=ROOT/'eval/dataops_tasks_v1.json';suite=json.loads(suite_path.read_text())
    return {'version':'dataops-synthetic-live-pilot-v3','status':'prepared_not_run',
        'cases':[{'case_id':c['case_id'],'question':c['question'],'goal':c['goal'],'parent_goal':c.get('parent_goal')}
                 for c in suite['cases'] if c['case_id'] in CASE_IDS],
        'suite_sha256':hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        'strategies':{'F':'fixed host, no model','P':'one native plan per goal, then host tools','A':'native observation tool loop'},
        'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash',
        'max_requests_total':36,'max_A_requests_per_goal':8,'max_P_requests_per_goal':1,
        'max_response_corrections_per_A_goal':1,
        'correction_policy':'Only a received, rejected tool action may get one new decision with sanitized feedback. Counts within the same eight requests; unknown dispatch is never resent.',
        'max_output_tokens':1200,'max_request_bytes':65536,'request_timeout_seconds':30,'retries':0,
        'thinking':{'type':'disabled'},
        'provider_token_parameter':'max_tokens',
        'data':'Only fixed synthetic goals, static instructions/tool definitions, synthetic observations and test evidence.',
        'excluded':['real ETCM','real pharmacopoeia','PDF','user history','credentials in prompts'],
        'real_model_calls':0,'authorization_required_for_execution':True}


def budget_transport(out, *, inner=None):
    import httpx
    from tcm.dataops.evaluation import write
    class BudgetTransport(httpx.BaseTransport):
        def __init__(self):self.inner=inner if inner is not None else httpx.HTTPTransport(retries=0);self.requests=[]
        def handle_request(self,request):
            body=request.read();url=urlsplit(str(request.url))
            if (request.method!='POST' or url.scheme!='https' or url.hostname!='api.deepseek.com' or
                url.port not in (None,443) or url.path!='/chat/completions' or
                len(self.requests)>=36 or len(body)>65536):raise RuntimeError('Provider protocol budget/recipient rejected')
            data=json.loads(body)
            if (data.get('model')!='deepseek-flash' or 'max_completion_tokens' in data or
                    type(data.get('max_tokens')) is not int or not 1<=data['max_tokens']<=1200):
                raise RuntimeError('Model/output protocol rejected')
            if data.get('thinking')!={'type':'disabled'}:
                raise RuntimeError('Required tool choice needs explicit non-thinking mode')
            self.requests.append({'sequence':len(self.requests)+1,'bytes':len(body),'outcome':'unknown'})
            write(out/'dispatch.json',self.requests)
            response=self.inner.handle_request(request)
            self.requests[-1]['outcome']='http_'+str(response.status_code)
            if response.status_code==200:
                try:
                    payload=json.loads(response.read());choices=payload.get('choices') or []
                    message=choices[0].get('message',{}) if choices else {}
                    calls=message.get('tool_calls') or []
                    allowed={'set_plan','query_records','inspect_sources','lookup_document_fields','compare_fields',
                             'read_evidence','ask_user','build_artifact','verify_delivery','finish_task'}
                    usage=payload.get('usage') or {}
                    self.requests[-1]['reply_summary']={'tool_call_count':len(calls),
                        'tool_names':[c.get('function',{}).get('name') if c.get('function',{}).get('name') in allowed
                                      else 'unregistered' for c in calls[:4]],
                        'usage':{k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens')
                                 if type(usage.get(k)) is int and usage[k]>=0}}
                except (ValueError,AttributeError,TypeError):
                    self.requests[-1]['reply_summary']={'status':'unparsed'}
            if response.status_code>=400:
                # Read only a short provider diagnostic, never save headers or prompt bodies.
                try:
                    error=json.loads(response.read()).get('error',{})
                    message=str(error.get('message',''))[:1000]
                    credential=request.headers.get('authorization','').removeprefix('Bearer ')
                    if credential:message=message.replace(credential,'[REDACTED]')
                    self.requests[-1]['error']={k:str(error[k])[:100] for k in ('type','code','param') if error.get(k) is not None}
                    self.requests[-1]['error']['message']=message[:500]
                except (ValueError,AttributeError,TypeError):
                    self.requests[-1]['error']={'type':'unparsed_provider_response'}
            write(out/'dispatch.json',self.requests)
            return response
        def close(self):self.inner.close()
    return BudgetTransport()


def configured_model(key, http):
    from langchain_openai import ChatOpenAI
    class DeepSeekChat(ChatOpenAI):
        # Pinned LangChain converts max_tokens into OpenAI's newer alias. DeepSeek
        # documents max_tokens, so normalize the provider payload at this boundary.
        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload=super()._get_request_payload(input_,stop=stop,**kwargs)
            if 'max_completion_tokens' in payload:payload['max_tokens']=payload.pop('max_completion_tokens')
            return payload
    return DeepSeekChat(model='deepseek-flash',api_key=key,base_url='https://api.deepseek.com',
        max_retries=0,max_tokens=1200,timeout=30,http_client=http,extra_body={'thinking':{'type':'disabled'}})


def execute(args):
    if args.out is None:raise ValueError('--out is required for execution')
    if args.out.exists():raise FileExistsError('No replay or overwrite of a provider run; use a separately authorized new directory')
    os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    import httpx
    from dotenv import dotenv_values
    from tests.dataops_fixture import make_sources
    from tcm.dataops.data_access import canonical,fingerprint
    from tcm.dataops.evaluation import SourceOracle,MeasuredDriver,run_case,write
    from tcm.dataops.model import OpenAIToolDriver,ModelResponseRejected
    from tcm.dataops.planning import NativePlanDriver,request_plan
    spec=preview();args.out.mkdir(parents=True)
    write(args.out/'protocol.json',spec)
    configured=dotenv_values(ROOT/'.env') if (ROOT/'.env').is_file() else {}
    key=os.environ.get('CHAT_API_KEY') or configured.get('CHAT_API_KEY')
    if not key:raise ValueError('Configured CHAT_API_KEY is missing')
    # Do not follow any endpoint in environment files; this pilot has a single bounded recipient.
    transport=budget_transport(args.out)
    with httpx.Client(transport=transport,timeout=30,follow_redirects=False,trust_env=False) as http:
        model=configured_model(key,http)
        records,factory=make_sources(args.out/'fixture');oracle=SourceOracle(args.out/'fixture')
        suite=json.loads((ROOT/'eval/dataops_tasks_v1.json').read_text());cases=[c for c in suite['cases'] if c['case_id'] in CASE_IDS]
        references={c['case_id']:oracle.reference(c) for c in cases};write(args.out/'reference.json',references)
        code={str(p.relative_to(ROOT)):fingerprint(p) for p in (ROOT/'tcm/dataops').glob('*.py')}
        code.update({str(p.relative_to(ROOT)):fingerprint(p) for p in (Path(__file__).resolve(),ROOT/'tests/dataops_fixture.py')})
        write(args.out/'source_code.json',code)
        class LiveDriver:
            def __init__(self,strategy,case):
                self.strategy,self.case=strategy,case;self.context_bytes=[];self.plans={};self.usage=[]
                self.label='dataops-live-'+strategy+'-synthetic-v3'
                self.max_response_corrections=spec['max_response_corrections_per_A_goal'] if strategy=='A' else 0
                self.native=OpenAIToolDriver(model,authorized=True,
                    max_response_corrections=self.max_response_corrections) if strategy=='A' else None
            def decide(self,context):
                self.context_bytes.append(len(canonical(context).encode()))
                if self.native:
                    try:
                        action=self.native.decide(context)
                    except ModelResponseRejected as exc:
                        self.usage.append(exc.diagnostics.get('usage',{}));raise
                    self.usage.append(action.get('usage',{}));return action
                sig=hashlib.sha256(canonical(context['goal']).encode()).hexdigest()
                usage={}
                if sig not in self.plans:
                    receipt=args.out/'plans'/(self.case['case_id']+'-'+sig+'.json');receipt.parent.mkdir(exist_ok=True)
                    if receipt.exists():raise RuntimeError('Planner outcome must not be resent')
                    write(receipt,{'status':'dispatch_reserved'})
                    steps,usage=request_plan(model,context['goal'],self.case['question'],authorized=True)
                    write(receipt,{'status':'response_recorded','steps':steps,'usage':usage})
                    self.plans[sig]=NativePlanDriver(steps);self.usage.append(usage)
                action=self.plans[sig].decide(context);action['usage']=usage;return action
        results=[]
        for case in cases:
            for strategy in ('F','P','A'):
                captured=[]
                def driver_factory(s,c):
                    driver=MeasuredDriver(s,c['goal']) if s=='F' else LiveDriver(s,c)
                    captured.append(driver);return driver
                before=len(transport.requests)
                directory=args.out/'runs'/case['case_id']/strategy
                result=run_case(deepcopy(case),references[case['case_id']],oracle,strategy,factory,directory,
                                driver_factory=driver_factory)
                result.update(case_id=case['case_id'],strategy=strategy,external_model_calls=len(transport.requests)-before,
                    planning_stub_steps=0,provider_usage=getattr(captured[0],'usage',None),cost=None)
                directory.mkdir(parents=True,exist_ok=True);write(directory/'evaluation.json',result);results.append(result)
                write(args.out/'progress.json',results)
        report={'status':'passed' if all(r['status']=='passed' for r in results) else 'failed',
            'kind':'three_known_development_synthetic_provider_pilot','results':results,
            'actual_provider_requests':len(transport.requests),'cost':None,
            'real_data_or_history_sent':False,'holdout_or_business_accuracy_claim':False}
        write(args.out/'summary.json',report)
        print(json.dumps({'status':report['status'],'actual_provider_requests':len(transport.requests)},ensure_ascii=False))
        return 0 if report['status']=='passed' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute-synthetic',action='store_true',help='Requires explicit separate authorization for this new pilot')
    p.add_argument('--out',type=Path)
    args=p.parse_args()
    if not args.execute_synthetic:
        print(json.dumps(preview(),ensure_ascii=False,indent=2));return 0
    return execute(args)


if __name__=='__main__':raise SystemExit(main())
