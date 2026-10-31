"""Frozen F / one-plan P / observation A comparison; never overwrite or retry."""
import argparse
from datetime import datetime,timezone
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time

from tcm.data_release import check
from tcm.mineru_pharmacopoeia import digest
from tcm.rag_benchmark import read,write
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchFixed,ResearchDriver,ResearchPlan,configured_model
from tcm.dataops.research_sources import ReleaseSources
from tcm.dataops.workspace import Workspaces
from tcm.dataops.artifacts import verify
from tcm.dataops.baseline import validate_result

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'eval/dataops_research_v1.json'
CODE=['scripts/eval_dataops_research.py','eval/dataops_research_v1.json','tcm/rag_retrieval.py',
      'tcm/mineru_retrieval.py','tcm/mineru_guards.py','tcm/tracing.py']+[
      'tcm/dataops/'+n+'.py' for n in ('research_contracts','research_graph','research_model','research_sources','research_context',
                                      'graph','model','data_access','baseline','artifacts','contracts','workspace')]
def hashes():return {p:digest(ROOT/p) for p in CODE}

def prepare(release,out,continue_from=None):
    integrity=check(release);out.mkdir(parents=True,exist_ok=False)
    # References never enter model context. Completeness witnesses are materialized
    # before any provider request, with exact original values/provenance.
    witnesses={}
    with ReleaseSources(release) as sources:
        for case in read(SUITE)['cases']:
            g=ResearchGoal.model_validate(case['goal']);pool=sources.query(g.query)
            witnesses[case['id']]={'source_revision':sources.revision,'pool':pool,
                'inspections':{r['source_record_id']:sources.inspect(r,g.document_fields) for r in pool['records']}}
    write(out/'references.private.json',witnesses)
    for p in CODE:
        target=out/'frozen_source'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/p,target)
    protocol={'version':'research-selection-live-v1','prepared_at':datetime.now(timezone.utc).isoformat(),
        'code_sha256':hashes(),'release_content_id':integrity['content_id'],'release_sha256':digest(release/'release.json'),
        'references_sha256':digest(out/'references.private.json'),
        'strategies':{'F':'deterministic field-completeness scan; same tools, no provider',
                      'P':'shared local listing, one model fixed script; no observation feedback',
                      'A':'one native tool action per decision with observation feedback'},
        'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash',
        'budget':{'cases':2,'F_requests':0,'P_requests_per_case':1,'A_requests_per_case':10,'total_requests':22,
                  'max_output_tokens':1200,'max_request_bytes':65536,'request_timeout_seconds':30,'retries':0},
        'same_limits':{'decision_steps':10,'tool_actions':24,'source_reads':200,'context_bytes':16000},
        'data_sent':'current frozen question, explicit goal, bounded candidate metadata, static tools and latest bounded observation; evidence reread <=3x800 chars; no PDF/DB/history/key body',
        'shared_preparation':'source verification and scorer witnesses; per-strategy S02 parent created by identical zero-model F',
        'reference_status':read(SUITE)['reference_status'],
        'stop':'HTTP error or unknown outcome stops subsequent model runs; no automatic retry/correction; preserve every result'}
    if continue_from is not None:
        prior=read(continue_from/'protocol.json')
        for name,sha in prior['code_sha256'].items():
            if name!='scripts/eval_dataops_research.py' and protocol['code_sha256'].get(name)!=sha:
                raise ValueError('A continuation cannot change the engine, tools or suite')
        if prior['release_content_id']!=protocol['release_content_id']:raise ValueError('Continuation data changed')
        reused={run:digest(continue_from/run/'score.json') for run in ('S01_F','S01_P')}
        consumed=sum(read(continue_from/run/'score.json')['requests'] for run in reused)
        if consumed!=1:raise ValueError('Unexpected prior request count')
        protocol['continuation']={'prior_root':str(continue_from.resolve()),
            'prior_protocol_sha256':digest(continue_from/'protocol.json'),'reused_scores':reused,
            'prior_requests':consumed,'reason':'local immutable-log write collision after S01_P HTTP200; no replay',
            'requires_explicit_new_authorization':True,'previous_run_retried':False}
        protocol['budget']['total_requests']=21
    write(out/'protocol.json',protocol);return protocol

