"""Local synthetic research tasks: native tool calling is explicit opt-in.

The task engine has no real source adapter. --live-synthetic sends only fictional
fixtures/action observations to the configured api.deepseek.com endpoint.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from tcm.research_task import ResearchTaskEngine, NativeToolModel, ScriptedToolModel
from tcm.research_task_fixtures import SyntheticResearchTools,PROFILES,NAMES
from tcm.research_task_store import ResearchTaskStore
from scripts.run_offline_ci import ROOT


def controller(policy):
    if policy=='scripted':return ScriptedToolModel(adaptive=True)
    if policy=='fixed':return ScriptedToolModel(adaptive=False)
    os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    from config import create_chat_model
    endpoint=urlparse(os.environ.get('CHAT_BASE_URL',''))
    if endpoint.hostname!='api.deepseek.com' or endpoint.scheme!='https' or endpoint.username:
        raise ValueError('Live synthetic smoke test only permits configured api.deepseek.com')
    return NativeToolModel(create_chat_model(temperature=0,timeout=15,max_retries=0,max_tokens=600),live_provider=True)


def summary(task,policy):
    usage=[e.get('usage') for e in task['events'] if e.get('usage')]
    return {'id':task['id'],'revision':task['revision'],'status':task['status'],'data_mode':task['data_mode'],
        'policy':policy,'pending':task['pending'],'controller_steps':task['model_calls'],'tool_attempts':task['tool_calls'],
        'provider_attempt_slots_reserved':task['provider_attempt_slots'],
        'active_ms':task['active_ms'],'usage':usage,'cost':None,
        'report':task['report'],'failure':task.get('failure'),'notice':'Synthetic software workflow only; scripted/fixed policies are test drivers, not real model comparisons.'}


from tcm.research_task_report import markdown


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('create','resume','show','export','delete','demo'))
    parser.add_argument('--database',type=Path,default=ROOT/'var/research_tasks_synthetic/tasks.sqlite3')
    parser.add_argument('--task');parser.add_argument('--revision',type=int)
    parser.add_argument('--profile',choices=PROFILES,default='retry')
    parser.add_argument('--policy',choices=('scripted','fixed','native'),default='scripted')
    parser.add_argument('--live-synthetic',action='store_true')
    parser.add_argument('--selection',help='JSON mapping for sources or JSON array for targets; only explicit offered IDs')
    parser.add_argument('--retry-interrupted',action='store_true')
    parser.add_argument('--retry-failed-model',action='store_true')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.policy=='native' and not args.live_synthetic:parser.error('Native model requires explicit --live-synthetic')
    store=ResearchTaskStore(args.database)
    task=None
    if args.action in ('show','export','delete','resume'):
        if not args.task:parser.error('--task required')
        task=store.get(args.task)
    if args.action=='delete':
        if args.revision is None:parser.error('--revision required')
        store.delete(args.task,args.revision);print(json.dumps({'status':'deleted','id':args.task}));return
    if args.action=='export':
        if args.out is None:parser.error('--out required')
        args.out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with args.out.open('x',encoding='utf-8') as f:os.chmod(args.out,0o600);f.write(markdown(task))
        print(json.dumps({'status':'exported','out':str(args.out)}));return
    if args.action=='show':print(json.dumps(summary(task,'stored_result'),ensure_ascii=False,indent=2));return
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with args.out.open('x',encoding='utf-8') as f:
            os.chmod(args.out,0o600);json.dump({'status':'reserved_before_any_provider_call'},f)
    profile=task['profile'] if task else args.profile
    engine=ResearchTaskEngine(store,controller(args.policy),SyntheticResearchTools(profile))
    if args.action in ('create','demo'):task=engine.create()
    if args.selection:
        if args.revision is None:parser.error('--revision required for selection')
        task=engine.choose(task['id'],json.loads(args.selection),revision=args.revision)
    if args.retry_interrupted:
        if args.revision is None:parser.error('--revision required for recovery')
        task=engine.retry_interrupted(task['id'],revision=args.revision)
    if args.retry_failed_model:
        if args.revision is None:parser.error('--revision required for recovery')
        task=engine.retry_failed_model_step(task['id'],revision=args.revision)
    task=engine.run(task['id'])
    if args.action=='demo':
        # This is a labelled simulated user, not an autonomous model choosing identities.
        if task['status']=='waiting_sources':
            task=engine.choose(task['id'],{NAMES[0]:'SYN-A1',NAMES[1]:'SYN-B1'},revision=task['revision'])
            task=engine.run(task['id'])
        if task['status']=='waiting_targets':
            task=engine.choose(task['id'],['SYN-T1'],revision=task['revision'])
            task=engine.run(task['id'])
    result=summary(task,args.policy)
    if args.action=='demo':result['user_selection']='simulated_explicit_preregistered_ids'
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with args.out.open('w',encoding='utf-8') as f:os.chmod(args.out,0o600);json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
