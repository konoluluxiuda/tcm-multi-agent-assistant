"""Freeze then compare full/compact understanding on authored synthetic questions.

One request per profile/case, no retries, no real corpus, no independent accuracy claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from scripts.dataops_source_manifest import manifest
from scripts.eval_research_combined import Recorder,save

ROOT=Path(__file__).resolve().parents[1]
CASES=[
 {'id':'S01','question':'刚接触这个平台，我能拿它做些什么事情？','topic':'capabilities'},
 {'id':'S02','question':'这两份数据各记了啥，分别适合干什么？','topic':'dataset_info'},
 {'id':'S03','question':'Alpha与Beta各放一行，主要用途和存放环境各占一列，没记载就空着，给一张附出处的表格',
  'fields':['功能与主治','贮藏']},
 {'id':'S04','question':'上一份资料保留原有对象和所有列，再添一列存放环境，出处照旧',
  'parent':True,'fields':['功能与主治','贮藏']},
]


def inputs():
 return {'runtime_tree':manifest()['source_tree_sha256'],
         'script':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'fixture':hashlib.sha256((ROOT/'tests/dataops_fixture.py').read_bytes()).hexdigest(),
         'recorder':hashlib.sha256((ROOT/'scripts/eval_research_combined.py').read_bytes()).hexdigest()}


def prepare(out,*,compact_only=False):
 out.mkdir(parents=True,exist_ok=False,mode=0o700)
 protocol={'version':'understanding-compact-v1','inputs':inputs(),'cases':CASES,
   'profiles':['compact_v2'] if compact_only else ['full_v1','compact_v2'],
   'max_requests':4 if compact_only else 8,'per_combination':1,'max_output_tokens':1200,
   'max_request_bytes':65536,'timeout_seconds':30,'retries':0,'endpoint':'https://api.deepseek.com',
   'model':'deepseek-flash','data':'authored synthetic questions, static schemas, synthetic aliases and parent structural metadata only',
   'real_corpus_sent':False,'reference_sent':False,'trace_export':False,'stop_on_provider_error':True,
   'independent_user_accuracy':False,'old_scores_changed':False}
 save(out/'protocol.json',protocol);return protocol


def parent_for(store,sources):
 from tcm.dataops.research_contracts import ResearchGoal
 from tcm.dataops.research_graph import ResearchEngine
 from tcm.dataops.research_model import ResearchFixed
 goal=ResearchGoal(query={'names':['Alpha','Beta'],'limit':20},attributes=['medicinal_part'],
    document_fields=['功能与主治'],research={'mode':'audit_named','target_count':2})
 with sources() as s:doc,_=store.reserve_agent('compact-eval','parent',goal.model_dump(),s.revision,question='合成父版本')
 parent=ResearchEngine(store,'compact-eval',doc['id'],sources,ResearchFixed()).run()
 if parent['status']!='complete':raise ValueError('Parent preparation failed')
 return parent


def execute(out):
 protocol=json.loads((out/'protocol.json').read_text())
 if protocol['inputs']!=inputs():raise ValueError('Frozen inputs changed')
 save(out/'execution_started.json',{'consumed_even_if_interrupted':True})
 from dotenv import dotenv_values
 configured=dotenv_values(ROOT/'.env')
 for key in ('MODEL','API_KEY','BASE_URL'):
  value=os.getenv('RAG_'+key) or configured.get('RAG_'+key) or configured.get('CHAT_'+key)
  if not value:raise ValueError('Missing model configuration')
  os.environ['RAG_'+key]=value
 from tests.dataops_fixture import make_sources
 from tcm.dataops.research_question import bounded_catalog,request_proposal,understanding_input
 from tcm.dataops.research_model import configured_model,ResearchFixed
 from tcm.dataops.research_graph import ResearchEngine
 from tcm.dataops.workspace import Workspaces
 from tcm.dataops.artifacts import verify
 _,sources=make_sources(out/'sources')
 with sources() as s:all_names=s.name_catalog()
 recorder=Recorder(out,protocol['max_requests']);records=[]
 env={'ENABLE_LANGFUSE':'0','LANGFUSE_TRACING_ENABLED':'false','LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false'}
 with patch.dict(os.environ,env):
  for index,case in enumerate(protocol['cases']):
   profiles=protocol['profiles'] if index%2==0 else list(reversed(protocol['profiles']))
   for profile in profiles:
    record={'id':case['id'],'profile':profile,'passed':False,'provider_requests':0}
    if recorder.stopped:
     records.append({**record,'status':'not_run_after_provider_error'});continue
    folder=out/(case['id']+'_'+profile);folder.mkdir()
    store=Workspaces(folder/'workspaces');parent=parent_for(store,sources) if case.get('parent') else None
    q=case['question'];catalog=bounded_catalog(q,all_names,parent)
    payload,system,tools=understanding_input(q,catalog,parent=parent,allow_queries=True,schema_profile=profile)
    record['input_utf8_bytes']=len(json.dumps({'payload':payload,'system':system,'tools':tools},ensure_ascii=False).encode())
    record['exposed_tools']=[t['function']['name'] for t in tools]
    model=configured_model(max_requests=1);entries=recorder.attach(model,case['id']+'_'+profile)
    try:
     proposal=request_proposal(model,q,catalog,parent=parent,authorized=True,allow_queries=True,schema_profile=profile)
     save(folder/'proposal.private.json',proposal)
     if 'topic' in case:
      passed=(proposal['status']=='query' and proposal['route']['request'].get('topic')==case['topic'])
      if passed and case['topic']=='dataset_info':passed=proposal['route']['request'].get('datasets',[]) in ([],['etcm','pharmacopoeia'],['pharmacopoeia','etcm'])
     else:
      goal=proposal.get('goal') or {}
      passed=(proposal['status']=='ready' and set(goal.get('query',{}).get('names',[]))=={'Alpha','Beta'}
       and set(goal.get('document_fields',[]))==set(case['fields']) and goal.get('attributes')==['medicinal_part']
       and goal.get('research',{}).get('mode')=='audit_named' and goal['research']['target_count']==2)
      if passed:
       with sources() as s:doc,_=store.reserve_agent('compact-eval','child',goal,s.revision,question=q,
          parent_id=parent['id'] if parent else None,expected_revision=parent['revision'] if parent else None)
       doc=ResearchEngine(store,'compact-eval',doc['id'],sources,ResearchFixed()).run()
       save(folder/'result.private.json',doc)
       path=store.root/doc['id']/doc['artifact']['filename'];verification=verify(path,doc['result'])
       passed=doc['status']=='complete' and len(doc['result']['rows'])==2 and verification['status']=='passed'
       record['delivery_verification']=verification
       if parent:
        old=store.root/parent['id']/parent['artifact']['filename']
        rows={r['source_id']:r for r in doc['result']['rows']}
        preserved=(hashlib.sha256(old.read_bytes()).hexdigest()==parent['artifact']['sha256'] and
          set(rows)=={r['source_id'] for r in parent['result']['rows']} and
          all(rows[r['source_id']]['cells'][f]==v for r in parent['result']['rows'] for f,v in r['cells'].items()))
        record['parent_preserved']=preserved;passed=passed and preserved
     record.update(status='passed' if passed else 'failed',passed=bool(passed),proposal_status=proposal['status'],usage=proposal.get('usage',{}))
    except Exception as exc:
     record.update(status='failed',error_type=type(exc).__name__)
    finally:
     model.http_client.close();recorder.check_stop()
     record.update(provider_requests=len(entries),tokens=sum(e.get('tokens',0) for e in entries))
     records.append(record);save(folder/'score.json',record)
 summary={profile:{'passed':sum(r['passed'] for r in records if r['profile']==profile),'total':sum(r['profile']==profile for r in records),
   'tokens':sum(r.get('tokens',0) for r in records if r['profile']==profile)} for profile in protocol['profiles']}
 report={'version':protocol['version'],'status':'passed' if all(r['passed'] for r in records) else 'failed',
   'records':records,'profiles':summary,'actual_requests':len(recorder.entries),'requests':recorder.entries,
   'tokens':sum(e.get('tokens',0) for e in recorder.entries),'provider_stopped':recorder.stopped,
   'real_corpus_sent':False,'independent_user_accuracy':False,'old_scores_changed':False,
   'comparison_scope':'authored synthetic integration and provider usage; no broad accuracy or native-loop necessity claim'}
 save(out/'report.json',report);return report


if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,type=Path);parser.add_argument('--execute',action='store_true');parser.add_argument('--compact-only',action='store_true');args=parser.parse_args()
 report=execute(args.out.resolve()) if args.execute else prepare(args.out.resolve(),compact_only=args.compact_only)
 print(json.dumps({k:report[k] for k in ('version','status','actual_requests','tokens','profiles','max_requests') if k in report},ensure_ascii=False))