def score(case,doc,store,witness,parent=None):
    result=doc.get('result');artifact=doc.get('artifact')
    detail={'status':doc['status'],'artifact':bool(artifact),'passed':False,'reason':doc.get('error')}
    if not result or not artifact:return detail
    validate_result(result);receipt=verify(store.root/doc['id']/artifact['filename'],result)
    rows={r['source_id']:r for r in result['rows']}
    pool={r['source_record_id']:r for r in witness['pool']['records']}
    wanted={sid for sid,x in witness['inspections'].items() if len(x['candidates'])==1 and
            x['status'] not in ('unmatched','source_mismatch','ambiguous') and all(
                x['fields'].get(f,{}).get('status') in ('candidate_value','candidate_scope_value') for f in case['goal']['document_fields']) and
            all(pool[sid]['fields'][f]['state']=='present' for f in case['goal']['attributes'])}
    raw_match=True
    for sid,row in rows.items():
        if sid not in pool:raw_match=False;continue
        for f in case['goal']['document_fields']:
            expected=witness['inspections'][sid]['fields'].get(f,{})
            raw_match &= row['cells'][f]['value']==expected.get('value') and row['cells'][f]['status']==expected.get('status')
        for f in case['goal']['attributes']:
            raw_match &= row['cells'][f]['value']==pool[sid]['fields'][f]['value']
    preserved=True
    if parent:
        preserved=set(rows)=={r['source_id'] for r in parent['result']['rows']}
        for old in parent['result']['rows']:
            if old['source_id'] not in rows:preserved=False;continue
            for f,c in old['cells'].items():preserved &= rows[old['source_id']]['cells'][f]==c
        preserved &= bool(result.get('diff'))
        preserved &= digest(store.root/parent['id']/parent['artifact']['filename'])==parent['artifact']['sha256']
        valid_selection=preserved and all(rows[sid]['cells']['注意']['value'] is None for sid in rows)
    else:
        valid_selection=len(rows)==case['goal']['research']['target_count'] and set(rows)<=wanted
    detail.update(passed=doc['status']==case['expected_status'] and valid_selection and raw_match,
                  selection_valid=valid_selection,parent_preserved=preserved,verification=receipt,
                  frozen_raw_source_match=raw_match,
                  qualified=result['research']['qualified_count'],decision_steps=result['research']['decision_steps'],
                  source_reads=result['leaf_reads'],scope_and_exact_citations_valid=True,
                  medical_identity_verified=False)
    return detail

