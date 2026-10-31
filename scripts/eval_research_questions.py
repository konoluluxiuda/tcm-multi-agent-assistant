"""Freeze new question-level references, then compare grammar vs one proposal.

No provider-generated goal is executed during this comparison. Source values and
the scoring references are never in the model context. Every request has a
separate immutable started/request/response receipt; a run cannot be repeated.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from tcm.dataops.research_question import bounded_catalog,local_proposal,request_proposal,draft_context
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_model import ResearchSynthetic,configured_model
from tcm.dataops.research_sources import ReleaseSources
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.goal_model import GoalResponseRejected
from tcm.dataops.workspace import Workspaces
from tcm.dataops.data_access import canonical
from tcm.mineru_pharmacopoeia import digest

ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'eval/research_question_v1.json'
CODE=['scripts/eval_research_questions.py','eval/research_question_v1.json','tcm/request_journal.py','tcm/tracing.py','tcm/rag_retrieval.py','tcm/mineru_retrieval.py']+[
    'tcm/dataops/'+name+'.py' for name in ('research_question','research_contracts','goal_proposal','goal_model',
        'research_sources','research_context','research_graph','research_model','contracts','data_access','workspace','baseline','artifacts','graph','model')]


def save(path,value):
    with path.open('x') as f:f.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def hashes():return {p:digest(ROOT/p) for p in CODE}


def score(case,proposal):
    accepted=proposal.get('status')=='ready';wanted=case['status']=='ready'
    fields_correct=False;scope_correct=False
    if accepted and proposal.get('goal'):
        goal=ResearchGoal.model_validate(proposal['goal'])
        fields_correct=(set(goal.document_fields)==set(case.get('fields',[])) and goal.attributes==['medicinal_part'])
        scope_correct=(set(goal.query.names)==set(case.get('names',[])) and not goal.query.source_ids and not goal.query.filters and
            goal.query.limit==20 and goal.research.mode==case.get('mode') and not goal.research.monograph_only and
            goal.research.target_count==case.get('target_count',len(case.get('names',[]))))
    return {'passed':proposal.get('status')==case['status'] and (not wanted or fields_correct and scope_correct),
            'status':proposal.get('status'),'fields_correct':fields_correct if wanted else None,
            'scope_correct':scope_correct if wanted else None,'unsafe_acceptance':accepted and not wanted,
            'wrong_ready_goal':accepted and wanted and not (fields_correct and scope_correct)}


def prepare(release,out):
    out.mkdir(parents=True,exist_ok=False);prepared=[]
    with ReleaseSources(release) as sources:
        catalog=sources.name_catalog()
    for case in json.loads(SUITE.read_text())['cases']:
        parent=None
        if case.get('parent'):
            p=case['parent'];goal=ResearchGoal(query={'names':p['names'],'limit':20},attributes=['medicinal_part'],
                document_fields=p['fields'],research={'mode':'audit_named','target_count':len(p['names'])})
            store=Workspaces(out/'shared_parent');owner='question-eval'
            with ReleaseSources(release) as sources:
                parent,_=store.reserve_agent(owner,case['id']+'-parent',goal.model_dump(),sources.revision,question='共同的本地父版本')
            parent=ResearchEngine(store,owner,parent['id'],lambda:ReleaseSources(release),ResearchSynthetic()).run()
            if parent['status']!='complete':raise ValueError('Shared parent not ready')
        selected=bounded_catalog(case['question'],catalog,parent)
        context=draft_context(case['question'],selected,parent)
        item={'case':case,'catalog':selected,'parent':parent,'model_context':context}
        path=out/(case['id']+'_prepared.private.json');save(path,item)
        prepared.append({'id':case['id'],'prepared_sha256':digest(path),'context_bytes':len(canonical(context).encode())})
    for p in CODE:
        target=out/'frozen_source'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/p,target)
    protocol={'version':'research-question-live-v1','cases':prepared,'code_sha256':hashes(),
        'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash',
        'release_content_id':json.loads((release/'release.json').read_text())['content_id'],
        'release_sha256':digest(release/'release.json'),
        'max_requests':8,'per_question_max_requests':1,'output_tokens':1200,'max_request_bytes':65536,
        'timeout_seconds':30,'retries':0,
        'data_sent':'fixed authored question + static goal schema + exact mentioned catalogue aliases + parent field/selection metadata; no source values, PDF/DB, user message history or key body',
        'semantic_references_sent_to_model':False,'execute_generated_goals':False,
        'reference_status':json.loads(SUITE.read_text())['reference_status'],
        'stop':'unknown/HTTP error stops subsequent model cases; malformed response is retained, no correction or retry'}
    save(out/'protocol.json',protocol);return protocol


def execute(release,out,model_factory=None):
    protocol=json.loads((out/'protocol.json').read_text())
    if protocol['code_sha256']!=hashes() or protocol['release_sha256']!=digest(release/'release.json'):raise ValueError('Frozen inputs changed')
    for case in protocol['cases']:
        if case['prepared_sha256']!=digest(out/(case['id']+'_prepared.private.json')):raise ValueError('Prepared case changed')
    save(out/'execution_started.json',{'status':'consumed_even_if_interrupted'})
    if model_factory is None:
        from dotenv import dotenv_values
        conf=dotenv_values(ROOT/'.env')
        for k in ('MODEL','API_KEY','BASE_URL'):
            v=os.getenv('RAG_'+k) or conf.get('RAG_'+k) or conf.get('CHAT_'+k)
            if not v:raise ValueError('Missing configured model')
            os.environ['RAG_'+k]=v
        os.environ.update(ENABLE_LANGFUSE='0',LANGFUSE_TRACING_ENABLED='false',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
        model_factory=lambda:configured_model(max_requests=1)
    records=[];total=0;stop=False
    for meta in protocol['cases']:
        item=json.loads((out/(meta['id']+'_prepared.private.json')).read_text());case=item['case'];q=case['question'];catalog=item['catalog'];parent=item['parent']
        local=local_proposal(q,catalog,parent=parent);save(out/(case['id']+'_local.json'),local)
        records.append({'id':case['id'],'strategy':'rules',**score(case,local),'requests':0})
        if stop:
            records.append({'id':case['id'],'strategy':'model','status':'not_run','passed':False,'requests':0});continue
        model=model_factory();exchanges=[]
        def requested(req):
            nonlocal total
            if total>=8 or exchanges:raise ValueError('Request budget exceeded')
            total+=1;exchanges.append({'body_sha256':hashlib.sha256(req.content).hexdigest(),'bytes':len(req.content),'outcome':'unknown'})
            save(out/(case['id']+'_request.private.json'),{'request':json.loads(req.content),**exchanges[-1]})
        def responded(resp):
            resp.read();exchanges[-1].update(outcome='response',http_status=resp.status_code)
            save(out/(case['id']+'_response.private.json'),{'response':resp.json(),**exchanges[-1]})
        model.http_client.event_hooks['request'].append(requested);model.http_client.event_hooks['response'].append(responded)
        started=time.monotonic()
        try:proposal=request_proposal(model,q,catalog,parent=parent,authorized=True)
        except GoalResponseRejected as exc:
            proposal={'status':'proposal_rejected','diagnostics':exc.diagnostics,'external_model_calls':len(exchanges)}
        except Exception as exc:
            proposal={'status':'dispatch_unknown','exception_type':type(exc).__name__,'external_model_calls':len(exchanges)};stop=True
        finally:model.http_client.close()
        if any(e.get('http_status',200)>=400 or e['outcome']=='unknown' for e in exchanges):stop=True
        save(out/(case['id']+'_model.json'),proposal)
        record={'id':case['id'],'strategy':'model',**score(case,proposal),'requests':len(exchanges),
                'tokens':proposal.get('usage',{}).get('total_tokens',0),'duration_seconds':round(time.monotonic()-started,3)}
        records.append(record);save(out/(case['id']+'_score.json'),records[-2:]);print(json.dumps(record,ensure_ascii=False),flush=True)
    report={'version':protocol['version'],'records':records,'actual_requests':total,'reference_status':protocol['reference_status'],
        'goal_execution':False,'generalization_proven':False,
        'summary':{s:{'passed':sum(r['passed'] for r in records if r['strategy']==s),'total':8,
                       'wrong_ready':sum(r.get('wrong_ready_goal',False) for r in records if r['strategy']==s),
                       'unsafe_acceptance':sum(r.get('unsafe_acceptance',False) for r in records if r['strategy']==s)} for s in ('rules','model')}}
    save(out/'report.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--release',type=Path,default=ROOT/'var/data_releases/r1_20261030');p.add_argument('--execute',action='store_true')
    args=p.parse_args();r=execute(args.release,args.out) if args.execute else prepare(args.release,args.out)
    print(json.dumps({k:v for k,v in r.items() if k in ('version','summary','actual_requests','max_requests')},ensure_ascii=False))
