"""Question -> bounded Goal -> F or native A; one shared model goal per case.

Freeze first, execute once. References grade results, never plan actions. No
automatic retry/continuation and no changed old scores. All artifacts stay local.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import time

from scripts import eval_research_questions as questions
from tcm.data_release import check
from tcm.dataops.artifacts import verify
from tcm.dataops.baseline import validate_result
from tcm.dataops.goal_model import GoalResponseRejected
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchFixed,ResearchDriver,configured_model
from tcm.dataops.research_question import bounded_catalog,local_proposal,request_proposal
from tcm.dataops.research_sources import ReleaseSources
from tcm.dataops.workspace import Workspaces
from tcm.mineru_pharmacopoeia import digest

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'eval/research_combined_v1.json'
CODE=list(dict.fromkeys(questions.CODE+['scripts/eval_research_combined.py','eval/research_combined_v1.json']))
save=questions.save


def hashes():return {p:digest(ROOT/p) for p in CODE}


def parent_for(store,sources,case):
    if not case.get('parent_fields'):return None
    goal=ResearchGoal(query={'names':case['names'],'limit':20},attributes=['medicinal_part'],
        document_fields=case['parent_fields'],research={'mode':case['mode'],'target_count':case['target_count'],
                                                       'monograph_only':case.get('monograph_only',False)})
    with sources() as s:
        parent,_=store.reserve_agent('combined-eval','shared-parent',goal.model_dump(),s.revision,question='共同的零模型父版本')
    parent=ResearchEngine(store,'combined-eval',parent['id'],sources,ResearchFixed()).run()
    if parent['status']!='complete':raise ValueError('Parent preparation failed')
    return parent


def prepare(release,out,suite_path=None):
    check(release);suite=json.loads(Path(suite_path or SUITE).read_text());prepared=[]
    ids=[c['id'] for c in suite['cases']]
    native_ids=suite.get('native_case_ids',[c['id'] for c in suite['cases']])
    if not 1<=len(ids)<=12 or any(not isinstance(i,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',i) for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('Invalid or duplicate case IDs')
    if len(native_ids)!=len(set(native_ids)) or set(native_ids)-set(ids):raise ValueError('Unknown native cases')
    budget=suite.get('max_total_requests',24)
    if type(budget) is not int or not 1<=budget<=132:raise ValueError('Invalid request budget')
    out.mkdir(parents=True,exist_ok=False,mode=0o700)
    save(out/'suite.private.json',suite)
    sources=lambda:ReleaseSources(release)
    with sources() as s:catalog=s.name_catalog()
    for case in suite['cases']:
        parent=parent_for(Workspaces(out/(case['id']+'_parent')),sources,case)
        item={'case':case,'catalog':bounded_catalog(case['question'],catalog,parent),'parent':parent}
        if case['status']=='ready':
            goal=ResearchGoal(query={'names':case['names'],'limit':20},attributes=['medicinal_part'],
                document_fields=case['fields'],research={'mode':case['mode'],'target_count':case['target_count'],
                                                       'monograph_only':case.get('monograph_only',False)})
            with sources() as s:
                pool=s.query(goal.query);inspections={r['source_record_id']:s.inspect(r,case['fields']) for r in pool['records']}
            item['witness']={'pool':pool,'inspections':inspections}
        path=out/(case['id']+'_prepared.private.json');save(path,item)
        prepared.append({'id':case['id'],'sha256':digest(path)})
    for p in CODE:
        dst=out/'frozen_source'/p;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/p,dst)
    protocol={'version':suite['version'],'cases':prepared,'code_sha256':hashes(),
        'suite_sha256':digest(out/'suite.private.json'),'native_case_ids':native_ids,
        'release_sha256':digest(release/'release.json'),'reference_status':suite['reference_status'],
        'strategies':{'R':'local grammar + deterministic executor','G':'one shared model Goal + deterministic executor',
                      'A':'same shared model Goal + native observation executor'},
        'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash',
        'budget':{'total_requests':budget,'goal_requests_per_case':1,'A_requests_per_authorized_case':10,
                  'output_tokens':1200,'max_request_bytes':65536,'timeout_seconds':30,'retries':0},
        'goal_call_shared_between_G_and_A':True,'reference_fields_never_sent_to_model':True,
        'data_sent':'fixed questions, static tools, exact name aliases, parent structural metadata; native execution additionally accepted Goal, bounded candidate states/tool interactions and optional <=3x800-char excerpts; no PDF/DB/history/key body',
        'execution_authorization':{'ready_cases':[c['id'] for c in suite['cases'] if c['status']=='ready' and c['execution_authorized']],
                                   'native_cases':native_ids,'controls_never_executed':True,'sources':'read-only; private local artifacts'},
        'stop':'unknown outcome or HTTP error stops later model stages, never retry or auto-continue',
        'source_quality_claim':'frozen source witnesses and delivery contracts, not independent raw extraction or clinical truth'}
    save(out/'protocol.json',protocol);return protocol


def grade_goal(case,proposal):
    expected={'status':case['status'],'names':case.get('names',[]),'fields':case.get('fields',[]),
              'mode':case.get('mode'),'target_count':case.get('target_count'),'monograph_only':case.get('monograph_only',False)}
    return questions.score(expected,proposal)


def grade_delivery(case,doc,store,witness,parent):
    detail={'delivery_passed':False,'status':doc['status'],'artifact':bool(doc.get('artifact')),'reason':doc.get('error')}
    if not doc.get('artifact') or not doc.get('result'):return detail
    r=doc['result'];validate_result(r);verification=verify(store.root/doc['id']/doc['artifact']['filename'],r)
    rows={x['source_id']:x for x in r['rows']};pool={x['source_record_id']:x for x in witness['pool']['records']}
    allowed={'candidate_value'} if case.get('monograph_only') else {'candidate_value','candidate_scope_value'}
    eligible={sid for sid,x in witness['inspections'].items() if len(x['candidates'])==1 and x['status'] not in ('unmatched','ambiguous','source_mismatch') and
              all(x['fields'].get(f,{}).get('status') in allowed for f in case['fields']) and
              pool[sid]['fields']['medicinal_part']['state']=='present'}
    selected=(set(rows)==set(pool) if case['mode']=='audit_named' else
              set(rows)<=eligible and len(rows)==min(case['target_count'],len(eligible)))
    raw_match=True
    for sid,row in rows.items():
        if sid not in pool:raw_match=False;continue
        if set(row['cells'])!=set(['medicinal_part',*case['fields']]):raw_match=False
        for f in case['fields']:
            expected=witness['inspections'][sid]['fields'].get(f,{})
            actual=row['cells'].get(f,{})
            raw_match &= actual.get('value')==expected.get('value') and actual.get('status')==expected.get('status')
        raw_match &= row['cells'].get('medicinal_part',{}).get('value')==pool[sid]['fields']['medicinal_part']['value']
    preserved=True
    if parent:
        preserved=set(rows)=={row['source_id'] for row in parent['result']['rows']}
        for row in parent['result']['rows']:
            for field,cell in row['cells'].items():preserved &= rows.get(row['source_id'],{}).get('cells',{}).get(field)==cell
        preserved &= digest(store.root/parent['id']/parent['artifact']['filename'])==parent['artifact']['sha256'] and bool(r.get('diff'))
    return {**detail,'delivery_passed':doc['status']==case['expected_status'] and selected and raw_match and preserved,
            'selection_valid':selected,'raw_source_witness_match':raw_match,'parent_preserved':preserved,
            'qualified_total':len(eligible),'returned':len(rows),'verification':verification,
            'decision_steps':r['research']['decision_steps'],'source_reads':r['leaf_reads']}


class Recorder:
    def __init__(self,out,budget):self.out=out;self.budget=budget;self.entries=[];self.stopped=False
    def attach(self,model,stage):
        local=[]
        def request(req):
            if self.stopped or len(self.entries)>=self.budget:raise ValueError('Global request budget exhausted')
            entry={'stage':stage,'number':len(self.entries)+1,'outcome':'unknown','request_bytes':len(req.content)}
            self.entries.append(entry);local.append(entry)
            save(self.out/f"request_{entry['number']:02d}.private.json",{**entry,'request':json.loads(req.content)})
        def response(resp):
            resp.read();entry=local[-1];entry.update(outcome='response',http_status=resp.status_code)
            try:body=resp.json()
            except ValueError:body={'body_sha256':hashlib.sha256(resp.content).hexdigest()}
            entry['tokens']=body.get('usage',{}).get('total_tokens',0)
            save(self.out/f"response_{entry['number']:02d}.private.json",{**entry,'response':body})
        model.http_client.event_hooks['request'].append(request);model.http_client.event_hooks['response'].append(response)
        return local
    def check_stop(self):
        self.stopped |= any(x['outcome']=='unknown' or x.get('http_status',200)>=400 for x in self.entries)


def execute(release,out,model_factory=None):
    protocol=json.loads((out/'protocol.json').read_text())
    if protocol['code_sha256']!=hashes() or protocol['release_sha256']!=digest(release/'release.json'):raise ValueError('Frozen inputs changed')
    if protocol.get('suite_sha256')!=digest(out/'suite.private.json'):raise ValueError('Frozen suite changed')
    for c in protocol['cases']:
        if c['sha256']!=digest(out/(c['id']+'_prepared.private.json')):raise ValueError('Frozen witness changed')
    save(out/'execution_started.json',{'consumed_even_if_interrupted':True})
    if model_factory is None:
        from dotenv import dotenv_values
        conf=dotenv_values(ROOT/'.env')
        for key in ('MODEL','API_KEY','BASE_URL'):
            v=os.getenv('RAG_'+key) or conf.get('RAG_'+key) or conf.get('CHAT_'+key)
            if not v:raise ValueError('Configured model missing')
            os.environ['RAG_'+key]=v
        model_factory=lambda limit:configured_model(max_requests=limit)
    os.environ.update(ENABLE_LANGFUSE='0',LANGFUSE_TRACING_ENABLED='false',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    recorder=Recorder(out,protocol['budget']['total_requests']);records=[];sources=lambda:ReleaseSources(release)
    for meta in protocol['cases']:
        item=json.loads((out/(meta['id']+'_prepared.private.json')).read_text());case=item['case'];q=case['question']
        local=local_proposal(q,item['catalog'],parent=item['parent']);save(out/(case['id']+'_local.json'),local)
        shared=None;shared_requests=[];goal_seconds=0
        if not recorder.stopped:
            goal_started=time.monotonic()
            model=model_factory(1);shared_requests=recorder.attach(model,case['id']+'_goal')
            try:shared=request_proposal(model,q,item['catalog'],parent=item['parent'],authorized=True)
            except GoalResponseRejected as exc:shared={'status':'proposal_rejected','goal':None,'diagnostics':exc.diagnostics}
            except Exception as exc:shared={'status':'dispatch_unknown','goal':None,'exception_type':type(exc).__name__};recorder.stopped=True
            finally:model.http_client.close();recorder.check_stop()
            goal_seconds=time.monotonic()-goal_started
            save(out/(case['id']+'_shared_model_goal.json'),shared)
        for strategy,proposal in [('R',local),('G',shared),('A',shared)]:
            folder=out/(case['id']+'_'+strategy);folder.mkdir();started=time.monotonic()
            if strategy=='A' and case['id'] not in protocol['native_case_ids']:
                record={'id':case['id'],'strategy':strategy,'status':'not_scheduled','passed':None,'execution_started':False}
                records.append(record);save(folder/'score.json',record);continue
            if proposal is None:
                record={'id':case['id'],'strategy':strategy,'status':'not_run_after_provider_error','passed':False,'execution_started':False}
            else:
                graded=grade_goal(case,proposal);record={'id':case['id'],'strategy':strategy,'goal':graded,'passed':graded['passed'],'execution_started':False}
                if proposal['status']=='ready' and case['execution_authorized'] and not (strategy=='A' and recorder.stopped):
                    store=Workspaces(folder/'workspaces');parent=parent_for(store,sources,case)
                    with sources() as s:
                        doc,_=store.reserve_agent('combined-eval','task',proposal['goal'],s.revision,question=q,
                            parent_id=parent['id'] if parent else None,expected_revision=parent['revision'] if parent else None)
                    model=None;execution_requests=[]
                    if strategy=='A':
                        model=model_factory(10);execution_requests=recorder.attach(model,case['id']+'_A');driver=ResearchDriver(model,authorized=True)
                    else:driver=ResearchFixed()
                    try:
                        result=ResearchEngine(store,'combined-eval',doc['id'],sources,driver).run()
                        save(folder/'result.private.json',result)
                        delivery=grade_delivery(case,result,store,item['witness'],parent)
                    finally:
                        if model:model.http_client.close();recorder.check_stop()
                    record.update(delivery=delivery,passed=graded['passed'] and delivery['delivery_passed'],execution_started=True,
                                  execution_requests=len(execution_requests))
                elif case['status']=='ready':record['passed']=False
                execution_seconds=time.monotonic()-started
                record.update(goal_requests_attributed=0 if strategy=='R' else len(shared_requests),
                              goal_requests_shared=strategy in ('G','A'),
                              goal_tokens_attributed=0 if strategy=='R' else sum(e.get('tokens',0) for e in shared_requests),
                              execution_tokens=sum(e.get('tokens',0) for e in recorder.entries if e['stage']==case['id']+'_A') if strategy=='A' else 0,
                              goal_seconds=round(0 if strategy=='R' else goal_seconds,3),execution_seconds=round(execution_seconds,3),
                              duration_seconds=round(execution_seconds+(0 if strategy=='R' else goal_seconds),3))
            records.append(record);save(folder/'score.json',record);print(json.dumps(record,ensure_ascii=False),flush=True)
    scheduled=lambda s:[r for r in records if r['strategy']==s and r.get('status')!='not_scheduled']
    report={'version':protocol['version'],'records':records,'actual_requests':len(recorder.entries),
            'total_tokens':sum(r.get('tokens',0) for r in recorder.entries),'provider_stopped':recorder.stopped,
            'by_strategy':{s:{'passed':sum(bool(r['passed']) for r in scheduled(s)),'total':len(scheduled(s)),
                              'wrong_goals':sum(r.get('goal',{}).get('wrong_ready_goal',False) for r in records if r['strategy']==s)} for s in ('R','G','A')},
            'request_accounting':'G/A share goal requests; attributed goal counts must not be summed as actual provider calls',
            'generalization_proven':False,'native_necessity_proven':False,'reference_status':protocol['reference_status']}
    save(out/'report.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--release',type=Path,default=ROOT/'var/data_releases/r1_20261030');p.add_argument('--execute',action='store_true')
    p.add_argument('--suite',type=Path,help='Freeze an alternate authored suite; execution uses its frozen copy')
    args=p.parse_args();r=execute(args.release,args.out) if args.execute else prepare(args.release,args.out,args.suite)
    print(json.dumps({k:v for k,v in r.items() if k in ('version','budget','actual_requests','by_strategy','total_tokens')},ensure_ascii=False))