def execute(release,out,*,langfuse=False):
    p=read(out/'protocol.json')
    if p['code_sha256']!=hashes() or p['release_sha256']!=digest(release/'release.json') or p['references_sha256']!=digest(out/'references.private.json'):
        raise ValueError('Frozen code, source or scorer changed')
    check(release)
    prior_root=Path(p['continuation']['prior_root']) if p.get('continuation') else None
    if prior_root:
        prior=p['continuation']
        if digest(prior_root/'protocol.json')!=prior['prior_protocol_sha256']:raise ValueError('Prior protocol changed')
        if any(digest(prior_root/run/'score.json')!=sha for run,sha in prior['reused_scores'].items()):raise ValueError('Prior scores changed')
    # Exclusive marker means a crashed or timed-out run cannot silently reuse budget.
    with (out/'execution_started.json').open('x') as f:json.dump({'started_at':datetime.now(timezone.utc).isoformat(),'consumed_even_if_interrupted':True},f)
    from dotenv import dotenv_values
    conf=dotenv_values(ROOT/'.env')
    for key in ('MODEL','API_KEY','BASE_URL'):
        value=os.getenv('RAG_'+key) or conf.get('RAG_'+key) or conf.get('CHAT_'+key)
        if not value:raise ValueError('Configured research model missing')
        os.environ['RAG_'+key]=value
    os.environ.update(ENABLE_LANGFUSE='0',LANGFUSE_TRACING_ENABLED='false',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    if langfuse:
        lf=dotenv_values(ROOT/'var/langfuse_local/app.env')
        for key in ('LANGFUSE_BASE_URL','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','TCM_TRACE_CONTENT'):
            if lf.get(key):os.environ[key]=lf[key]
        os.environ.update(ENABLE_LANGFUSE='1',LANGFUSE_TRACING_ENABLED='true')
    records=[];stopped=False;requests_total=0
    for case in read(SUITE)['cases']:
        for strategy in ('F','P','A'):
            run_id=case['id']+'_'+strategy;folder=out/run_id;folder.mkdir()
            if prior_root and run_id in p['continuation']['reused_scores']:
                record=read(prior_root/run_id/'score.json')
                record.update(reused_from_prior=True,quality_graded=run_id!='S01_P')
                write(folder/'reused_score.json',record);records.append(record)
                continue
            if stopped and strategy!='F':
                records.append({'id':run_id,'status':'not_run_after_provider_error','passed':False,'requests':0});continue
            started=time.monotonic();store=Workspaces(folder/'workspaces');owner='frozen-eval';goal=ResearchGoal.model_validate(case['goal']);parent=None
            sources=lambda:ReleaseSources(release)
            if case.get('parent_fields'):
                old=goal.model_copy(update={'document_fields':case['parent_fields']})
                with sources() as s:parent,_=store.reserve_agent(owner,'common-parent',old.model_dump(),s.revision,question='共同的零模型父版本')
                parent=ResearchEngine(store,owner,parent['id'],sources,ResearchFixed()).run()
                if parent['status']!='complete':raise ValueError('Shared parent preparation failed')
                write(folder/'parent.json',parent)
            with sources() as s:
                doc,_=store.reserve_agent(owner,'frozen-task',goal.model_dump(),s.revision,question=case['question'],
                         parent_id=parent['id'] if parent else None,expected_revision=parent['revision'] if parent else None)
            exchanges=[];model=None
            if strategy=='F':driver=ResearchFixed()
            else:
                model=configured_model(max_requests=1 if strategy=='P' else 10)
                def requested(req):
                    nonlocal requests_total
                    if requests_total>=p['budget']['total_requests']:raise ValueError('Global budget exceeded')
                    requests_total+=1;exchanges.append({'request':json.loads(req.content),'request_bytes':len(req.content),'outcome':'unknown'})
                    write(folder/f'request_{len(exchanges):02d}.private.json',exchanges[-1])
                def responded(resp):
                    resp.read();exchanges[-1].update(http_status=resp.status_code,outcome='response',response=resp.json())
                    write(folder/f'response_{len(exchanges):02d}.private.json',exchanges[-1])
                model.http_client.event_hooks['request'].append(requested);model.http_client.event_hooks['response'].append(responded)
                driver=ResearchPlan(model.bind(response_format={'type':'json_object'})) if strategy=='P' else ResearchDriver(model,authorized=True)
            try:
                from tcm.tracing import observation
                with observation('research.selection.eval',metadata={'case_id':case['id'],'strategy':strategy}) as trace:
                    result=ResearchEngine(store,owner,doc['id'],sources,driver).run()
                    trace.update(output={'status':result['status'],'artifact':bool(result.get('artifact'))})
                write(folder/'result.json',result)
                graded=score(case,result,store,read(out/'references.private.json')[case['id']],parent)
            finally:
                if model:model.http_client.close()
            if any(e.get('http_status',200)>=400 or e['outcome']=='unknown' for e in exchanges):stopped=True
            tokens=sum(e.get('response',{}).get('usage',{}).get('total_tokens',0) for e in exchanges)
            record={'id':run_id,'strategy':strategy,**graded,'requests':len(exchanges),'tokens':tokens,
                    'duration_seconds':round(time.monotonic()-started,3),
                    'provider_responses':[e.get('http_status') for e in exchanges]}
            records.append(record);write(folder/'score.json',record);write(out/('progress_'+run_id+'.json'),records)
            print(json.dumps(record,ensure_ascii=False),flush=True)
    report={'version':p['version'],'records':records,'requests_total':requests_total,
            'prior_requests':p.get('continuation',{}).get('prior_requests',0),
            'combined_requests_total':requests_total+p.get('continuation',{}).get('prior_requests',0),
            'by_strategy':{s:{'passed':sum(r['passed'] for r in records if r.get('strategy')==s),
                              'total':2,'requests':sum(r['requests'] for r in records if r.get('strategy')==s)} for s in ('F','P','A')},
            'reference_status':p['reference_status'],'universal_model_benefit_proven':False}
    write(out/'report.json',report)
    if langfuse:
        from tcm.tracing import flush
        flush()
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,default=ROOT/'var/data_releases/r1_20261030')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--with-langfuse',action='store_true')
    parser.add_argument('--continue-from',type=Path,help='Prepare a separately authorized continuation, never replay S01_F/P')
    args=parser.parse_args()
    result=execute(args.release,args.out,langfuse=args.with_langfuse) if args.execute else prepare(args.release,args.out,args.continue_from)
    print(json.dumps({k:v for k,v in result.items() if k in ('version','budget','by_strategy','requests_total')},ensure_ascii=False))
if __name__=='__main__':main()
