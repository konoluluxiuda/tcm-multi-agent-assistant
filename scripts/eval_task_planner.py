"""Known synthetic question-to-plan comparison; never reads corpus or chat history."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from tcm.task_plan import TaskPlan
from tcm.task_planner import NativeTaskPlanner, conservative_plan, create_proposal
from tcm.research_task import ResearchTaskEngine, ScriptedToolModel
from tcm.research_task_fixtures import SyntheticResearchTools, NAMES
from tcm.research_task_store import ResearchTaskStore

ROOT=Path(__file__).resolve().parents[1]
CASES=[
 {'id':'P01','question':'只核对示例甲草和示例乙草有哪些共同靶点，给我双方原始记录就好。','intent':'source_relations','names':NAMES},
 {'id':'P02','question':'示例乙草与示例甲草是否挂了同样的标识？先不要看研究记录。','intent':'source_relations','names':NAMES[::-1]},
 {'id':'P03','question':'比较示例甲草和示例乙草，看看药材本体的研究记录能不能支持，成分的不要混在一起。','intent':'evidence_review','names':NAMES},
 {'id':'P04','question':'示例甲草和示例乙草的来源关系之外，也核查已有合成研究记录的对象和撤稿状态。','intent':'evidence_review','names':NAMES},
 {'id':'P05','question':'帮我比较示例甲草和示例乙草。','intent':None,'names':NAMES},
 {'id':'P06','question':'核查示例甲草的共同靶点来源关系。','intent':'source_relations','names':NAMES[:1]},
 {'id':'P07','question':'查查这两味药之间的来源关系。','intent':'source_relations','names':[]},
 {'id':'P08','question':'请比较示例甲草和示例乙草的价格，别改成靶点或研究查询。','intent':None,'names':NAMES},
]


def expected(case):
    names=case['names']
    return TaskPlan(intent=case['intent'],herb_one=names[0] if names else None,
                    herb_two=names[1] if len(names)>1 else None)


def agreement(plan,reference):
    return plan.intent==reference.intent and set(plan.names())==set(reference.names())


def score(result,case):
    ref=expected(case)
    return {'validated_goal_entities_match':result.status=='ok' and agreement(result.plan,ref),
            'raw_goal_entities_match':result.status=='ok' and agreement(result.raw_plan or result.plan,ref),
            'missing_field_count_match':len(result.plan.missing())==len(ref.missing()),
            'unmentioned_entities_removed':result.grounded_entities_removed}


def exercise_proposal(engine,task,result,case):
    """Only execute matching complete plans; simulated users never repair scores."""
    evaluation=score(result,case)
    failures=[]
    if result.status!='ok':failures.append('planner_'+result.status)
    if result.plan.intent!=case['intent']:
        failures.append('unsupported_scope_selected' if case['intent'] is None else 'missed_or_wrong_goal')
    if set(result.plan.names())!=set(case['names']):failures.append('wrong_or_missing_entities')
    if not evaluation['raw_goal_entities_match'] and evaluation['validated_goal_entities_match']:
        failures.append('raw_entity_error_intercepted')
    if failures:return {'status':'needs_correction','failure_types':failures,'tools_after_proposal':task['tool_calls']}
    if result.plan.missing():return {'status':'clarification_required','failure_types':[],
                                    'tools_after_proposal':task['tool_calls'],'missing_fields':result.plan.missing()}
    try:
        selection={k:getattr(result.plan,k) for k in ('intent','herb_one','herb_two')}
        t=engine.choose(task['id'],selection,revision=task['revision']);t=engine.run(t['id'])
        source_pause=t['status']=='waiting_sources' and t['sources']==[]
        if not source_pause:raise ValueError('Source pause missing')
        sources={n:'SYN-A1' if n==NAMES[0] else 'SYN-B1' for n in t['names']}
        t=engine.choose(t['id'],sources,revision=t['revision']);t=engine.run(t['id'])
        target_pause=t['status']=='waiting_targets' and t['targets']==[]
        if target_pause:
            t=engine.choose(t['id'],['SYN-T1'],revision=t['revision']);t=engine.run(t['id'])
        expected_status=('partial' if case['intent']=='evidence_review' and
                         case['profile'] in ('ingredient_only','wrong_object','withdrawn','unavailable','no_common') else 'complete')
        report=t['report'];studies=report['study_evidence']
        checks={'expected_status':t['status']==expected_status,'source_pause':source_pause,
                'target_pause_or_valid_empty':target_pause or (case['profile']=='no_common' and not t['targets']),
                'chosen_sources_preserved':set(t['sources'])==set(sources.values()),
                'bounded_tools':t['tool_calls']<=t['max_tool_calls'],'no_executor_provider_calls':t['provider_attempt_slots']==0,
                'partial_has_gaps':t['status']!='partial' or bool(report['gaps']),
                'final_state_durable':ResearchTaskStore(engine.store.path).get(t['id'])==t,
                'no_unselected_studies':all(r['target_id'] in t['targets'] for r in studies),
                'selected_relations_have_both_sources':all(
                    any(r['id']==target and {w['source_id'] for w in r['witnesses']}==set(t['sources'])
                        for r in report['relation_evidence']) for target in t['targets'])}
        if case['intent']=='source_relations':
            checks['no_unselected_relation_rows']=all(r['id'] in t['targets'] for r in report['relation_evidence'])
            checks['no_research_search']=not any(e.get('name')=='search_literature' for e in t['events'])
            checks['no_studies']=studies==[]
        if case['profile'] in ('ingredient_only','wrong_object','withdrawn'):
            expected_class={'ingredient_only':'ingredient_only','wrong_object':'excluded_wrong_object','withdrawn':'excluded_retracted'}[case['profile']]
            checks['evidence_boundary']=bool(studies) and all(r['classification']==expected_class for r in studies)
        ok=all(checks.values())
        return {'status':'execution_'+t['status'] if ok else 'execution_failed','checks':checks,
                'failure_types':[] if ok else ['execution_contract'],'tool_calls':t['tool_calls'],
                'simulated_user':True,'original_plan_changed':False}
    except Exception as error:
        return {'status':'execution_failed','failure_types':['execution_exception'],
                'error_type':type(error).__name__,'simulated_user':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--live-synthetic',action='store_true')
    parser.add_argument('--suite',choices=('development','acceptance-v1'),default='development');args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    files=['scripts/eval_task_planner.py','tcm/task_planner.py','tcm/task_plan.py','tcm/research_task.py']
    cases=CASES;split='known_author_designed_development'
    if args.suite=='acceptance-v1':
        fixture='eval/task_planner_acceptance_v1.json';files.append(fixture)
        dataset=json.loads((ROOT/fixture).read_text());cases=dataset['cases'];split=dataset['split']
    manifest={'data_mode':'synthetic_only','split':split,'suite':args.suite,
        'cases':cases,'max_provider_calls':len(cases),'calls_per_question':1,'retries':0,
        'live':args.live_synthetic,'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files},
        'scoring':'Expected goal and unordered entity set; raw and grounded outputs separated. All proposals require user review.',
        'baseline':'Conservative explicit-name/keyphrase parser authored with these cases; not a held-out baseline.'}
    for name in files:
        target=args.out/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    planner=None
    if args.live_synthetic:
        from dotenv import load_dotenv
        load_dotenv();os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
        endpoint=urlparse(os.getenv('CHAT_BASE_URL',''))
        if endpoint.scheme!='https' or endpoint.hostname!='api.deepseek.com' or endpoint.username:
            raise ValueError('Only configured HTTPS api.deepseek.com permitted')
        from config import create_chat_model
        planner=NativeTaskPlanner(create_chat_model(temperature=0,timeout=15,max_retries=0,max_tokens=600),live_provider=True)
        manifest['provider']={'host':endpoint.hostname,'model':os.getenv('CHAT_MODEL'),'timeout':15,'max_tokens':600}
        (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    store=ResearchTaskStore(args.out/'tasks.sqlite3');rows=[]
    for case in cases:
        # The external input can only be this frozen list, never a file, stdin or user history.
        for policy in (('deterministic','model') if planner else ('deterministic',)):
            result=planner.plan(case['question']) if policy=='model' else conservative_plan(case['question'])
            engine=ResearchTaskEngine(store,ScriptedToolModel(),SyntheticResearchTools(case.get('profile','direct')))
            task=engine.run(create_proposal(engine,case['question'],result)['id'])
            row={'id':case['id'],'policy':policy,'result':result.model_dump(),'score':score(result,case),
                 'proposal_waits_for_review':task['status']=='waiting_plan' and task['tool_calls']==0,
                 'task_id':task['id']}
            if args.suite=='acceptance-v1':row['execution']=exercise_proposal(engine,task,result,case)
            rows.append(row);path=args.out/(case['id']+'-'+policy+'.json')
            path.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)
            print(json.dumps({'id':case['id'],'policy':policy,'status':result.status,**row['score']},ensure_ascii=False),flush=True)
    summary={'status':'completed','mode':'live_synthetic' if planner else 'offline_baseline_only',
        'cases':len(cases),'suite':args.suite,'split':split,'provider_calls':sum(r['result']['provider_calls'] for r in rows),
        'policies':{p:{'run':sum(r['policy']==p for r in rows),
            'raw_goal_entities_match':sum(r['policy']==p and r['score']['raw_goal_entities_match'] for r in rows),
            'validated_goal_entities_match':sum(r['policy']==p and r['score']['validated_goal_entities_match'] for r in rows),
            'duration_ms_total':round(sum(r['result']['duration_ms'] for r in rows if r['policy']==p),3),
            'execution_outcomes':{s:sum(r['policy']==p and r.get('execution',{}).get('status')==s for r in rows)
                                  for s in ('execution_complete','execution_partial','clarification_required','needs_correction','execution_failed')}}
            for p in sorted({r['policy'] for r in rows})},
        'all_proposals_wait_for_review':all(r['proposal_waits_for_review'] for r in rows),
        'business_accuracy':None,'independent_review':False,'cost':None,
        'limitations':['Author-designed synthetic questions, not a general intent accuracy result or independent holdout.',
            'Expected labels are author-designed, not independent human review.',
            'Task execution remains deterministic and requires source/target choices.',
            'Single run latency, price not configured, failures may have unknown token usage.']}
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
