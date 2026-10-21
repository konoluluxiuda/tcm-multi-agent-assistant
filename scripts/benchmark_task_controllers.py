"""Frozen synthetic execution comparison, not intent/retrieval/clinical accuracy."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlparse

from pydantic import BaseModel,ConfigDict
from tcm.research_task import ResearchTaskEngine,ScriptedToolModel,NativeToolModel,public_state
from tcm.research_task_fixtures import SyntheticResearchTools,NAMES
from tcm.research_task_store import ResearchTaskStore

ROOT=Path(__file__).resolve().parents[1]
CASES=[{'id':'C01','profile':'direct','status':'complete','studies':2},
       {'id':'C02','profile':'retry','status':'complete','studies':2},
       {'id':'C03','profile':'wrong_object','status':'partial','studies':2},
       {'id':'C04','profile':'unavailable','status':'partial','studies':0}]
POLICIES=('deterministic','one_plan','bounded_loop')
PLAN_SYSTEM='''这是合成资料软件控制流程对照，不做真实药材或文献研究。来源和靶点已由模拟用户明确选择。
只调用submit_execution_policy一次，提交有界执行策略，不写思维链或自由回答。
工具初次返回空结果时允许expanded补检一次；应核查所有实际返回记录，区分对象不符和成分范围。
运行器按策略使用相同工具执行并生成证据/缺口报告，你不能编造来源或研究ID。'''


class ExecutionPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    retry_empty:bool
    inspect_returned:bool


class Calls:
    def __init__(self,limit=28):self.limit=limit;self.used=0
    def reserve(self):
        if self.used>=self.limit:raise RuntimeError('Global provider budget exhausted')
        self.used+=1


class MeteredSDK:
    def __init__(self,sdk,calls):self.sdk,self.calls=sdk,calls;self.count=0;self.responses=[]
    def bind_tools(self,*args,**kwargs):self.sdk=self.sdk.bind_tools(*args,**kwargs);return self
    def invoke(self,messages):
        self.calls.reserve();self.count+=1
        reply=self.sdk.invoke(messages)
        self.responses.append({'usage':getattr(reply,'usage_metadata',None),
                               'model':getattr(reply,'response_metadata',{}).get('model_name'),
                               'tool_calls':deepcopy(getattr(reply,'tool_calls',[])),
                               'invalid_tool_calls':deepcopy(getattr(reply,'invalid_tool_calls',[]))})
        return reply


class OfflineSDK:
    """Mock transport for verifying the benchmark itself; never a model result."""
    def bind_tools(self,tools,**kwargs):self.plan=tools[0]['function']['name']=='submit_execution_policy';return self
    def invoke(self,messages):
        from langchain_core.messages import AIMessage
        decision=({'name':'submit_execution_policy','args':{'retry_empty':True,'inspect_returned':True}}
                  if self.plan else ScriptedToolModel().decide(json.loads(messages[1].content)))
        return AIMessage(content='',tool_calls=[{**decision,'id':'offline-call','type':'tool_call'}])


class OnePlan:
    def __init__(self,plan):self.plan=plan
    def decide(self,task):
        for target in task['targets']:
            searches=[s for s in task['searches'] if s['target_id']==target]
            if not searches:return {'name':'search_literature','args':{'target_id':target,'variant':'initial'}}
            if self.plan.retry_empty and searches[-1]['status']=='empty' and len(searches)==1:
                return {'name':'search_literature','args':{'target_id':target,'variant':'expanded'}}
            if self.plan.inspect_returned:
                for s in searches:
                    for row in s['records']:
                        if row['id'] not in task['studies']:return {'name':'inspect_study','args':{'study_id':row['id']}}
        return {'name':'finish_task','args':{}}


def plan_once(sdk,task):
    from langchain_core.messages import SystemMessage,HumanMessage
    tool={'type':'function','function':{'name':'submit_execution_policy','description':'Submit a bounded execution policy.',
                                       'parameters':ExecutionPolicy.model_json_schema()}}
    reply=sdk.bind_tools([tool],tool_choice='auto',parallel_tool_calls=False).invoke([
        SystemMessage(content=PLAN_SYSTEM),HumanMessage(content=json.dumps(public_state(task),ensure_ascii=False))])
    if getattr(reply,'invalid_tool_calls',[]) or len(reply.tool_calls)!=1 or reply.tool_calls[0]['name']!='submit_execution_policy':
        raise ValueError('Exactly one valid execution policy required')
    return ExecutionPolicy.model_validate(reply.tool_calls[0]['args'])


def prepare(store,profile):
    e=ResearchTaskEngine(store,ScriptedToolModel(),SyntheticResearchTools(profile));t=e.run(e.create(max_model_calls=8)['id'])
    t=e.choose(t['id'],{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},revision=t['revision']);t=e.run(t['id'])
    return e.choose(t['id'],['SYN-T1'],revision=t['revision'])


def score(task,case):
    r=task.get('report') or {};studies=r.get('study_evidence',[])
    checks={'expected_status':task['status']==case['status'],'all_expected_records_checked':len(studies)==case['studies'],
            'selected_identities_preserved':task['sources']==['SYN-A1','SYN-B1'] and task['targets']==['SYN-T1'],
            'no_invalid_actions':task['invalid_actions']==0,
            'source_anchors_present':len(r.get('relation_evidence',[]))==2 and all(len(x['witnesses'])==2 for x in r.get('relation_evidence',[])),
            'partial_has_explicit_gaps':task['status']!='partial' or bool(r.get('gaps'))}
    if case['profile']=='wrong_object':checks['wrong_objects_excluded']=len(studies)==2 and all(s['classification']=='excluded_wrong_object' for s in studies)
    if case['profile'] in ('direct','retry'):
        checks['ingredient_not_herb']=sorted(s['classification'] for s in studies)==['herb_source_record','ingredient_only']
    if case['profile']=='retry':checks['observed_empty_then_retry']=[s['variant'] for s in task['searches']]==['initial','expanded']
    return {'execution_contract_passed':all(checks.values()),'checks':checks,'semantic_support_review':'not_performed'}


def run_row(store,case,policy,sdk_factory,calls,mode):
    task=prepare(store,case['profile']);initial_tools=task['tool_calls'];sdk=None;plan=None;failure=None;start=time.monotonic()
    try:
        if policy=='deterministic':driver=ScriptedToolModel(adaptive=True)
        else:
            sdk=MeteredSDK(sdk_factory(),calls)
            if policy=='one_plan':plan=plan_once(sdk,task);driver=OnePlan(plan)
            else:driver=NativeToolModel(sdk,live_provider=mode=='live')
        task=ResearchTaskEngine(store,driver,SyntheticResearchTools(case['profile'])).run(task['id'])
    except Exception as error:
        failure={'phase':'controller_setup_or_plan','error_type':type(error).__name__}
    row={'case_id':case['id'],'policy':policy,'mode':mode,'status':'failed' if failure else task['status'],
         'duration_ms':round((time.monotonic()-start)*1000,3),'timing_scope':'after_same_source_target_selection_includes_controller_and_local_tools',
         'provider_calls':sdk.count if sdk and mode=='live' else 0,'mock_transport_calls':sdk.count if sdk and mode=='offline' else 0,
         'tool_actions':task['tool_calls']-initial_tools,'plan':plan.model_dump() if plan else None,
         'failure':failure or task.get('failure'),'responses':sdk.responses if sdk else [],
         'model_api_cost':0 if policy=='deterministic' else None,'task_id':task['id'],'report':task['report'],
         'actions':task['events'],'evaluation':score(task,case)}
    if failure:row['evaluation']['execution_contract_passed']=False
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--live-synthetic',action='store_true');args=parser.parse_args()
    # Freeze before constructing any provider; refuse to overwrite a previous experiment.
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    paths=['scripts/benchmark_task_controllers.py','tcm/research_task.py','tcm/research_task_fixtures.py','tcm/research_task_store.py']
    manifest={'data_mode':'synthetic_only','split':'known_designed_development','cases':CASES,'policies':POLICIES,
        'model_phase':'after_simulated_explicit_source_and_target_choices','max_provider_calls':28,
        'max_bounded_provider_calls_per_case':6,'max_tool_actions_whole_task':12,
        'baseline':'Adaptive deterministic driver includes empty-result retry and full returned-record inspection.',
        'one_plan':'One model call chooses two boolean policy parameters; fixed interpreter handles returned IDs/conditional retry. Not an open-ended planner.',
        'scoring':'Expected status, record coverage, identity scope, anchors, gaps and invalid actions. No free-form answer or independent semantic scoring.',
        'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        'mode':'live' if args.live_synthetic else 'offline','live_model_config':None}
    (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (args.out/'source').mkdir()
    for p in paths:
        dest=args.out/'source'/p;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/p).read_bytes())
    if args.live_synthetic:
        from dotenv import load_dotenv
        load_dotenv();os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
        endpoint=urlparse(os.getenv('CHAT_BASE_URL',''))
        if endpoint.scheme!='https' or endpoint.hostname!='api.deepseek.com' or endpoint.username:raise ValueError('Only configured HTTPS api.deepseek.com permitted')
        manifest['live_model_config']={'endpoint_host':endpoint.hostname,'model':os.getenv('CHAT_MODEL'),'temperature':0,'timeout':15,'max_retries':0,'max_tokens':600}
        (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        from config import create_chat_model
        factory=lambda:create_chat_model(temperature=0,timeout=15,max_retries=0,max_tokens=600)
    else:factory=OfflineSDK
    rows=[];calls=Calls();store=ResearchTaskStore(args.out/'tasks.sqlite3')
    for index,case in enumerate(CASES):
        # Rotate order to reduce fixed policy-order bias, though one run cannot estimate latency distributions.
        order=POLICIES[index%3:]+POLICIES[:index%3]
        for policy in order:
            row=run_row(store,case,policy,factory,calls,manifest['mode']);rows.append(row)
            path=args.out/(case['id']+'-'+policy+'.json');path.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
            print(json.dumps({k:row[k] for k in ('case_id','policy','status','provider_calls','duration_ms')},ensure_ascii=False),flush=True)
    summaries={policy:{'contract_passed':sum(r['evaluation']['execution_contract_passed'] for r in rows if r['policy']==policy),
                       'run':sum(r['policy']==policy for r in rows),'provider_calls':sum(r['provider_calls'] for r in rows if r['policy']==policy),
                       'duration_ms_total':round(sum(r['duration_ms'] for r in rows if r['policy']==policy),3)} for policy in POLICIES}
    report={'status':'completed','data_mode':'synthetic_only','mode':manifest['mode'],'policy_results':summaries,
            'provider_calls':calls.used if args.live_synthetic else 0,'max_provider_calls':28,
            'business_accuracy':None,'independent_review':False,'cost':None,
            'limits':['Known synthetic execution cases; not novel user questions, real corpus retrieval or real research validation.',
                      'Shared server enforces scope and generates reports; this does not measure free-text hallucinations or medical correctness.',
                      'One-plan search space is only two booleans; it can match the deterministic driver without proving general planning ability.',
                      'Single run and rotated order, no statistical latency or robustness conclusion. Failed calls may have unknown token usage.']}
    (args.out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
